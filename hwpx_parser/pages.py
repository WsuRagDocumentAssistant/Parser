"""Conservative saved-layout estimates. This module does not typeset documents."""
from collections import Counter, defaultdict

from .models import Block, Document, ImageBlock, PageLocation, TableBlock, TextBlock


def number(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def property_attrs(block, name):
    return block.metadata.get("properties", {}).get(name, {}).get("attributes", {})


def descendants(records):
    stack = list(records)
    while stack:
        item = stack.pop()
        yield item
        stack.extend(item.get("children", []))


class PageResolver:
    def __init__(self, mode: str = "layout") -> None:
        self.mode = mode

    def resolve(self, document: Document) -> None:
        if self.mode == "auto":
            LayoutEstimator(document).resolve()
            return
        if self.mode == "structural":
            for block in document.blocks:
                block.location.reason = "Page inference disabled; source layout is preserved in metadata."
            document.metadata["page_mode"] = self.mode
            return
        lookup = {b.id: b for b in document.blocks}
        cell_parents = {cell.id: b for b in document.blocks if isinstance(b, TableBlock) for cell in b.cells}
        children = defaultdict(list)
        for block in document.blocks:
            children[block.parent_id].append(block)
        document.metadata["page_mode"] = self.mode
        document.metadata["page_inference_assumptions"] = [
            "Saved layout matches the document content (not independently verifiable).",
            "Only the first, single-column horizontal section has a known physical start.",
            "No physical page is marked verified; missing layout or unbounded objects break continuity.",
        ]
        for section in document.sections:
            items = [lookup[i] for i in section["block_ids"]]
            props = list(descendants(section["properties"]))
            paper = next((p["attributes"] for p in props if p["tag"] == "pagePr"), {})
            margin = next((p["attributes"] for p in props if p["tag"] == "margin"), {})
            page_height = number(paper.get("height"))
            # WIDELY = portrait, NARROWLY = landscape in the supported 2011 schema.
            if paper.get("landscape") == "NARROWLY":
                page_height = number(paper.get("width"))
            body_height = None
            if page_height is not None and all(number(margin.get(k)) is not None for k in ("top", "bottom", "header", "footer")):
                body_height = page_height - sum(number(margin[k], 0) for k in ("top", "bottom", "header", "footer"))
            reason = None
            if section["index"] != 0:
                reason = "Physical offset of subsequent sections cannot be established without pagination."
            if any(number(c.get("colCount"), 1) != 1 for c in section["columns"]):
                reason = "Multi-column layout requires pagination; vertical resets are ambiguous."
            if any(p["attributes"].get("textDirection", "HORIZONTAL") != "HORIZONTAL" for p in props if p["tag"] == "secPr"):
                reason = "Vertical text layout is not supported for page inference."
            page, previous_y = 1, None
            for block in items:
                if block.parent_id is None and isinstance(block, TextBlock):
                    if reason:
                        block.location.reason = reason
                        continue
                    lines = block.metadata.get("linesegments", [])
                    if not lines or any(number(s.get("vertpos")) is None or number(s.get("textpos")) is None for s in lines):
                        reason = "Missing or invalid line layout; subsequent physical pages are unknown."
                        block.location.reason = reason
                        continue
                    attrs = block.metadata["paragraph_attributes"]
                    if attrs.get("columnBreak") == "1":
                        reason = "Column break prevents reliable page continuity."
                        block.location.reason = reason
                        continue
                    explicit = attrs.get("pageBreak") == "1" and previous_y is not None
                    if explicit:
                        page += 1
                        previous_y = None
                    fragments = []
                    pages = []
                    bad_layout = False
                    for index, segment in enumerate(lines):
                        y, offset = number(segment["vertpos"]), number(segment["textpos"])
                        height = number(segment.get("vertsize"), 0)
                        if y < 0 or offset < 0 or height < 0 or (body_height is not None and y + height > body_height):
                            bad_layout = True
                            break
                        if previous_y is not None and y < previous_y:
                            page += 1
                        if page not in pages:
                            pages.append(page)
                        fragments.append({"physical_page": page, "line_index": index, "source_textpos": offset,
                                          "source_vertpos": y, "offset_unit": "source_native"})
                        previous_y = y
                    if bad_layout:
                        reason = "Line geometry is outside the body area; pagination is ambiguous."
                        block.location.reason = reason
                        continue
                    location = PageLocation(pages, "estimated", "saved_single_column_layout",
                                            "Assumes current single-column cached layout; no rendering verification.",
                                            anchor_page=pages[0], fragments=fragments)
                    # A floating or split object can occupy unseen pages between body lines.
                    for obj in children.get(block.id, []):
                        if obj.kind not in {"table", "image", "container"} or obj.scope in {"header", "footer"}:
                            continue
                        if not self.fits_inline(obj, len(pages), max(number(s["vertpos"]) for s in lines), body_height):
                            reason = "Floating/split/unbounded object prevents complete paragraph and subsequent page inference."
                            location = PageLocation(reason=reason, anchor_page=pages[0])
                            break
                    block.location = location
                else:
                    parent = lookup.get(block.parent_id) or cell_parents.get(block.parent_id)
                    if parent is None:
                        continue
                    source_location = parent.location
                    anchor = source_location.anchor_page
                    if block.scope in {"header", "footer", "footnote", "endnote", "caption"}:
                        block.location = PageLocation(reason="Repeated/note/caption display requires pagination.", anchor_page=anchor)
                    elif source_location.physical_pages is not None and len(source_location.physical_pages) == 1:
                        is_cell_text = isinstance(block, TextBlock) and block.parent_id in cell_parents
                        simple_object = isinstance(block, (ImageBlock, TableBlock)) and property_attrs(block, "pos").get("treatAsChar") == "1"
                        if is_cell_text or simple_object:
                            block.location = PageLocation(list(source_location.physical_pages), "estimated", "single_page_parent",
                                                          "Inherited from bounded inline parent; not independently verified.", anchor_page=anchor)
                        else:
                            block.location = PageLocation(reason="Object display page differs potentially from its anchor.", anchor_page=anchor)
                    else:
                        block.location = PageLocation(reason=source_location.reason, anchor_page=anchor)
        counts = Counter(b.location.status for b in document.blocks)
        document.diagnostics.append({"severity": "info", "code": "page_inference_summary", "counts": dict(counts),
                                     "message": "Pure XML extraction never verifies pagination; estimates are explicitly labelled."})

    @staticmethod
    def fits_inline(block, page_count, y, body_height):
        if not isinstance(block, (TableBlock, ImageBlock)) or page_count != 1 or body_height is None:
            return False
        position, size = property_attrs(block, "pos"), property_attrs(block, "sz")
        height = number(size.get("height"))
        return (position.get("treatAsChar") == "1" and size.get("heightRelTo", "ABSOLUTE") == "ABSOLUTE"
                and height is not None and height >= 0 and y + height <= body_height)


class LayoutEstimator:
    """Best-effort geometry, deliberately separate from conservative cached layout.

    This is not a font/line-breaking engine. Child content on a split cell gets
    candidate pages, not a claim that every child occurs on every such page.
    """

    def __init__(self, document: Document) -> None:
        self.document = document
        self.blocks = {block.id: block for block in document.blocks}
        self.children = defaultdict(list)
        self.roots_by_section = defaultdict(list)
        self.repeated_by_section = defaultdict(list)
        for block in document.blocks:
            self.children[block.parent_id].append(block)
            section_index = block.source_ref["section_index"]
            if block.parent_id is None:
                self.roots_by_section[section_index].append(block)
            if block.kind == "container" and block.metadata.get("element") in {"header", "footer"}:
                self.repeated_by_section[section_index].append(block)
        self.height = 0
        self.page_references = 0

    def location(self, start: int, end: int, method: str, **evidence) -> PageLocation:
        count = end - start + 1
        if start < 1 or end > 10_000 or not 1 <= count <= 1000 or self.page_references + count > 300_000:
            return PageLocation(reason="Estimated page range exceeds allocation limits.")
        self.page_references += count
        return PageLocation(
            physical_pages=list(range(start, end + 1)), status="estimated", method=method,
            reason="Heuristic geometry and section offsets; not independently paginated.",
            anchor_page=start,
            evidence={"confidence": "low", "coverage": "estimated_span", **evidence},
        )

    def resolve(self) -> None:
        document = self.document
        document.metadata["page_mode"] = "auto"
        document.metadata["page_inference_assumptions"] = [
            "Saved line and object geometry is current; fonts are not reflowed.",
            "Sections start on successive pages; odd/even start settings are applied.",
            "Rows that fit a page stay together; oversized rows split geometrically.",
            "Split-cell, repeated and ambiguously anchored content uses candidate pages.",
            "All auto page numbers have low confidence; offset errors can propagate.",
        ]
        next_page = 1
        for section in document.sections:
            properties = list(descendants(section["properties"]))
            paper = next((p["attributes"] for p in properties if p["tag"] == "pagePr"), {})
            margins = next((p["attributes"] for p in properties if p["tag"] == "margin"), {})
            dimension = "width" if paper.get("landscape") == "NARROWLY" else "height"
            self.height = number(paper.get(dimension), 0) - sum(
                number(margins.get(key), 0) for key in ("top", "bottom", "header", "footer")
            )
            roots = self.roots_by_section[section["index"]]
            unsupported = (
                self.height <= 0
                or any(number(c.get("colCount"), 1) != 1 for c in section["columns"])
                or any(p["attributes"].get("textDirection", "HORIZONTAL") != "HORIZONTAL"
                       for p in properties if p["tag"] == "secPr")
            )
            if unsupported or next_page is None:
                section["estimated_pages"] = None
                for block_id in section["block_ids"]:
                    self.blocks[block_id].location.reason = (
                        "Unsupported page geometry/columns/direction or unknown preceding section offset."
                    )
                next_page = None
                continue
            start_number = next((p["attributes"] for p in properties if p["tag"] == "startNum"), {})
            parity = {"ODD": 1, "EVEN": 0}.get(start_number.get("pageStartsOn"))
            if parity is not None and next_page % 2 != parity:
                next_page += 1
            start_page = page = next_page
            previous_y = None
            for index, block in enumerate(roots):
                if not isinstance(block, TextBlock):
                    page = max(page, self.place_object(block, page, 0))
                    continue
                lines = block.metadata.get("linesegments", [])
                valid = [line for line in lines if number(line.get("vertpos")) is not None
                         and number(line.get("vertpos")) >= 0]
                explicit = index > 0 and block.metadata.get("paragraph_attributes", {}).get("pageBreak") == "1"
                if explicit:
                    page += 1
                    previous_y = None
                first_y = number(valid[0]["vertpos"]) if valid else 0
                first_page = page
                fragments = []
                for line_index, line in enumerate(valid):
                    y = number(line["vertpos"])
                    if previous_y is not None and y < previous_y:
                        page += 1
                    fragments.append({"physical_page": page, "line_index": line_index,
                                      "source_vertpos": y, "source_textpos": number(line.get("textpos")),
                                      "offset_unit": "source_native"})
                    previous_y = y
                    if line_index == 0:
                        first_page = page
                block.location = self.location(first_page, page, "auto_saved_lines",
                                               layout_missing=len(valid) != len(lines) or not lines)
                block.location.fragments = fragments
                if not valid:
                    block.location.evidence["coverage"] = "candidate"
                text_end = page
                object_end = page
                for child in self.children[block.id]:
                    if child.scope in {"header", "footer"}:
                        continue
                    end = self.place_object(child, first_page, first_y)
                    if len(valid) > 1:
                        child.location.evidence["anchor_assumption"] = "First cached line; object/native text offsets are not mapped."
                    position = property_attrs(child, "pos")
                    if position.get("treatAsChar") == "1" or position.get("flowWithText") == "1":
                        end_y = child.location.evidence.get("end_y", first_y)
                        if end > object_end:
                            previous_y = end_y
                        elif end == object_end:
                            previous_y = max(previous_y or 0, end_y)
                        object_end = max(object_end, end)
                page = max(text_end, object_end)
            section["estimated_pages"] = [start_page, page]
            for block in self.repeated_by_section[section["index"]]:
                self.inherit(block, self.location(start_page, page, "auto_repeated_region", coverage="candidate"))
            next_page = page + 1 if page <= 10_000 else None
        document.metadata["estimated_page_count"] = next_page - 1 if next_page is not None else None
        document.diagnostics.append({
            "severity": "info", "code": "page_inference_summary",
            "counts": dict(Counter(b.location.status for b in document.blocks)),
            "message": "Auto pages are low-confidence geometry estimates, including candidate ranges; not verified.",
        })

    def inherit(self, block: Block, location: PageLocation) -> None:
        pages = location.physical_pages
        block.location = self.location(
            pages[0], pages[-1], "auto_parent_range",
            **{**location.evidence, "coverage": "candidate", "parent_method": location.method},
        ) if pages else PageLocation(reason=location.reason)
        for child in self.children[block.id]:
            self.inherit(child, block.location)
        if isinstance(block, TableBlock):
            for cell in block.cells:
                for child in self.children[cell.id]:
                    self.inherit(child, block.location)

    def place_object(self, block: Block, page: int, y: int) -> int:
        position = property_attrs(block, "pos")
        # Inline objects follow the text baseline; stored floating offsets can be
        # stale (including unsigned encodings of negative values).
        inline = position.get("treatAsChar") == "1"
        offset = 0 if inline else number(position.get("vertOffset"), 0)
        if offset >= 2**31:
            offset -= 2**32
        page_relative = not inline and position.get("vertRelTo") in {"PAGE", "PAPER"}
        y = max(0, (0 if page_relative else y) + offset)
        page += y // self.height
        y %= self.height
        if isinstance(block, TableBlock):
            return self.place_table(block, page, y)
        height = max(0, number(property_attrs(block, "sz").get("height"), 0))
        # Images/shapes are indivisible: overflow moves the object, not its pixels.
        if 0 < height <= self.height and y + height > self.height and position.get("treatAsChar") == "1":
            page += 1
            y = 0
        location = self.location(page, page, "auto_object_anchor", coverage="candidate", end_y=y + height)
        self.inherit(block, location)
        block.location.method = "auto_object_anchor"
        if height > self.height:
            block.location.evidence["oversized_object"] = True
        return page

    def place_table(self, table: TableBlock, page: int, y: int) -> int:
        heights = [0] * table.rows
        for cell in table.cells:
            height = max(0, number(cell.metadata.get("size", {}).get("height"), 0))
            if cell.row_span == 1:
                heights[cell.row] = max(heights[cell.row], height)
        for cell in table.cells:
            height = max(0, number(cell.metadata.get("size", {}).get("height"), 0))
            stop = cell.row + cell.row_span
            deficit = height - sum(heights[cell.row:stop])
            if deficit > 0:
                heights[stop - 1] += deficit
        total_height = sum(heights)
        if not total_height or any(height == 0 for height in heights):
            location = self.location(page, page, "auto_table_anchor", coverage="candidate", missing_row_geometry=True)
            self.inherit(table, location)
            return page
        split = table.metadata.get("attributes", {}).get("pageBreak") != "NONE"
        if not split:
            if total_height <= self.height and y + total_height > self.height:
                page += 1
                y = 0
            if total_height > self.height:
                self.inherit(table, self.location(page, page, "auto_table_anchor", coverage="candidate", oversized_unsplit_table=True))
                return page
        spacing = max(0, number(table.metadata.get("attributes", {}).get("cellSpacing"), 0))
        header_rows = {
            row for cell in table.cells if cell.metadata.get("attributes", {}).get("header") == "1"
            for row in range(cell.row, cell.row + cell.row_span)
        }
        header_height = sum(heights[row] for row in header_rows)
        if table.metadata.get("attributes", {}).get("repeatHeader") != "1":
            header_height = 0
        header_height = min(header_height, self.height - 1)
        row_pages = []
        for height in heights:
            if y and height <= self.height and y + height > self.height:
                page += 1
                y = header_height
            start = page
            remaining = max(0, height - (self.height - y))
            if remaining:
                usable = self.height - header_height
                extra_pages = (remaining + usable - 1) // usable
                page += extra_pages
                y = header_height + (remaining - 1) % usable + 1
            else:
                y += height
            row_pages.append((start, page))
            y += spacing
        table.location = self.location(row_pages[0][0], page, "auto_table_rows",
                                       repeat_header_height=header_height, end_y=y)
        table.location.fragments = [
            {"row": index, "page_start": start, "page_end": end}
            for index, (start, end) in enumerate(row_pages)
        ]
        for cell in table.cells:
            start = row_pages[cell.row][0]
            end = row_pages[cell.row + cell.row_span - 1][1]
            if header_height and cell.metadata.get("attributes", {}).get("header") == "1":
                end = page
            location = self.location(start, end, "auto_cell_rows", coverage="candidate")
            for child in self.children[cell.id]:
                self.inherit(child, location)
        for child in self.children[table.id]:
            self.inherit(child, table.location)
        return page
