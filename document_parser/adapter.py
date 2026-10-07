from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .models import (Block, Cell, DocumentModel, Figure, FileInfo, ImageFile,
                     ImageRef, Table, TableParent)


class ModelAdapter:
    """Map extracted structure to the contract read by RagModuls.chunk()."""

    def __init__(self, document):
        self.document = document
        self.blocks = {b.id: b for b in document.blocks}
        self.headings = {h.id: h for h in document.headings if h.id != "root"}
        self.children = defaultdict(list)
        self.cell_owners = {}
        for block in document.blocks:
            self.children[block.parent_id].append(block)
            if block.kind == "table":
                for cell in block.cells:
                    self.cell_owners[cell.id] = block.id
        self.in_cells = set()
        self.containing_cell = {}
        for block in document.blocks:
            parent = block.parent_id
            while parent in self.blocks:
                parent = self.blocks[parent].parent_id
            if parent in self.cell_owners:
                self.in_cells.add(block.id)
                self.containing_cell[block.id] = parent
        self.images = {}
        self.heading_ids = {
            h.id: h.block_id if h.block_id in self.blocks
            and self.blocks[h.block_id].kind == "text"
            and h.block_id not in self.in_cells
            else "heading:" + h.id
            for h in self.headings.values()
        }
        self.heading_by_block = {self.heading_ids[h.id]: h for h in self.headings.values()}

    def image_ref(self, asset_id):
        image = self.images.get(asset_id)
        return ImageRef(image.ref, image.path, image.media_type) if image else None

    @staticmethod
    def section(block):
        source = block.source_ref
        return source.get("section_index") or source.get("sheet_index") or max(0, (source.get("page") or 1) - 1)

    def table(self, block):
        cells, nested = [], []
        for cell in block.cells:
            members = [self.blocks[ident] for ident in cell.block_ids if ident in self.blocks]
            seen = {b.id for b in members}
            for member in members:
                if member.kind != "table":
                    for child in self.children[member.id]:
                        if child.id not in seen:
                            seen.add(child.id)
                            members.append(child)
            texts = [b.text for b in members if b.kind == "text"]
            child_tables = [b for b in members if b.kind == "table"]
            cells.append(Cell(cell.row, cell.column, cell.row_span, cell.col_span,
                              "\n".join(texts), texts,
                              [ref for b in members if b.kind == "image"
                               and (ref := self.image_ref(b.asset_id)) is not None],
                              [b.id for b in child_tables]))
            nested.extend(self.table(b) for b in child_tables)
        parent = None
        cell_id = self.containing_cell.get(block.id)
        if cell_id:
            parent = TableParent(self.cell_owners[cell_id], cell_id)
        # No semantic header/record inference: preserve cells and mark the verdict unknown.
        markdown = self.table_markdown(cells)
        # The existing chunker reads only the outer table's markdown, not children.
        for child in nested:
            if child.markdown:
                position = next(c for c in cells if child.id in c.child_tables)
                markdown = (markdown or "") + f"\n\n중첩 표 (행 {position.row + 1}, 열 {position.col + 1})\n" + child.markdown
        return Table(id=block.id, kind="데이터표", rows=block.rows,
                     cols=block.columns, numeric_verdict="판정불가", cells=cells,
                     markdown=markdown, parent=parent, children=nested)

    @staticmethod
    def table_markdown(cells):
        # ponytail: sparse address rows avoid expanding huge XLSX ranges; grid rendering if needed.
        if not cells:
            return None
        lines = ["| 행 | 열 | 행 병합 | 열 병합 | 내용 |", "| --- | --- | --- | --- | --- |"]
        for cell in cells:
            text = cell.text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")
            lines.append(f"| {cell.row + 1} | {cell.col + 1} | {cell.row_span} | {cell.col_span} | {text} |")
        return "\n".join(lines)

    def context(self, heading_id):
        heading = self.headings.get(heading_id)
        if heading is None:
            return [], [], 0
        path = [ident for ident in heading.path_ids if ident in self.headings]
        return ([self.heading_ids[ident] for ident in path],
                [self.headings[ident].title for ident in path], heading.depth)

    def file_info(self):
        metadata = self.document.metadata
        properties = {}
        # Read stored core/app property records; never reinterpret custom fields.
        records = [metadata.get('core-properties'), metadata.get('extended-properties')]
        stored = metadata.get('properties') or {}
        records.extend(stored.get(name) for name in ('docProps/core.xml', 'docProps/app.xml'))
        stack = [record for record in records if isinstance(record, dict)]
        while stack:
            record = stack.pop()
            name = record.get('tag', '').rsplit('}', 1)[-1].rsplit(':', 1)[-1]
            if record.get('text'):
                properties[name] = record['text']
            stack.extend(record.get('children', []))
        properties.update({key: value for key, value in metadata.items() if isinstance(value, str)})
        if metadata.get('format') == 'pdf':
            aliases = {'/Title': 'title', '/Author': 'creator', '/Creator': 'Application',
                       '/CreationDate': 'created', '/ModDate': 'modified'}
            properties.update({name: stored[key] for key, name in aliases.items() if key in stored})
        return FileInfo(
            title=properties.get('title') or metadata['filename'], filename=metadata['filename'],
            creator=properties.get('creator'), last_saved_by=properties.get('lastModifiedBy'),
            created_at=properties.get('created'), modified_at=properties.get('modified'),
            language=properties.get('language'), application=properties.get('Application'),
            app_version=properties.get('AppVersion'), section_count=len(self.document.sections),
            table_count=sum(b.kind == 'table' for b in self.document.blocks))

    def build(self, image_dir=None):
        for asset in self.document.assets:
            if asset["media_type"].startswith("image/"):
                self.images[asset["id"]] = ImageFile(
                    asset["id"], asset.get("output_path") or asset.get("path") or asset.get("href") or "",
                    asset["media_type"], asset.get("size_bytes"))
        model = DocumentModel(self.file_info(), self.images)
        emitted = set()
        for source in self.document.blocks:
            # Cell text/images/nested tables are owned by Table.cells, not repeated in body.
            if source.id in self.in_cells:
                continue
            path, titles, depth = self.context(source.heading_id)
            for heading_block in path:
                ident = self.heading_by_block[heading_block].id
                heading = self.headings[ident]
                if ident in emitted or self.heading_ids[ident] == source.id:
                    continue
                heading_path, heading_titles, heading_depth = self.context(ident)
                model.blocks.append(Block(
                    id=self.heading_ids[ident], order=len(model.blocks), section=self.section(source),
                    depth=max(0, heading_depth - 1), area="본문", kind="문단", role="제목",
                    searchable=True, text=heading.title, heading_path=heading_path,
                    heading_path_text=heading_titles,
                    child_headings=[self.heading_ids[c] for c in heading.children if c in self.heading_ids]))
                emitted.add(ident)
            heading = self.heading_by_block.get(source.id)
            area = "주변부" if source.scope in {"header", "footer", "footnote", "endnote"} else "본문"
            block = Block(source.id, len(model.blocks), self.section(source),
                          max(0, depth - 1) if heading else depth, area,
                          {"text": "문단", "table": "표", "image": "이미지"}.get(source.kind, source.kind),
                          "제목" if heading else {"text": "본문", "table": "표", "image": "그림"}.get(source.kind, "문서컨트롤"),
                          heading_path=path, heading_path_text=titles)
            if source.kind == "text":
                block.text = heading.title if heading else source.text
                if not block.text.strip():
                    block.role = "빈문단"
                elif source.scope == "caption":
                    block.role = "캡션"
                block.searchable = area == "본문" and bool(block.text.strip())
            elif source.kind == "table":
                block.table = self.table(source)
                block.searchable = area == "본문" and bool(block.table.markdown)
            elif source.kind == "image":
                block.figure = Figure(shape="이미지", image=self.image_ref(source.asset_id))
            if heading:
                block.child_headings = [self.heading_ids[c] for c in heading.children if c in self.heading_ids]
                emitted.add(heading.id)
            model.blocks.append(block)
        # Rich evidence remains available without adding fields to the organization's JSON schema.
        model.structure = self.document
        model.document_images = []
        if image_dir is not None:
            self.save_images(model, Path(image_dir))
        return model

    def save_images(self, model, directory):
        from .models import DocumentImage
        model.document_images = []
        # Hash separates files with the same stem and different source bytes/formats.
        root = directory / self.document.metadata["sha256"]
        from hwpx_parser.exporter import DocumentExporter
        DocumentExporter(self.document).export_images(root)
        for asset in self.document.assets:
            if asset["id"] in model.images and asset.get("output_path"):
                model.images[asset["id"]].path = str((root / asset["output_path"]).resolve())
        seen = set()
        for index, block in enumerate(model.blocks):
            image = block.figure.image if block.figure else None
            if image is None:
                continue
            saved = model.images[image.ref]
            image.path = saved.path
            if image.ref in seen or image.ref not in self.document._asset_bytes:
                continue
            seen.add(image.ref)
            following = model.blocks[index + 1:index + 4]
            caption = None
            for candidate in following:
                if candidate.role == "캡션":
                    caption = candidate.text
                    break
                if candidate.role != "빈문단":
                    break
            model.document_images.append(DocumentImage(image.ref, saved.path, block.order,
                block.section, image.media_type, list(block.heading_path_text), caption))
        # Cell references use the same saved paths, including images inside nested tables.
        for table in model.tables():
            for cell in table.cells:
                for image in cell.images:
                    image.path = model.images[image.ref].path
