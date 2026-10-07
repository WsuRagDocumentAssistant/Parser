from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from .headings import HeadingBuilder
from .models import Block, Cell, Document, ImageBlock, TableBlock, TextBlock
from .package import HwpxParseError, Package, ParseLimits, attrs, child, record, tag
from .pages import PageResolver


def integer(value, label: str, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError) as exc:
        raise HwpxParseError(f"Invalid integer for {label}: {value!r}") from exc


class HwpxParser:
    """Reusable configuration; all per-document mutable state lives in a fresh reader."""

    def __init__(
        self, page_mode: str = "auto", style_levels: dict[str, int] | None = None,
        outline_level_base: int = 0, heading_scopes: tuple[str, ...] = ("body",),
        style_scopes: dict[str, list[str]] | None = None, join_numbered_heading_cells: bool = False,
        limits: ParseLimits | None = None,
        auto_headings: bool | None = None,
    ) -> None:
        if page_mode not in {"auto", "layout", "structural"}:
            raise ValueError("page_mode must be 'auto', 'layout' or 'structural'; no office renderer is used")
        self.page_mode = page_mode
        self.style_levels = dict(style_levels or {})
        self.auto_headings = style_levels is None if auto_headings is None else auto_headings
        self.outline_level_base = outline_level_base
        self.heading_scopes = tuple(heading_scopes)
        self.style_scopes = style_scopes or {}
        self.join_numbered_heading_cells = join_numbered_heading_cells
        self.limits = limits or ParseLimits()
        self.heading_builder()

    def heading_builder(self) -> HeadingBuilder:
        return HeadingBuilder(self.style_levels, self.outline_level_base, self.heading_scopes,
                              self.style_scopes, self.join_numbered_heading_cells,
                              auto_detect=self.auto_headings)

    def parse(self, path: str | Path) -> Document:
        source = Path(path)
        with source.open("rb") as stream:
            data = stream.read(self.limits.max_file_bytes + 1)
        if len(data) > self.limits.max_file_bytes:
            raise HwpxParseError("Input file exceeds size limit")
        package = Package(data, self.limits)
        with package.zip:
            reader = _Reader(package, source.name, hashlib.sha256(data).hexdigest())
            document = reader.read()
            self.heading_builder().build(document)
            PageResolver(self.page_mode).resolve(document)
            package.check()
            return document


class _Reader:
    def __init__(self, package: Package, filename: str, digest: str):
        self.package = package
        self.document = Document({"filename": filename, "sha256": digest, "parser_version": "0.1.0"})
        self.styles = {}
        self.para_properties = {}
        self.char_properties = {}
        self.assets = {}
        self.file = ""
        self.section_index = 0
        self.section = {}
        self.warned = set()

    def diagnostic(self, code, source=None, severity="warning", **details):
        key = (code, repr(source), repr(details))
        if key not in self.warned:
            self.warned.add(key)
            self.document.diagnostics.append(dict(severity=severity, code=code, source=source, **details))

    def read(self) -> Document:
        package = self.package
        hpf_path = "Contents/content.hpf"
        if "META-INF/container.xml" in package.entries:
            container = package.xml("META-INF/container.xml")
            roots = [e.get("full-path") for e in container.iter() if tag(e) == "rootfile" and e.get("full-path")]
            if not roots:
                raise HwpxParseError("container.xml has no supported rootfile")
            hpf_path = next((name for name in roots if name.endswith(".hpf")), roots[0])
            hpf_path = package.resolve(hpf_path, "META-INF/container.xml")
        hpf = package.xml(hpf_path)
        if tag(hpf) != "package":
            raise HwpxParseError("Unsupported package XML namespace/root")
        manifest = child(hpf, "manifest")
        spine = child(hpf, "spine")
        if manifest is None or spine is None:
            raise HwpxParseError("Package requires manifest and spine")
        entries = {}
        paths = {}
        for item in manifest:
            if tag(item) != "item":
                continue
            ident, href = item.get("id"), item.get("href")
            if not ident or not href or ident in entries:
                raise HwpxParseError("Missing/duplicate manifest ID or href")
            entries[ident] = attrs(item)
            reference = urlsplit(href)
            if reference.scheme or reference.netloc:
                paths[ident] = None
                self.diagnostic("external_reference", href)
            else:
                paths[ident] = package.resolve(href, hpf_path, must_exist=False)
        metadata = child(hpf, "metadata")
        fields = []
        if metadata is not None:
            for e in metadata:
                name = e.get("name") or tag(e)
                value = "".join(e.itertext()).strip() or None
                fields.append({"name": name, "value": value, "attributes": attrs(e)})
        names = {f["name"]: f["value"] for f in fields}
        self.document.metadata.update({
            "title": names.get("title"), "creator": names.get("creator"),
            "created": names.get("CreatedDate") or names.get("created"),
            "modified": names.get("ModifiedDate") or names.get("modified"),
            "package_metadata": fields,
        })
        if "version.xml" in package.entries:
            self.document.metadata["version"] = record(package.xml("version.xml"))
        headers = [paths[i] for i, entry in entries.items() if i == "header" or entry["href"].endswith("/header.xml")]
        if len(set(headers)) != 1 or headers[0] is None:
            raise HwpxParseError("Missing or ambiguous header reference")
        self.read_header(package.xml(headers[0]))
        for ident, entry in entries.items():
            mime = entry.get("media-type", "application/octet-stream")
            path = paths[ident]
            if mime.startswith("image/") or "BinData/" in entry["href"]:
                data = None
                if path is not None and path in package.entries:
                    data = package.read(path)
                elif path is not None:
                    self.diagnostic("missing_asset", entry["href"], asset_id=ident)
                asset = {"id": ident, "href": entry["href"], "path": path, "media_type": mime,
                         "size_bytes": len(data) if data is not None else None,
                         "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                         "used_by": [], "output_path": None}
                self.document.assets.append(asset)
                self.assets[ident] = asset
                if data is not None:
                    self.document._asset_bytes[ident] = data
        sections = []
        seen = set()
        for item in spine:
            if tag(item) != "itemref":
                continue
            ident = item.get("idref")
            if ident not in entries or paths[ident] is None:
                raise HwpxParseError(f"Invalid spine reference: {ident}")
            path = paths[ident]
            if path in seen:
                raise HwpxParseError(f"Repeated spine item: {path}")
            seen.add(path)
            # A spine can include Scripts/*.js. Never execute or parse those as XML.
            mime = entries[ident].get("media-type", "").split(";", 1)[0].lower()
            if not path.lower().endswith(".xml") and mime not in {"application/xml", "text/xml"} and not mime.endswith("+xml"):
                self.diagnostic("unsupported_spine_item", path, severity="info", media_type=mime)
                continue
            root = package.xml(path)
            if tag(root) == "sec":
                sections.append((path, root))
            elif tag(root) != "head":
                self.diagnostic("unsupported_spine_item", path, root=root.tag)
        if not sections:
            raise HwpxParseError("No section in package spine")
        self.document.metadata["section_count"] = len(sections)
        for index, (path, root) in enumerate(sections):
            self.section_index, self.file = index, path
            self.section = {"index": index, "path": path, "properties": [], "columns": [], "block_ids": []}
            self.document.sections.append(self.section)
            for offset, paragraph in enumerate(root):
                self.walk(paragraph, None, "body", f"/sec/{offset}")
        self.document.metadata["heading_configuration"] = "Explicit OUTLINE and caller-provided style mapping"
        return self.document

    def read_header(self, root):
        if tag(root) != "head":
            raise HwpxParseError("Unsupported header XML namespace/root")
        targets = {"style": self.styles, "paraPr": self.para_properties, "charPr": self.char_properties}
        for element in root.iter():
            name = tag(element)
            target = targets.get(name)
            if target is not None:
                ident = element.get("id")
                if ident is None or ident in target:
                    raise HwpxParseError(f"Missing/duplicate {name} ID: {ident}")
                target[ident] = element
        self.document.metadata["header_version"] = root.get("version")
        self.document.metadata["styles"] = {i: attrs(e) for i, e in self.styles.items()}
        self.document.metadata["character_properties"] = {i: record(e) for i, e in self.char_properties.items()}
        self.document.metadata["paragraph_properties"] = {i: record(e) for i, e in self.para_properties.items()}
        self.document.metadata["font_faces"] = [record(e) for e in root.iter() if tag(e) == "fontface"]
        self.document.metadata["numberings"] = [record(e) for e in root.iter() if tag(e) in {"numbering", "bullet"}]

    def reference(self, mapping, ident, category):
        value = mapping.get(ident)
        if ident is not None and value is None:
            self.diagnostic("missing_style_reference", self.file, category=category, id=ident)
        return value

    def new(self, cls, kind, parent, scope, path, element, **values):
        self.package.check()
        if len(self.document.blocks) >= self.package.limits.max_blocks:
            raise HwpxParseError("Block count exceeds limit")
        token = f"{self.document.metadata['sha256']}:{self.file}:{path}"
        ident = kind + "-" + hashlib.sha256(token.encode()).hexdigest()[:24]
        block = cls(id=ident, kind=kind, parent_id=parent, scope=scope,
                    order=len(self.document.blocks), source_ref={
                        "file": self.file, "section_index": self.section_index,
                        "xml_path": path, "xml_id": element.get("id")}, **values)
        self.document.blocks.append(block)
        self.section["block_ids"].append(ident)
        return block

    def walk(self, element, parent, scope, path):
        name = tag(element)
        if name == "p":
            return [self.paragraph(element, parent, scope, path).id]
        if name == "tbl":
            return [self.table(element, parent, scope, path).id]
        if name == "pic":
            block = self.new(ImageBlock, "image", parent, scope, path, element, metadata=self.object_metadata(element))
            image = next((e for e in element.iter() if tag(e) == "img"), None)
            block.asset_id = image.get("binaryItemIDRef") if image is not None else None
            if block.asset_id in self.assets:
                self.assets[block.asset_id]["used_by"].append(block.id)
            else:
                self.diagnostic("missing_image_reference", block.source_ref, asset_id=block.asset_id)
            self.walk_sublists(element, block.id, "caption", path)
            return [block.id]
        scopes = {"header": "header", "footer": "footer", "footNote": "footnote",
                  "endNote": "endnote", "caption": "caption", "drawText": "textbox"}
        if name in scopes:
            block = self.new(Block, "container", parent, scopes[name], path, element,
                             metadata={"element": name, "attributes": attrs(element)})
            self.walk_children(element, block.id, scopes[name], path)
            return [block.id]
        if name in {"rect", "ellipse", "polygon", "curve", "arc", "line", "connectLine", "container", "textart", "ole", "equation", "chart", "video"}:
            block = self.new(Block, "container", parent, scope, path, element, metadata=self.object_metadata(element))
            severity = "warning" if name in {"ole", "equation", "chart", "video", "textart"} else "info"
            self.diagnostic("non_text_object", block.source_ref, element=name, severity=severity)
            self.walk_sublists(element, block.id, "textbox", path)
            return [block.id]
        if name == "secPr":
            self.section["properties"].append(record(element))
            return []
        if name == "colPr":
            self.section["columns"].append(attrs(element))
            return []
        if name in {"ctrl", "subList", "run"}:
            return self.walk_children(element, parent, scope, path)
        if name == "textMargin":
            return []
        if name in {"linesegarray", "lineseg", "fieldBegin", "fieldEnd", "autoNum", "newNum", "pageNum", "pageHiding", "pageNumCtrl", "bookmark", "indexmark", "formObject", "compose", "dutmal", "t"}:
            # Preserve control attributes; fields have separate displayed text in runs.
            if name not in {"linesegarray", "lineseg", "t"}:
                self.diagnostic("control_metadata_only", {"file": self.file, "xml_path": path}, severity="info", element=record(element))
            return []
        self.diagnostic("unsupported_element", {"file": self.file, "xml_path": path}, element=name)
        return self.walk_children(element, parent, scope, path)

    def walk_children(self, element, parent, scope, path):
        result = []
        for index, item in enumerate(element):
            result.extend(self.walk(item, parent, scope, f"{path}/{tag(item)}[{index}]"))
        return result

    def walk_sublists(self, element, parent, scope, path):
        for index, item in enumerate(element):
            item_path = f"{path}/{tag(item)}[{index}]"
            if tag(item) in {"subList", "drawText", "caption", "p", "tbl", "pic", "rect", "ellipse", "polygon", "curve", "arc", "line", "connectLine", "container", "textart", "ole", "equation", "chart", "video"}:
                self.walk(item, parent, scope, item_path)
            else:
                self.walk_sublists(item, parent, scope, item_path)

    def object_metadata(self, element):
        return {"element": tag(element), "attributes": attrs(element),
                "geometry_unit": "HWPUNIT", "coordinate_system": "source_relative",
                "properties": {tag(e): record(e) for e in element if tag(e) not in {"tr", "subList", "drawText", "caption", "p", "tbl", "pic", "rect", "ellipse", "polygon", "curve", "arc", "line", "connectLine", "container", "textart", "ole", "equation", "chart", "video"}}}

    def paragraph(self, element, parent, scope, path):
        style_id = element.get("styleIDRef")
        style = self.reference(self.styles, style_id, "style")
        para_id = element.get("paraPrIDRef") or attrs(style).get("paraPrIDRef")
        para = self.reference(self.para_properties, para_id, "paragraph")
        heading = child(para, "heading") if para is not None else None
        layout = child(element, "linesegarray")
        block = self.new(TextBlock, "text", parent, scope, path, element, metadata={
            "paragraph_attributes": attrs(element), "paragraph_property_id": para_id,
            "style_id": style_id, "style": attrs(style), "heading": attrs(heading),
            "linesegments": [attrs(e) for e in layout if tag(e) == "lineseg"] if layout is not None else [],
            "layout_offset_unit": "source_native", "text_offset_unit": "unicode_codepoints",
        })
        for index, run in enumerate(element):
            name = tag(run)
            run_path = f"{path}/{name}[{index}]"
            if name == "linesegarray":
                continue
            if name != "run":
                ids = self.walk(run, block.id, scope, run_path)
                block.content.extend({"type": "object", "block_id": ident, "text_offset": len(block.text)} for ident in ids)
                continue
            char_id = run.get("charPrIDRef") or attrs(style).get("charPrIDRef")
            self.reference(self.char_properties, char_id, "character")
            start = len(block.text)
            for item_index, item in enumerate(run):
                item_path = f"{run_path}/{tag(item)}[{item_index}]"
                if tag(item) == "t":
                    value = self.inline_text(item, item_path)
                    text_start = len(block.text)
                    block.text += value
                    block.content.append({"type": "text", "start": text_start, "end": len(block.text)})
                else:
                    ids = self.walk(item, block.id, scope, item_path)
                    for ident in ids:
                        block.content.append({"type": "object", "block_id": ident, "text_offset": len(block.text)})
            block.runs.append({"start": start, "end": len(block.text), "char_property_id": char_id, "attributes": attrs(run)})
        return block

    def inline_text(self, element, path):
        parts = [element.text or ""]
        translations = {"tab": "\t", "lineBreak": "\n", "nbSpace": "\u00a0", "fwSpace": "\u3000", "hyphen": "\u00ad"}
        for index, item in enumerate(element):
            name = tag(item)
            if name in translations:
                parts.append(translations[name])
            else:
                self.diagnostic("inline_control", {"file": self.file, "xml_path": path}, element=name)
                parts.append(self.inline_text(item, f"{path}/{index}"))
            parts.append(item.tail or "")
        return "".join(parts)

    def table(self, element, parent, scope, path):
        rows = integer(element.get("rowCnt"), "rowCnt")
        columns = integer(element.get("colCnt"), "colCnt")
        if rows <= 0 or columns <= 0 or rows * columns > self.package.limits.max_nodes:
            raise HwpxParseError(f"Invalid/oversized table dimensions at {self.file}:{path}")
        block = self.new(TableBlock, "table", parent, scope, path, element,
                         rows=rows, columns=columns, metadata=self.object_metadata(element))
        occupied = set()
        for row_index, row in enumerate(e for e in element if tag(e) == "tr"):
            for cell_index, cell in enumerate(e for e in row if tag(e) == "tc"):
                address = attrs(child(cell, "cellAddr"))
                span = attrs(child(cell, "cellSpan"))
                r = integer(address.get("rowAddr"), "rowAddr", row_index)
                c = integer(address.get("colAddr"), "colAddr", cell_index)
                rs, cs = integer(span.get("rowSpan"), "rowSpan", 1), integer(span.get("colSpan"), "colSpan", 1)
                if min(r, c) < 0 or min(rs, cs) < 1 or r + rs > rows or c + cs > columns:
                    raise HwpxParseError(f"Invalid cell span at {self.file}:{path}")
                coverage = {(a, b) for a in range(r, r + rs) for b in range(c, c + cs)}
                if occupied.intersection(coverage):
                    raise HwpxParseError(f"Overlapping cells at {self.file}:{path}")
                occupied.update(coverage)
                ident = f"{block.id}/cell/{row_index}/{cell_index}"
                result = Cell(ident, r, c, rs, cs, metadata={"attributes": attrs(cell), "size": attrs(child(cell, "cellSz")), "margin": attrs(child(cell, "cellMargin"))})
                block.cells.append(result)
                for sub_index, sub in enumerate(cell):
                    if tag(sub) == "subList":
                        result.block_ids.extend(self.walk(sub, ident, "cell", f"{path}/tr[{row_index}]/tc[{cell_index}]/subList[{sub_index}]"))
        for index, item in enumerate(element):
            if tag(item) == "caption":
                self.walk(item, block.id, "caption", f"{path}/caption[{index}]")
        return block
