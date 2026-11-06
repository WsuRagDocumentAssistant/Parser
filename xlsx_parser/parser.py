from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import re

from .models import Block, Cell, Document, HeadingNode, ImageBlock, TableBlock, TextBlock
from .package import Package, ParseLimits, XlsxParseError, attr, child, record, tag


def integer(value, label, default=0):
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError) as exc:
        raise XlsxParseError(f"Invalid integer for {label}: {value!r}") from exc


def coordinates(address):
    match = re.fullmatch(r"\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})", address or "")
    if not match:
        raise XlsxParseError(f"Invalid cell address: {address!r}")
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - 64
    row = int(match[2])
    if row > 1_048_576 or column > 16_384:
        raise XlsxParseError(f"Cell outside XLSX bounds: {address}")
    return row - 1, column - 1


def address(row, column):
    letters = ""
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    result = letters + str(row + 1)
    coordinates(result)
    return result


def bounds(reference):
    parts = (reference or "").split(":")
    if len(parts) not in {1, 2}:
        raise XlsxParseError(f"Invalid cell range: {reference!r}")
    top, left = coordinates(parts[0])
    bottom, right = coordinates(parts[-1])
    if bottom < top or right < left:
        raise XlsxParseError(f"Reversed cell range: {reference}")
    return top, left, bottom, right


def rich_text(element):
    """Read text/rich runs, excluding phonetic pronunciation annotations."""
    pieces, runs = [], []
    offset = 0
    if element is not None:
        for item in element:
            if tag(item) == "s:t":
                text = item.text or ""
                properties = None
            elif tag(item) == "s:r":
                text = "".join(e.text or "" for e in item if tag(e) == "s:t")
                properties = record(child(item, "s:rPr"))
            else:
                continue
            pieces.append(text)
            runs.append({"start": offset, "end": offset + len(text), "properties": properties})
            offset += len(text)
    return "".join(pieces), runs


class XlsxParser:
    """Reusable configuration; each parse owns an independent reader."""
    def __init__(self, limits: ParseLimits | None = None):
        self.limits = limits or ParseLimits()

    def parse(self, path: str | Path) -> Document:
        source = Path(path)
        with source.open("rb") as stream:
            data = stream.read(self.limits.max_file_bytes + 1)
        if len(data) > self.limits.max_file_bytes:
            raise XlsxParseError("Input file exceeds size limit")
        package = Package(data, self.limits)
        with package.zip:
            document = _Reader(package, source.name, hashlib.sha256(data).hexdigest()).read()
            package.check()
            return document


class _Reader:
    def __init__(self, package, filename, digest):
        self.package = package
        self.document = Document({"filename": filename, "sha256": digest, "format": "xlsx",
                                  "parser_version": "0.1.0", "formula_mode": "saved_cache",
                                  "page_mode": "structural"})
        self.document.headings.append(HeadingNode("root", "", 0, 0, None))
        self.strings, self.styles, self.assets = [], [], {}
        self.file, self.section, self.heading = "", None, "root"

    def diagnostic(self, code, source=None, severity="warning", **details):
        self.document.diagnostics.append(dict(severity=severity, code=code, source=source, **details))

    def part(self, relations, kind):
        found = [r for r in relations.values() if self.package.is_relation(r, kind)]
        if len(found) > 1:
            raise XlsxParseError(f"Ambiguous {kind} part")
        if not found:
            return None
        if found[0]["external"]:
            raise XlsxParseError(f"External {kind} part")
        return found[0]["path"]

    def new(self, cls, kind, parent=None, source=None, **values):
        self.package.check()
        if len(self.document.blocks) >= self.package.limits.max_blocks:
            raise XlsxParseError("Block count exceeds limit")
        source = {"file": self.file, "sheet_index": self.section["index"],
                  "sheet_name": self.section["name"], **(source or {})}
        token = f"{self.document.metadata['sha256']}:{source}:{kind}"
        block = cls(id=kind + "-" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:24],
                    kind=kind, order=len(self.document.blocks), scope="sheet", parent_id=parent,
                    source_ref=source, heading_id=self.heading, **values)
        block.location.reason = "Cell addresses are exact; print pages require rendering."
        self.document.blocks.append(block)
        self.section["block_ids"].append(block.id)
        return block

    def read(self):
        workbook_file = self.part(self.package.relationships(""), "officeDocument")
        if not workbook_file:
            raise XlsxParseError("No workbook officeDocument relationship")
        workbook = self.package.xml(workbook_file)
        if tag(workbook) != "s:workbook":
            raise XlsxParseError("Not a SpreadsheetML workbook")
        relations = self.package.relationships(workbook_file)
        strings_file = self.part(relations, "sharedStrings")
        if strings_file:
            strings = self.package.xml(strings_file)
            if tag(strings) != "s:sst":
                raise XlsxParseError("Invalid shared string table root")
            self.strings = [rich_text(e) for e in strings if tag(e) == "s:si"]
        styles_file = self.part(relations, "styles")
        if styles_file:
            styles = self.package.xml(styles_file)
            if tag(styles) != "s:styleSheet":
                raise XlsxParseError("Invalid styles root")
            self.document.metadata["styles"] = record(styles)
            xfs = child(styles, "s:cellXfs")
            self.styles = [record(e) for e in xfs] if xfs is not None else []
        self.document.metadata.update({"workbook_file": workbook_file,
                                       "workbook_properties": record(child(workbook, "s:workbookPr")),
                                       "defined_names": record(child(workbook, "s:definedNames")),
                                       "calculation_properties": record(child(workbook, "s:calcPr")),
                                       "relationships": relations})
        self.document.metadata["properties"] = {
            name: record(self.package.xml(name)) for name in sorted(self.package.entries)
            if name in {"docProps/core.xml", "docProps/app.xml", "docProps/custom.xml"}}
        sheets = child(workbook, "s:sheets")
        if sheets is None:
            raise XlsxParseError("Workbook has no sheets element")
        seen_ids, seen_names = set(), set()
        for index, sheet in enumerate(sheets):
            if tag(sheet) != "s:sheet":
                continue
            name, ident = sheet.get("name"), sheet.get("sheetId")
            if not name or not ident or name in seen_names or ident in seen_ids:
                raise XlsxParseError("Missing/duplicate sheet name or ID")
            seen_names.add(name)
            seen_ids.add(ident)
            relation = relations.get(attr(sheet))
            if relation is None or relation["external"]:
                raise XlsxParseError(f"Missing/external sheet relationship: {name}")
            self.file = relation["path"]
            self.section = {"index": index, "name": name, "sheet_id": ident,
                            "state": sheet.get("state", "visible"), "file": self.file, "block_ids": []}
            self.document.sections.append(self.section)
            self.heading = f"sheet-{index}"
            self.document.headings[0].children.append(self.heading)
            self.document.headings.append(HeadingNode(self.heading, name, 1, 1, "root", source="sheet_name",
                                                       path_ids=[self.heading], path_titles=[name]))
            if not self.package.is_relation(relation, "worksheet"):
                self.diagnostic("unsupported_sheet_type", self.file, relationship=relation)
                continue
            self.sheet()
        for kind in ("externalLink", "pivotCacheDefinition", "vbaProject"):
            if any(r["type"].endswith("/" + kind) for r in relations.values()):
                self.diagnostic("unsupported_workbook_feature", workbook_file, feature=kind)
        return self.document

    def cell_value(self, element):
        kind = element.get("t", "n")
        node = child(element, "s:v")
        raw = node.text if node is not None else None
        runs = []
        if kind == "inlineStr":
            value, runs = rich_text(child(element, "s:is"))
        elif raw is None:
            value = None
        elif kind == "s":
            index = integer(raw, "shared string index")
            if not 0 <= index < len(self.strings):
                raise XlsxParseError(f"Shared string index out of range: {index}")
            value, runs = self.strings[index]
        elif kind == "b":
            if raw not in {"0", "1"}:
                raise XlsxParseError(f"Invalid Boolean value: {raw}")
            value = raw == "1"
        elif kind == "n":
            try:
                if not Decimal(raw).is_finite():
                    raise InvalidOperation
            except InvalidOperation as exc:
                raise XlsxParseError(f"Invalid numeric value: {raw!r}") from exc
            # Preserve decimals and large integers exactly; do not coerce to binary floats.
            value = raw
        elif kind in {"str", "e", "d"}:
            value = raw
        else:
            value = raw
            self.diagnostic("unsupported_cell_type", self.file, data_type=kind)
        formula = child(element, "s:f")
        text = str(value) if value is not None else ("=" + (formula.text or "") if formula is not None and formula.text else "")
        return text, runs, {"data_type": kind, "raw_value": raw, "value": value,
                            "formula": record(formula), "cached_value": value if formula is not None else None,
                            "cache_status": "unverified" if formula is not None and raw is not None else "missing" if formula is not None else "not_applicable"}

    def sheet(self):
        root = self.package.xml(self.file)
        if tag(root) != "s:worksheet":
            raise XlsxParseError(f"Invalid worksheet root: {self.file}")
        self.section["properties"] = {tag(e): record(e) for e in root if tag(e) in {
            "s:dimension", "s:sheetPr", "s:sheetViews", "s:sheetFormatPr", "s:cols", "s:autoFilter",
            "s:printOptions", "s:pageMargins", "s:pageSetup", "s:headerFooter", "s:rowBreaks", "s:colBreaks"}}
        relations = self.package.relationships(self.file)
        self.section["relationships"] = relations
        table = self.new(TableBlock, "table", source={"xml_path": "worksheet/sheetData"},
                         metadata={"table_type": "worksheet", "sheet_name": self.section["name"],
                                   "row_properties": [], "merged_ranges": [], "structured_tables": []})
        self.document.headings[-1].block_id = table.id
        cells, text_blocks = {}, {}
        rows = child(root, "s:sheetData")
        previous_row = -1
        if rows is None:
            raise XlsxParseError("Worksheet has no sheetData")
        for row in rows:
            if tag(row) != "s:row":
                continue
            row_index = integer(row.get("r"), "row", previous_row + 2) - 1
            if not 0 <= row_index < 1_048_576 or row_index <= previous_row:
                raise XlsxParseError("Invalid/non-increasing row index")
            previous_row = row_index
            table.metadata["row_properties"].append(dict(row.attrib))
            previous_column = -1
            for element in row:
                self.package.check()
                if tag(element) != "s:c":
                    continue
                cell_address = element.get("r") or address(row_index, previous_column + 1)
                cell_row, column = coordinates(cell_address)
                if cell_row != row_index or column <= previous_column:
                    raise XlsxParseError("Cell address conflicts with row/order")
                previous_column = column
                text, runs, metadata = self.cell_value(element)
                style_index = integer(element.get("s"), "style index")
                if style_index < 0 or (self.styles and style_index >= len(self.styles)) or (not self.styles and style_index != 0):
                    raise XlsxParseError("Cell style index out of range")
                metadata.update({"attributes": dict(element.attrib), "style_index": style_index,
                                 "style": self.styles[style_index] if self.styles else None})
                cell = Cell(f"{table.id}/{cell_address}", cell_row, column, metadata={"address": cell_address, **metadata})
                cells[(cell_row, column)] = cell
                table.cells.append(cell)
                if len(table.cells) > self.package.limits.max_blocks:
                    raise XlsxParseError("Cell count exceeds limit")
                if text or child(element, "s:f") is not None:
                    block = self.new(TextBlock, "text", parent=cell.id,
                                     source={"cell_address": cell_address, "row": cell_row, "column": column},
                                     text=text, runs=runs, metadata=metadata)
                    text_blocks[(cell_row, column)] = block
                    cell.block_ids.append(block.id)
                table.rows = max(table.rows, cell_row + 1)
                table.columns = max(table.columns, column + 1)
        merges = child(root, "s:mergeCells")
        ranges = []
        if merges is not None:
            for merge in merges:
                ref = merge.get("ref")
                top, left, bottom, right = bounds(ref)
                # ponytail: bounded quadratic overlap check; use a sweep line for thousands of merges.
                if len(ranges) >= 1000:
                    raise XlsxParseError("Merged range count exceeds 1000")
                if any(top <= b and bottom >= t and left <= r and right >= l for t, l, b, r in ranges):
                    raise XlsxParseError("Overlapping merged ranges")
                ranges.append((top, left, bottom, right))
                table.metadata["merged_ranges"].append(ref)
                cell = cells.get((top, left))
                if cell is None:
                    if len(table.cells) >= self.package.limits.max_blocks:
                        raise XlsxParseError("Cell count exceeds limit")
                    cell = Cell(f"{table.id}/{address(top, left)}", top, left,
                                metadata={"address": address(top, left), "implicit_merge_origin": True})
                    cells[(top, left)] = cell
                    table.cells.append(cell)
                cell.row_span, cell.col_span = bottom - top + 1, right - left + 1
                table.rows, table.columns = max(table.rows, bottom + 1), max(table.columns, right + 1)
                for position, covered in cells.items():
                    self.package.check()
                    if position != (top, left) and top <= position[0] <= bottom and left <= position[1] <= right:
                        covered.metadata["merged_into"] = cell.id
                        if covered.block_ids:
                            self.diagnostic("merged_covered_cell_has_content", self.file, address=covered.metadata["address"])
        table.cells.sort(key=lambda c: (c.row, c.column))
        self.links_and_comments(root, relations, cells, text_blocks)
        for element in root:
            name = tag(element)
            if name == "s:tableParts":
                for item in element:
                    relation = relations.get(attr(item))
                    if not self.package.is_relation(relation, "table") or relation["external"]:
                        raise XlsxParseError("Invalid structured table relationship")
                    definition = self.package.xml(relation["path"])
                    if tag(definition) != "s:table":
                        raise XlsxParseError("Invalid structured table root")
                    bounds(definition.get("ref"))
                    table.metadata["structured_tables"].append({"file": relation["path"], "xml": record(definition)})
            elif name == "s:drawing":
                self.drawing(element, relations, table.id)
            elif name in {"s:legacyDrawing", "s:legacyDrawingHF", "s:oleObjects", "s:controls", "s:extLst", "s:pivotTableParts"}:
                self.diagnostic("unsupported_sheet_feature", self.file, xml=record(element))
            elif name not in {"s:sheetData", "s:mergeCells", "s:hyperlinks"} and name not in self.section["properties"]:
                self.section["properties"][name] = record(element)

    def links_and_comments(self, root, relations, cells, blocks):
        hyperlinks = child(root, "s:hyperlinks")
        if hyperlinks is not None:
            for link in hyperlinks:
                rectangle = bounds(link.get("ref"))
                relation = relations.get(attr(link)) if attr(link) else None
                if attr(link) and not self.package.is_relation(relation, "hyperlink"):
                    raise XlsxParseError("Invalid hyperlink relationship")
                value = {"attributes": dict(link.attrib), "relationship": relation}
                self.section.setdefault("hyperlinks", []).append(value)
                top, left, bottom, right = rectangle
                for position, cell in cells.items():
                    self.package.check()
                    if top <= position[0] <= bottom and left <= position[1] <= right:
                        cell.metadata.setdefault("hyperlinks", []).append(value)
                        if position in blocks:
                            blocks[position].metadata.setdefault("hyperlinks", []).append(value)
        comments_file = self.part(relations, "comments")
        if comments_file:
            comments = self.package.xml(comments_file)
            if tag(comments) != "s:comments":
                raise XlsxParseError("Invalid comments root")
            self.section["comments"] = record(comments)
            authors = child(comments, "s:authors")
            authors = [e.text or "" for e in authors] if authors is not None else []
            items = child(comments, "s:commentList")
            for item in items if items is not None else []:
                position = coordinates(item.get("ref"))
                author = integer(item.get("authorId"), "comment author")
                if not 0 <= author < len(authors):
                    raise XlsxParseError("Comment author index out of range")
                text, runs = rich_text(child(item, "s:text"))
                self.new(TextBlock, "text", source={"file": comments_file, "cell_address": item.get("ref"),
                                                    "comment": True}, text=text, runs=runs,
                         metadata={"author": authors[author], "comment": record(item)})
                if position in cells:
                    cells[position].metadata.setdefault("comments", []).append(record(item))

    def drawing(self, element, relations, parent):
        relation = relations.get(attr(element))
        if not self.package.is_relation(relation, "drawing") or relation["external"]:
            raise XlsxParseError("Invalid drawing relationship")
        drawing_file = relation["path"]
        root = self.package.xml(drawing_file)
        if tag(root) != "xdr:wsDr":
            raise XlsxParseError("Invalid spreadsheet drawing root")
        drawing_relations = self.package.relationships(drawing_file)
        for index, anchor in enumerate(root):
            pictures = [e for e in anchor.iter() if tag(e) == "xdr:pic"]
            # Shared by the anchor's pictures (see DocxParser.drawing): one copy, not one per picture.
            anchor_record = record(anchor) if pictures else None
            for picture_index, picture in enumerate(pictures):
                blip = next((e for e in picture.iter() if tag(e) == "a:blip"), None)
                rid = attr(blip, "embed") or attr(blip, "link")
                image_relation = drawing_relations.get(rid)
                block = self.new(ImageBlock, "image", parent=parent,
                                 source={"file": drawing_file, "xml_path": f"anchor/{index}/pic/{picture_index}"},
                                 metadata={"anchor": anchor_record, "relationship": image_relation})
                if not self.package.is_relation(image_relation, "image"):
                    self.diagnostic("missing_image_reference", block.source_ref)
                    continue
                key = image_relation["path"] or (drawing_file, rid)
                asset = self.assets.get(key)
                if asset is None:
                    data = None
                    if image_relation["external"]:
                        self.diagnostic("external_image", block.source_ref, target=image_relation["target"])
                    elif image_relation["path"] not in self.package.entries:
                        self.diagnostic("missing_asset", block.source_ref, path=image_relation["path"])
                    else:
                        data = self.package.read(image_relation["path"])
                    ident = "asset-" + hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:24]
                    asset = {"id": ident, "href": image_relation["target"], "path": image_relation["path"],
                             "media_type": self.package.content_type(image_relation["path"]) if image_relation["path"] else "image/external",
                             "size_bytes": len(data) if data is not None else None,
                             "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                             "used_by": [], "output_path": None}
                    if data is not None:
                        self.document._asset_bytes[ident] = data
                    self.assets[key] = asset
                    self.document.assets.append(asset)
                block.asset_id = asset["id"]
                asset["used_by"].append(block.id)
            if any(tag(e) in {"a:graphicData", "xdr:sp", "xdr:cxnSp"} for e in anchor.iter()):
                self.diagnostic("unrendered_drawing", drawing_file, xml=record(anchor))
