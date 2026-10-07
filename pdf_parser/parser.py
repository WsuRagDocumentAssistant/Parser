from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from io import BytesIO
import math
from pathlib import Path
import time

import pdfplumber
from pypdf import PdfReader

from .models import Cell, Document, HeadingNode, ImageBlock, PageLocation, TableBlock, TextBlock


class PdfParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParseLimits:
    max_file_bytes: int = 100 * 1024 * 1024
    max_pages: int = 2000
    max_chars: int = 2_000_000
    max_page_objects: int = 100_000
    max_blocks: int = 100_000
    max_cells: int = 100_000
    max_image_pixels: int = 40_000_000
    max_total_asset_bytes: int = 256 * 1024 * 1024
    max_headings: int = 10_000
    max_depth: int = 96
    timeout_seconds: float = 60

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in vars(self).values()):
            raise ValueError("All parse limits must be finite and positive")
        if any(not isinstance(v, int) or isinstance(v, bool)
               for k, v in vars(self).items() if k != "timeout_seconds"):
            raise ValueError("Count and byte limits must be integers")


def bbox(item):
    return [float(item[k]) for k in ("x0", "top", "x1", "bottom")]


def contains(box, char):
    x, y = (char["x0"] + char["x1"]) / 2, (char["top"] + char["bottom"]) / 2
    return box[0] <= x < box[2] and box[1] <= y < box[3]


class PdfParser:
    """Reusable configuration; each parse owns a separate reader and document."""
    def __init__(self, limits=None, *, auto_headings=True, table_strategy="lines", ocr=None):
        if table_strategy not in {"lines", "text", "none"}:
            raise ValueError("table_strategy must be lines, text or none")
        self.limits = limits or ParseLimits()
        self.auto_headings = auto_headings
        self.table_strategy = table_strategy
        self.ocr = ocr

    def parse(self, path: str | Path, *, password: str | None = None) -> Document:
        source = Path(path)
        with source.open("rb") as stream:
            data = stream.read(self.limits.max_file_bytes + 1)
        if len(data) > self.limits.max_file_bytes:
            raise PdfParseError("Input file exceeds size limit")
        if b"%PDF-" not in data[:1024]:
            raise PdfParseError("Not a PDF file")
        try:
            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted and not reader.decrypt(password or ""):
                raise PdfParseError("PDF password is required or incorrect")
            if not 0 < len(reader.pages) <= self.limits.max_pages:
                raise PdfParseError("PDF page count is empty or exceeds limit")
            with pdfplumber.open(BytesIO(data), password=password or "") as pdf:
                worker = _Reader(self, reader, source.name, hashlib.sha256(data).hexdigest())
                return worker.read(pdf)
        except (OSError, PdfParseError):
            raise
        except Exception as exc:
            # Library exceptions vary with codec/encryption backend; keep the public error stable.
            raise PdfParseError(f"Cannot parse PDF ({type(exc).__name__}): {exc}") from exc


class _Reader:
    def __init__(self, parser, reader, filename, digest):
        self.parser, self.reader, self.limits = parser, reader, parser.limits
        self.deadline = time.monotonic() + self.limits.timeout_seconds
        self.document = Document({"filename": filename, "sha256": digest, "format": "pdf",
                                  "parser_version": "0.1.0", "page_count": len(reader.pages),
                                  "page_mode": "physical", "coordinate_system": "top-left, PDF points",
                                  "table_strategy": parser.table_strategy,
                                  "properties": {str(k): str(v) for k, v in (reader.metadata or {}).items()}})
        self.char_count = self.cell_count = self.asset_bytes = 0
        self.font_sizes = Counter()
        self.asset_refs = {}
        self.labels = reader.page_labels

    def check(self):
        if time.monotonic() > self.deadline:
            raise PdfParseError("Parse time limit exceeded")

    def diagnostic(self, code, **details):
        self.document.diagnostics.append({"severity": "warning", "code": code, **details})

    def new(self, cls, kind, page, box, *, parent_id=None, scope="body", **values):
        self.check()
        order = len(self.document.blocks)
        if order >= self.limits.max_blocks:
            raise PdfParseError("Block count exceeds limit")
        block = cls(id=f"b{order + 1}", kind=kind, order=order, scope=scope,
                    parent_id=parent_id, source_ref={"page": page.page_number, "bbox": list(box)},
                    location=PageLocation(physical_pages=[page.page_number], status="verified",
                                          method="pdf_page_tree", reason=None,
                                          printed_page_labels=[self.labels[page.page_number - 1]],
                                          evidence={"bbox": list(box)}), **values)
        self.document.blocks.append(block)
        return block

    def text(self, page, line, *, parent_id=None, scope="body", text=None):
        chars = line.get("chars", [])
        sizes = [round(float(c["size"]), 2) for c in chars if c["text"].strip()]
        return self.new(TextBlock, "text", page, bbox(line), parent_id=parent_id, scope=scope,
                        text=line["text"] if text is None else text,
                        runs=[{"text": c["text"], "font": c["fontname"], "size": float(c["size"]),
                               "bbox": bbox(c), "upright": c["upright"]} for c in chars],
                        metadata={"font_size": max(sizes, default=0), "extraction": "positioned_text"})

    def read(self, pdf):
        if len(pdf.pages) != len(self.reader.pages):
            raise PdfParseError("PDF backends disagree on page count")
        labels = self.labels
        if len(labels) != len(pdf.pages):
            raise PdfParseError("Invalid page label count")
        for page in pdf.pages:
            self.check()
            chars = page.chars
            self.char_count += len(chars)
            if self.char_count > self.limits.max_chars:
                raise PdfParseError("Character count exceeds limit")
            if sum(len(v) for v in page.objects.values()) > self.limits.max_page_objects:
                raise PdfParseError("Page object count exceeds limit")
            self.font_sizes.update(round(float(c["size"]), 2) for c in chars if c["text"].strip())
            start = len(self.document.blocks)
            section = {"index": page.page_number - 1, "physical_page": page.page_number,
                       "printed_page_label": labels[page.page_number - 1], "width": page.width,
                       "height": page.height, "rotation": page.rotation, "bbox": list(page.bbox)}
            self.document.sections.append(section)
            has_text = any(c["text"].strip() for c in chars)
            tables = [] if self.parser.table_strategy == "none" else page.find_tables({
                "vertical_strategy": self.parser.table_strategy, "horizontal_strategy": self.parser.table_strategy})
            cell_boxes = []
            for table in tables:
                self.table(page, table)
                cell_boxes.extend(table.cells)
            # ponytail: O(chars * cells) filtering; spatial index if large tables dominate runtime.
            remaining = page.filter(lambda obj: obj.get("object_type") != "char"
                                    or not any(contains(box, obj) for box in cell_boxes))
            for line in remaining.extract_text_lines():
                self.text(page, line)
            self.images(page)
            if not has_text:
                if self.parser.ocr is not None and (page.images or page.curves or page.lines or page.rects):
                    self.ocr_page(page, section)
                elif page.images:
                    self.diagnostic("ocr_required", page=page.page_number,
                                    message="Image page has no extractable text; OCR was not performed.")
                elif page.curves or page.lines or page.rects:
                    self.diagnostic("no_text_layer", page=page.page_number,
                                    message="Vector-only page; text recognition was not performed.")
            if any("(cid:" in c["text"] or "\ufffd" in c["text"] for c in chars):
                self.diagnostic("unmapped_glyphs", page=page.page_number)
            if page.annots:
                self.diagnostic("annotations_not_extracted", page=page.page_number, count=len(page.annots))
            section["block_ids"] = [b.id for b in self.document.blocks[start:]]
            section["has_text_layer"] = has_text
            page.close()
        # Stable physical-page / top / left ordering, retaining cell parent references.
        self.document.blocks.sort(key=lambda b: (b.source_ref["page"], b.source_ref["bbox"][1],
                                                 b.source_ref["bbox"][0], b.order))
        for order, block in enumerate(self.document.blocks):
            block.order = order
        for section in self.document.sections:
            section["block_ids"] = [b.id for b in self.document.blocks
                                    if b.source_ref["page"] == section["physical_page"]]
        self.headings()
        self.check()
        return self.document

    def ocr_page(self, page, section):
        engine = self.parser.ocr
        self.check()
        if math.ceil(page.width * engine.dpi / 72) * math.ceil(page.height * engine.dpi / 72) > self.limits.max_image_pixels:
            raise PdfParseError("OCR page raster pixels exceed limit")
        # Render the full MediaBox so coordinates stay aligned with pdfplumber.
        image = page.to_image(resolution=engine.dpi, force_mediabox=True).original
        try:
            if image.width * image.height > self.limits.max_image_pixels:
                raise PdfParseError("OCR page raster pixels exceed limit")
            lines = engine.recognize(image)
            self.check()
            sx, sy = page.width / image.width, page.height / image.height
            for line in lines:
                self.char_count += len(line["text"])
                if self.char_count > self.limits.max_chars:
                    raise PdfParseError("OCR character count exceeds limit")
                polygon = [[page.bbox[0] + x * sx, page.bbox[1] + y * sy] for x, y in line["polygon"]]
                box = [min(p[0] for p in polygon), min(p[1] for p in polygon),
                       max(p[0] for p in polygon), max(p[1] for p in polygon)]
                block = self.new(TextBlock, "text", page, box, text=line["text"],
                                 metadata={"extraction": "paddleocr", "font_size": 0,
                                           "confidence": line["confidence"], "polygon": polygon,
                                           "recognition_model": engine.recognition_model,
                                           "detection_model": engine.detection_model, "dpi": engine.dpi})
                block.source_ref["ocr"] = True
        finally:
            image.close()
        section["ocr"] = {"engine": "paddleocr", "recognition_model": engine.recognition_model,
                          "detection_model": engine.detection_model, "dpi": engine.dpi, "line_count": len(lines)}
        self.diagnostic("ocr_text_unverified" if lines else "ocr_no_text", page=page.page_number,
                        message="OCR text is probabilistic; original font and table semantics are not recovered.")

    def table(self, page, table):
        xs = sorted({x for box in table.cells for x in (box[0], box[2])})
        ys = sorted({y for box in table.cells for y in (box[1], box[3])})
        self.cell_count += len(table.cells)
        if self.cell_count > self.limits.max_cells or (len(xs) - 1) * (len(ys) - 1) > 1_000_000:
            raise PdfParseError("Table cell/grid count exceeds limit")
        block = self.new(TableBlock, "table", page, table.bbox, rows=len(ys) - 1, columns=len(xs) - 1,
                         metadata={"structure_status": "estimated", "method": "pdfplumber_geometry"})
        self.diagnostic("table_structure_estimated", block_id=block.id, page=page.page_number)
        for box in sorted(table.cells, key=lambda b: (b[1], b[0])):
            row, column = ys.index(box[1]), xs.index(box[0])
            cell = Cell(f"{block.id}/cell/{row}/{column}", row, column,
                        row_span=ys.index(box[3]) - row, col_span=xs.index(box[2]) - column,
                        metadata={"bbox": list(box), "span_status": "estimated"})
            block.cells.append(cell)
            selected = page.filter(lambda obj: obj.get("object_type") != "char" or contains(box, obj))
            for line in selected.extract_text_lines():
                child = self.text(page, line, parent_id=cell.id, scope="cell")
                cell.block_ids.append(child.id)

    def images(self, page):
        displayed = {getattr(im["stream"], "objid", None) for im in page.images}
        assets = {}
        pdf_page = self.reader.pages[page.page_number - 1]
        if displayed - {None}:
            for key in pdf_page.images.keys():
                self.check()
                if isinstance(key, str) and key.startswith("~"):
                    continue  # Inline images retain geometry below but are not decoded.
                obj = pdf_page
                for name in key if isinstance(key, (tuple, list)) else [key]:
                    obj = obj["/Resources"]["/XObject"][name]
                ref = getattr(obj, "indirect_reference", None)
                if ref is None or ref.idnum not in displayed:
                    continue
                if ref.idnum in self.asset_refs:
                    assets[ref.idnum] = self.asset_refs[ref.idnum]
                    continue
                width, height = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
                if width <= 0 or height <= 0 or width * height > self.limits.max_image_pixels:
                    raise PdfParseError("Image dimensions exceed limit or are invalid")
                try:
                    image = pdf_page.images[key]
                except Exception as exc:
                    self.diagnostic("image_decode_failed", page=page.page_number,
                                    object_id=ref.idnum, error=type(exc).__name__)
                    continue
                digest = hashlib.sha256(image.data).hexdigest()
                ident = "asset-" + digest
                if ident not in self.document._asset_bytes:
                    self.asset_bytes += len(image.data)
                    if self.asset_bytes > self.limits.max_total_asset_bytes:
                        raise PdfParseError("Decoded asset bytes exceed limit")
                    media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                             ".jp2": "image/jp2", ".tif": "image/tiff", ".tiff": "image/tiff"}.get(
                                 Path(image.name).suffix.lower(), "image/octet-stream")
                    self.document.assets.append({"id": ident, "sha256": digest, "media_type": media,
                                                 "width": width, "height": height, "size": len(image.data),
                                                 "source_object_id": ref.idnum, "encoding": "decoded_image"})
                    self.document._asset_bytes[ident] = image.data
                assets[ref.idnum] = self.asset_refs[ref.idnum] = ident
                if image.image is not None:
                    image.image.close()
        for image in page.images:
            ref = getattr(image["stream"], "objid", None)
            block = self.new(ImageBlock, "image", page, bbox(image), asset_id=assets.get(ref),
                             metadata={"object_id": ref, "name": image["name"],
                                       "source_size": list(image["srcsize"]), "imagemask": image["imagemask"]})
            if block.asset_id is None:
                self.diagnostic("image_asset_unavailable", block_id=block.id, page=page.page_number)

    def headings(self):
        root = HeadingNode("root", "", 0, 0, None, path_ids=["root"])
        self.document.headings.append(root)
        stack, targets = [root], []
        pending = [(item, 1) for item in reversed(self.reader.outline)]
        while pending:
            item, level = pending.pop()
            self.check()
            if level > self.limits.max_depth:
                raise PdfParseError("Outline depth exceeds limit")
            if isinstance(item, list):
                pending.extend((child, level + 1) for child in reversed(item))
                continue
            number = self.reader.get_destination_page_number(item)
            if number is None or not 0 <= number < len(self.reader.pages):
                self.diagnostic("outline_destination_unresolved", title=str(item.title))
                continue
            title = str(item.title)
            match = next((b for b in self.document.blocks if b.kind == "text" and b.scope == "body"
                          and b.source_ref["page"] == number + 1
                          and " ".join(b.text.split()) == " ".join(title.split())), None)
            targets.append((level, title, "pdf_outline", match, number + 1))
        if not targets and self.parser.auto_headings and self.font_sizes:
            body = self.font_sizes.most_common(1)[0][0]
            candidates = [b for b in self.document.blocks if b.kind == "text" and b.scope == "body"
                          and 0 < len(b.text.strip()) <= 120 and b.metadata["font_size"] >= body * 1.2]
            sizes = sorted({b.metadata["font_size"] for b in candidates}, reverse=True)
            targets = [(sizes.index(b.metadata["font_size"]) + 1, b.text, "font_size_heuristic", b,
                        b.source_ref["page"]) for b in candidates]
            if targets:
                self.diagnostic("heading_structure_estimated", method="font_size", body_font_size=body)
        events = []
        for level, title, source, block, page in targets:
            if len(self.document.headings) > self.limits.max_headings:
                raise PdfParseError("Heading count exceeds limit")
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1]
            node = HeadingNode(f"h{len(self.document.headings)}", title, level, parent.depth + 1, parent.id,
                               block_id=block.id if block else None, raw_level=level, source=source,
                               evidence={"physical_page": page, "scope_assignment": "estimated",
                                         "matched_text_block": block.id if block else None})
            node.path_ids = parent.path_ids + [node.id]
            node.path_titles = parent.path_titles + [title]
            parent.children.append(node.id)
            self.document.headings.append(node)
            stack.append(node)
            events.append(((page, block.source_ref["bbox"][1] if block else -math.inf), node.id))
        if targets:
            self.diagnostic("heading_scope_estimated", message="Heading membership uses page and matched-title position.")
        events.sort(key=lambda event: event[0])
        index, current = 0, "root"
        for block in self.document.blocks:
            position = (block.source_ref["page"], block.source_ref["bbox"][1])
            while index < len(events) and events[index][0] <= position:
                current = events[index][1]
                index += 1
            block.heading_id = current
        # Table contents belong to the same heading as their parent table.
        owners = {cell.id: table.heading_id for table in self.document.blocks if table.kind == "table"
                  for cell in table.cells}
        for block in self.document.blocks:
            if block.parent_id in owners:
                block.heading_id = owners[block.parent_id]
