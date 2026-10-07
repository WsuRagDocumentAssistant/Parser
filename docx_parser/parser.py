from __future__ import annotations
import hashlib
from pathlib import Path

from .headings import HeadingBuilder
from .models import Block, Cell, Document, ImageBlock, PageLocation, TableBlock, TextBlock
from .package import DocxParseError, Package, ParseLimits, attr, child, record, tag


def integer(value, label, default=0):
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError) as exc:
        raise DocxParseError(f"Invalid integer for {label}: {value!r}") from exc


def enabled(element):
    return element is not None and attr(element, default="true").lower() not in {"0", "false", "off"}


class DocxParser:
    """Reusable configuration; a new reader owns each document's mutable state."""
    def __init__(self, page_mode="auto", style_levels=None, heading_scopes=("body",),
                 auto_headings=None, limits=None):
        if page_mode not in {"auto", "layout", "structural"}:
            raise ValueError("page_mode must be auto, layout or structural")
        self.page_mode = page_mode
        self.limits = limits or ParseLimits()
        self.heading_builder = HeadingBuilder(style_levels, heading_scopes, auto_headings)

    def parse(self, path: str | Path) -> Document:
        source = Path(path)
        with source.open("rb") as stream:
            data = stream.read(self.limits.max_file_bytes + 1)
        if len(data) > self.limits.max_file_bytes:
            raise DocxParseError("Input file exceeds size limit")
        package = Package(data, self.limits)
        with package.zip:
            reader = _Reader(package, source.name, hashlib.sha256(data).hexdigest(), self.page_mode)
            document = reader.read()
            self.heading_builder.build(document)
            package.check()
            return document


class _Reader:
    def __init__(self, package, filename, digest, page_mode):
        self.package = package
        self.document = Document({"filename": filename, "sha256": digest, "parser_version": "0.1.0",
                                  "format": "docx", "revision_view": "final", "page_mode": page_mode})
        self.file = ""
        self.styles, self.style_cache, self.assets = {}, {}, {}
        self.default_style = None
        self.defaults = None
        self.page_mode, self.cached_pages, self.page = page_mode, False, 1
        self.section_index = 0
        self.section = {"index": 0, "properties": None, "block_ids": []}
        self.document.sections.append(self.section)

    def diagnostic(self, code, source=None, severity="warning", **details):
        self.document.diagnostics.append(dict(severity=severity, code=code, source=source, **details))

    def selected(self, element, *, body_only=False, shallow_textboxes=False):
        """Yield final-view descendants, selecting one AlternateContent branch."""
        name = tag(element)
        if name in {"w:del", "w:moveFrom"} or body_only and name == "w:txbxContent":
            return
        yield element
        if shallow_textboxes and name == "w:txbxContent":
            return
        if name == "mc:AlternateContent":
            branch = self.package.alternate(element)
            if branch is not None:
                yield from self.selected(branch, body_only=body_only, shallow_textboxes=shallow_textboxes)
        else:
            for item in element:
                yield from self.selected(item, body_only=body_only, shallow_textboxes=shallow_textboxes)

    def relation_part(self, relations, kind):
        candidates = [r for r in relations.values() if self.package.is_relation(r, kind)]
        if len(candidates) > 1:
            raise DocxParseError(f"Ambiguous {kind} part")
        if not candidates:
            return None
        relation = candidates[0]
        if relation["external"]:
            raise DocxParseError(f"External {kind} part is unsupported")
        return relation["path"]

    def read(self):
        root_relations = self.package.relationships("")
        main = self.relation_part(root_relations, "officeDocument")
        if not main:
            raise DocxParseError("No officeDocument relationship")
        if self.package.content_type(main) != "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml":
            raise DocxParseError("Not a DOCX main document (DOCM/DOTX unsupported)")
        self.file = main
        root = self.package.xml(main)
        body = child(root, "w:body")
        if tag(root) != "w:document" or body is None:
            raise DocxParseError("Unsupported document namespace/root or missing body")
        self.document.metadata["main_part"] = main
        relations = self.package.relationships(main)
        for kind in ("styles", "numbering", "fontTable", "settings", "theme"):
            part = self.relation_part(relations, kind)
            if part:
                xml = self.package.xml(part)
                self.document.metadata[kind] = record(xml)
                if kind == "styles":
                    self.read_styles(xml)
        self.cached_pages = self.page_mode != "structural" and any(
            tag(e) == "w:lastRenderedPageBreak" for e in self.selected(body, body_only=True))
        for index, element in enumerate(body):
            self.walk(element, None, "body", f"/document/body/{index}")
        if self.cached_pages:
            self.document.metadata["estimated_page_count"] = self.page
            self.diagnostic("cached_pagination", severity="info",
                            message="Saved page-break cache may be stale/incomplete; physical pages are unverified.")
        else:
            self.document.metadata["estimated_page_count"] = None
        self.document.metadata["section_count"] = len(self.document.sections)
        self.document.metadata["relationships"] = {main: list(relations.values())}
        for kind, scope, expected in (("header", "header", "w:hdr"), ("footer", "footer", "w:ftr"),
                                      ("footnotes", "footnote", "w:footnotes"),
                                      ("endnotes", "endnote", "w:endnotes"), ("comments", "comment", "w:comments")):
            seen = set()
            for relation in relations.values():
                if not self.package.is_relation(relation, kind):
                    continue
                part = relation["path"]
                if relation["external"] or part not in self.package.entries:
                    self.diagnostic("missing_story_part", relation)
                    continue
                if part in seen:
                    continue
                seen.add(part)
                self.file = part
                story = self.package.xml(part)
                if tag(story) != expected:
                    raise DocxParseError(f"Unsupported {kind} root: {part}")
                self.document.metadata["relationships"][part] = list(self.package.relationships(part).values())
                if scope in {"header", "footer"}:
                    container = self.new(Block, "container", None, scope, "/" + scope, story,
                                         metadata={"relationship": relation})
                    self.walk_children(story, container.id, scope, "/" + scope)
                else:
                    for index, note in enumerate(story):
                        if tag(note) != "w:" + scope:
                            continue
                        if scope != "comment" and attr(note, "type", default="normal") != "normal":
                            continue
                        container = self.new(Block, "container", None, scope, f"/{scope}/{index}", note,
                                             metadata={"attributes": dict(note.attrib)})
                        self.walk_children(note, container.id, scope, f"/{scope}/{index}")
        self.read_properties(root_relations)
        return self.document

    def read_properties(self, relations):
        for relation in relations.values():
            kind = relation["type"].rsplit("/", 1)[-1]
            valid = (self.package.is_relation(relation, "extended-properties")
                     or self.package.is_relation(relation, "custom-properties")
                     or relation["type"] == "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties")
            if not valid or relation["external"]:
                continue
            root = self.package.xml(relation["path"])
            self.document.metadata[kind] = record(root)
            if kind == "core-properties":
                allowed = {"http://purl.org/dc/elements/1.1/", "http://purl.org/dc/terms/"}
                for e in root:
                    uri, _, name = e.tag[1:].partition("}")
                    if uri in allowed and name in {"title", "creator", "created", "modified", "description"}:
                        self.document.metadata[name] = e.text

    def read_styles(self, root):
        if tag(root) != "w:styles":
            raise DocxParseError("Unsupported styles root")
        defaults = child(root, "w:docDefaults")
        self.defaults = child(child(defaults, "w:pPrDefault"), "w:pPr")
        for style in root:
            if tag(style) != "w:style":
                continue
            ident = attr(style, "styleId")
            if not ident or ident in self.styles:
                raise DocxParseError("Missing/duplicate style ID")
            self.styles[ident] = style
            if attr(style, "type") == "paragraph" and attr(style, "default") in {"1", "true", "on"}:
                self.default_style = ident

    def properties(self, style_id):
        if style_id in self.style_cache:
            return self.style_cache[style_id]
        chain, seen, ident = [], set(), style_id
        while ident:
            if ident in seen:
                raise DocxParseError(f"Cyclic style inheritance: {ident}")
            seen.add(ident)
            style = self.styles.get(ident)
            if style is None:
                self.diagnostic("missing_style_reference", self.file, style_id=ident)
                break
            chain.append(style)
            ident = attr(child(style, "w:basedOn"))
        properties = {tag(e): e for e in self.defaults} if self.defaults is not None else {}
        for style in reversed(chain):
            ppr = child(style, "w:pPr")
            if ppr is not None:
                properties.update({tag(e): e for e in ppr})
        self.style_cache[style_id] = properties
        return properties

    def new(self, cls, kind, parent, scope, path, element, **values):
        self.package.check()
        if len(self.document.blocks) >= self.package.limits.max_blocks:
            raise DocxParseError("Block count exceeds limit")
        token = f"{self.document.metadata['sha256']}:{self.file}:{path}:{kind}"
        block = cls(id=kind + "-" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:24], kind=kind,
                    order=len(self.document.blocks), parent_id=parent, scope=scope,
                    source_ref={"file": self.file, "xml_path": path, "section_index": self.section_index if scope in {"body", "cell", "textbox"} else None,
                                "xml_id": attr(element, "id")}, **values)
        self.document.blocks.append(block)
        if scope in {"body", "cell", "textbox"}:
            self.section["block_ids"].append(block.id)
        return block

    def location(self, scope, pages, fragments=None, floating=False):
        if not self.cached_pages or scope not in {"body", "cell"}:
            return PageLocation(reason="No saved page-break cache, structural mode, or non-body story.")
        if floating:
            return PageLocation(anchor_page=self.page, method="saved_page_breaks",
                                reason="Floating image anchor does not prove its display page.")
        return PageLocation(physical_pages=sorted(pages), status="estimated", method="saved_page_breaks",
                            reason="Assumes saved page-break cache is complete and current; not verified.",
                            anchor_page=min(pages), fragments=fragments or [],
                            evidence={"confidence": "low", "coverage": "candidate", "cache_verified": False})

    def walk_children(self, element, parent, scope, path):
        result = []
        for index, item in enumerate(element):
            result.extend(self.walk(item, parent, scope, f"{path}/{index}"))
        return result

    def walk(self, element, parent, scope, path):
        name = tag(element)
        if name == "w:p":
            return [self.paragraph(element, parent, scope, path).id]
        if name == "w:tbl":
            return [self.table(element, parent, scope, path).id]
        if name == "w:sectPr":
            if scope == "body":
                self.section["properties"] = record(element)
            return []
        if name in {"w:del", "w:moveFrom"}:
            self.diagnostic("revision_excluded", {"file": self.file, "xml_path": path}, severity="info", element=name)
            return []
        if name == "mc:AlternateContent":
            branch = self.package.alternate(element)
            if branch is None:
                self.diagnostic("unsupported_alternate_content", path)
                return []
            return self.walk_children(branch, parent, scope, path + "/selected")
        if name == "w:sdt":
            content = child(element, "w:sdtContent")
            return self.walk_children(content, parent, scope, path + "/sdtContent") if content is not None else []
        if name in {"w:ins", "w:moveTo", "w:customXml", "w:sdtContent", "mc:Choice", "mc:Fallback"}:
            return self.walk_children(element, parent, scope, path)
        if name in {"w:bookmarkStart", "w:bookmarkEnd", "w:proofErr", "w:permStart", "w:permEnd",
                    "w:commentRangeStart", "w:commentRangeEnd", "w:moveFromRangeStart", "w:moveFromRangeEnd",
                    "w:moveToRangeStart", "w:moveToRangeEnd"}:
            return []
        block = self.new(Block, "container", parent, scope, path, element, metadata={"xml": record(element)})
        self.diagnostic("unsupported_content", block.source_ref, element=name)
        return [block.id]

    def paragraph(self, element, parent, scope, path):
        ppr = child(element, "w:pPr")
        style_id = attr(child(ppr, "w:pStyle"), default=self.default_style)
        effective = dict(self.properties(style_id))
        if ppr is not None:
            effective.update({tag(e): e for e in ppr})
        outline_element = effective.get("w:outlineLvl")
        outline = integer(attr(outline_element), "outlineLvl") if outline_element is not None else None
        if outline is not None and not 0 <= outline <= 9:
            raise DocxParseError("outlineLvl must be between 0 and 9")
        style = self.styles.get(style_id)
        metadata = {"attributes": dict(element.attrib), "paragraph_properties": record(ppr),
                    "effective_paragraph_properties": {k: record(v) for k, v in effective.items()},
                    "style_id": style_id, "style": {"name": attr(child(style, "w:name"))},
                    "outline_level": outline, "outline_origin": "direct" if child(ppr, "w:outlineLvl") is not None else "style",
                    "numbering": record(effective.get("w:numPr")), "page_break_before": enabled(effective.get("w:pageBreakBefore"))}
        block = self.new(TextBlock, "text", parent, scope, path, element, metadata=metadata)
        state = {"offset": 0, "pieces": [], "pages": set(), "fragments": []}

        def append(text):
            if not text:
                return
            start = state["offset"]
            state["pieces"].append(text)
            state["offset"] += len(text)
            block.content.append({"kind": "text", "start": start, "end": state["offset"], "text": text})
            if self.cached_pages and scope in {"body", "cell"}:
                state["fragments"].append({"start": start, "end": state["offset"], "physical_pages": [self.page]})
                state["pages"].add(self.page)

        def inline(item, item_path):
            name = tag(item)
            if name in {"w:pPr", "w:rPr"}:
                return
            if name in {"w:del", "w:moveFrom"}:
                self.diagnostic("revision_excluded", block.source_ref, severity="info", element=name)
                return
            if name == "mc:AlternateContent":
                branch = self.package.alternate(item)
                if branch is None:
                    self.diagnostic("unsupported_alternate_content", block.source_ref)
                else:
                    for i, e in enumerate(branch):
                        inline(e, item_path + f"/selected/{i}")
                return
            if name == "w:sdt":
                content = child(item, "w:sdtContent")
                if content is not None:
                    for i, e in enumerate(content):
                        inline(e, item_path + f"/sdtContent/{i}")
                return
            if name == "w:r":
                start = state["offset"]
                for i, e in enumerate(item):
                    inline(e, f"{item_path}/{i}")
                block.runs.append({"start": start, "end": state["offset"],
                                   "properties": record(child(item, "w:rPr")), "attributes": dict(item.attrib)})
                return
            if name == "w:t":
                append(item.text or "")
                return
            if name in {"w:tab", "w:ptab"}:
                append("\t")
                return
            if name in {"w:cr", "w:br"}:
                kind = attr(item, "type", default="textWrapping")
                append("\n")
                if kind != "textWrapping":
                    block.content.append({"kind": "break", "type": kind, "offset": state["offset"]})
                return
            if name == "w:lastRenderedPageBreak":
                block.content.append({"kind": "page_break", "source": "saved_layout", "offset": state["offset"]})
                if self.cached_pages and scope in {"body", "cell"}:
                    self.page += 1
                return
            if name in {"w:noBreakHyphen", "w:softHyphen"}:
                append("\u2011" if name == "w:noBreakHyphen" else "\u00ad")
                return
            if name in {"w:instrText", "w:fldChar", "w:sym", "w:footnoteReference", "w:endnoteReference", "w:commentReference"}:
                block.content.append({"kind": name.split(":")[1], "offset": state["offset"], "xml": record(item)})
                if name == "w:sym":
                    self.diagnostic("unresolved_symbol_font", block.source_ref)
                return
            if name in {"w:drawing", "w:pict", "w:object"}:
                self.drawing(item, block, scope, item_path, state["offset"])
                state["pages"].add(self.page)
                return
            if name in {"m:oMath", "m:oMathPara"}:
                obj = self.new(Block, "container", block.id, scope, item_path, item, metadata={"xml": record(item)})
                block.content.append({"kind": "object", "block_id": obj.id, "offset": state["offset"]})
                self.diagnostic("unrendered_equation", obj.source_ref)
                return
            if name == "w:hyperlink":
                relation = self.package.relationships(self.file).get(attr(item, "id", "r"))
                metadata.setdefault("hyperlinks", []).append({"relationship": relation, "anchor": attr(item, "anchor"),
                                                             "start": state["offset"]})
            elif name == "w:fldSimple":
                metadata.setdefault("fields", []).append(dict(item.attrib))
            elif name not in {"w:ins", "w:moveTo", "w:smartTag", "w:customXml", "w:sdtContent",
                              "w:bookmarkStart", "w:bookmarkEnd", "w:proofErr", "w:permStart", "w:permEnd",
                              "w:commentRangeStart", "w:commentRangeEnd", "w:delText"}:
                metadata.setdefault("unsupported_inline", []).append(record(item))
                self.diagnostic("unsupported_inline", block.source_ref, element=name)
                return
            for i, e in enumerate(item):
                inline(e, f"{item_path}/{i}")

        for index, item in enumerate(element):
            inline(item, f"{path}/{index}")
        block.text = "".join(state["pieces"])
        block.location = self.location(scope, state["pages"] or {self.page}, state["fragments"])
        section = child(ppr, "w:sectPr")
        if section is not None and scope == "body":
            self.section["properties"] = record(section)
            self.section_index += 1
            self.section = {"index": self.section_index, "properties": None, "block_ids": []}
            self.document.sections.append(self.section)
        return block

    def drawing(self, element, parent, scope, path, offset):
        nodes = list(self.selected(element, shallow_textboxes=True))
        floating = any(tag(e) == "wp:anchor" for e in nodes)
        images = [(i, e) for i, e in enumerate(nodes) if tag(e) in {"a:blip", "v:imagedata"}]
        for index, image in images:
            rid = attr(image, "embed", "r") or attr(image, "link", "r") or attr(image, "id", "r")
            relation = self.package.relationships(self.file).get(rid)
            block = self.new(ImageBlock, "image", parent.id, scope, f"{path}/image/{index}", image,
                             metadata={"relationship_id": rid, "drawing": record(element), "floating": floating})
            block.location = self.location(scope, {self.page}, floating=floating)
            parent.content.append({"kind": "image", "block_id": block.id, "offset": offset})
            if not self.package.is_relation(relation, "image"):
                self.diagnostic("missing_image_reference", block.source_ref, relationship_id=rid)
                continue
            key = relation["path"] if not relation["external"] else (self.file, rid)
            asset = self.assets.get(key)
            if asset is None:
                data = None
                if relation["external"]:
                    self.diagnostic("external_image", block.source_ref, target=relation["target"])
                elif relation["path"] in self.package.entries:
                    data = self.package.read(relation["path"])
                else:
                    self.diagnostic("missing_asset", block.source_ref, path=relation["path"])
                ident = "asset-" + hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:24]
                asset = {"id": ident, "href": relation["target"], "path": relation["path"],
                         "media_type": self.package.content_type(relation["path"]) if relation["path"] else "image/external",
                         "size_bytes": len(data) if data is not None else None,
                         "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                         "used_by": [], "output_path": None}
                if data is not None:
                    self.document._asset_bytes[ident] = data
                self.assets[key] = asset
                self.document.assets.append(asset)
            block.asset_id = asset["id"]
            asset["used_by"].append(block.id)
        for index, node in enumerate(nodes):
            if tag(node) == "w:txbxContent":
                box = self.new(Block, "container", parent.id, "textbox", f"{path}/textbox/{index}", node,
                               metadata={"drawing": record(element)})
                parent.content.append({"kind": "object", "block_id": box.id, "offset": offset})
                self.walk_children(node, box.id, "textbox", f"{path}/textbox/{index}")
        if not images or tag(element) == "w:object":
            self.diagnostic("unrendered_drawing", parent.source_ref, xml=record(element))

    def table_items(self, element, target):
        """Unwrap row/cell content controls without entering nested tables."""
        for item in element:
            name = tag(item)
            if name == target:
                yield item
            elif name in {"w:ins", "w:moveTo", "w:customXml", "w:sdtContent"}:
                yield from self.table_items(item, target)
            elif name == "w:sdt":
                content = child(item, "w:sdtContent")
                if content is not None:
                    yield from self.table_items(content, target)
            elif name == "mc:AlternateContent":
                branch = self.package.alternate(item)
                if branch is not None:
                    yield from self.table_items(branch, target)
                else:
                    self.diagnostic("unsupported_alternate_content", self.file)
            elif name in {"w:del", "w:moveFrom"}:
                self.diagnostic("revision_excluded", self.file, severity="info", element=name)
            elif name not in {"w:tblPr", "w:tblGrid", "w:trPr", "w:bookmarkStart", "w:bookmarkEnd",
                              "w:commentRangeStart", "w:commentRangeEnd", "w:proofErr"}:
                self.diagnostic("unsupported_table_content", self.file, xml=record(item))

    def table(self, element, parent, scope, path):
        rows = list(self.table_items(element, "w:tr"))
        grid = child(element, "w:tblGrid")
        columns = len([e for e in grid if tag(e) == "w:gridCol"]) if grid is not None else 0
        block = self.new(TableBlock, "table", parent, scope, path, element, rows=len(rows), columns=columns,
                         metadata={"properties": record(child(element, "w:tblPr")), "grid": record(grid), "row_properties": []})
        active = {}
        for row_index, row in enumerate(rows):
            row_properties = child(row, "w:trPr")
            block.metadata["row_properties"].append(record(row_properties))
            column = integer(attr(child(row_properties, "w:gridBefore")), "gridBefore")
            if column < 0:
                raise DocxParseError("Negative gridBefore")
            next_active = {}
            for cell_index, element_cell in enumerate(self.table_items(row, "w:tc")):
                properties = child(element_cell, "w:tcPr")
                span = integer(attr(child(properties, "w:gridSpan")), "gridSpan", 1)
                if span < 1 or column + span > 1_000_000:
                    raise DocxParseError("Invalid/excessive cell span")
                merge = child(properties, "w:vMerge")
                if child(properties, "w:hMerge") is not None:
                    self.diagnostic("legacy_hmerge_unsupported", block.source_ref)
                merge_value = attr(merge, default="continue") if merge is not None else None
                key = (column, span)
                if merge_value == "continue":
                    cell = active.get(key)
                    if cell is None:
                        raise DocxParseError("Vertical merge continuation has no matching origin")
                    cell.row_span += 1
                    next_active[key] = cell
                    cell.metadata.setdefault("continuations", []).append({"row": row_index, "properties": record(properties)})
                else:
                    if merge_value not in {None, "restart"}:
                        raise DocxParseError("Invalid vMerge value")
                    cell = Cell(f"{block.id}/cell/{row_index}/{cell_index}", row_index, column,
                                col_span=span, metadata={"properties": record(properties)})
                    block.cells.append(cell)
                    if merge_value == "restart":
                        next_active[key] = cell
                nested_scope = "cell" if scope in {"body", "cell"} else scope
                for index, item in enumerate(element_cell):
                    if tag(item) != "w:tcPr":
                        cell.block_ids.extend(self.walk(item, cell.id, nested_scope,
                                                      f"{path}/tr/{row_index}/tc/{cell_index}/{index}"))
                column += span
            after = integer(attr(child(row_properties, "w:gridAfter")), "gridAfter")
            if after < 0:
                raise DocxParseError("Negative gridAfter")
            width = column + after
            if columns and width > columns:
                raise DocxParseError("Table cells exceed declared grid")
            block.columns = max(block.columns, width)
            if block.rows * block.columns > 1_000_000:
                raise DocxParseError("Table grid exceeds limit")
            active = next_active
        descendants = self.document.blocks[block.order + 1:]
        pages = {page for b in descendants for page in (b.location.physical_pages or [])}
        block.location = self.location(scope, pages or {self.page})
        return block
