from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .models import (Block, Cell, DocumentModel, Figure, FileInfo, ImageFile,
                     ImageRef, Table, TableParent, TableHeader, TableColumn, TableRecord)
from .table_hierarchy.orchestrator import add_hierarchy_recursive
from .semantics import numeric_verdict, restore_heading_paths
from hwpx_parser.headings import compact_title, is_title_text


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
        self._tables = {}
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
        if block.id in self._tables:
            return self._tables[block.id]
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
        raw_cells = [dict(cell_id=original.id, row_addr=cell.row, col_addr=cell.col,
                          row_span=cell.row_span, col_span=cell.col_span, text=cell.text,
                          images=cell.images, nested_tables=cell.child_tables)
                     for original, cell in zip(block.cells, cells)]
        raw_cells.sort(key=lambda cell: (cell['row_addr'], cell['col_addr']))
        raw = dict(table_id=block.id, row_count=block.rows, col_count=block.columns,
                   cells=raw_cells, is_nested=parent is not None,
                   repeat_header=block.metadata.get('attributes', {}).get('repeatHeader'))
        # ponytail: huge sparse XLSX ranges retain addresses; analyze after partitioning if needed.
        sparse = self.document.metadata.get('format') == 'xlsx' and block.rows * block.columns > 100_000
        if sparse:
            hierarchy = {'table_type': 'data_table'}
        else:
            if block.rows * block.columns > 1_000_000:
                raise ValueError('Table grid exceeds the semantic analysis limit')
            add_hierarchy_recursive(raw)
            hierarchy = raw['hierarchy']
        table_type = hierarchy['table_type']
        if table_type == 'title_box' and not is_title_text(' '.join(c.text for c in cells if c.text)):
            # Preserve the full cells/Markdown; a long sentence is content, not a boundary.
            table_type = hierarchy['table_type'] = 'data_table'
            hierarchy['title_cells'] = []
        header = None
        columns = hierarchy.get('columns') or []
        if columns or hierarchy.get('header_rows') or hierarchy.get('header_cols'):
            header = TableHeader(hierarchy.get('header_rows') or [], hierarchy.get('header_cols') or [],
                                 [TableColumn(c['col_index'], c['name'], bool(c.get('is_row_header'))) for c in columns])
        records = []
        for record in hierarchy.get('structured_records') or []:
            values = {**record.get('row_headers', {}), **record.get('values', {})}
            inherited = []
            for key, value in values.items():
                if value:
                    continue
                column = next((c['col_index'] for c in columns if c['name'] == key.split('__')[0]), None)
                origin = next((c for c in cells if column is not None and
                               c.row <= record['source_row_addr'] < c.row + c.row_span and
                               c.col <= column < c.col + c.col_span and c.text), None)
                if origin:
                    values[key] = origin.text
                    inherited.append(key)
            records.append(TableRecord(record['row_index'], values, inherited))
        if not records:
            records = [TableRecord(r.get('row_addr') or 0, {r['key']: r.get('value') or ''})
                       for r in hierarchy.get('key_value_records') or [] if r.get('key')]
        verdict = numeric_verdict(cells, table_type, header)
        markdown = self.table_markdown(cells, block.rows, block.columns,
                                       header.header_rows if header else [], sparse=sparse)
        # The existing chunker reads only the outer table's markdown, not children.
        for child in nested:
            if child.markdown:
                position = next(c for c in cells if child.id in c.child_tables)
                markdown = (markdown or "") + f"\n\n중첩 표 (행 {position.row + 1}, 열 {position.col + 1})\n" + child.markdown
        kind = {'title_box': '제목상자', 'key_value_table': '키값표',
                'caption_or_note_table': '주석표', 'data_table': '데이터표'}[table_type]
        titles = [compact_title(c.text) for original, c in sorted(zip(block.cells, cells), key=lambda pair: (pair[1].row, pair[1].col))
                  if original.id in hierarchy.get('title_cells', []) and c.text]
        result = Table(id=block.id, kind=kind, rows=block.rows,
                     cols=block.columns, numeric_verdict=verdict, numeric=verdict == '수치표', cells=cells,
                     title=titles, header=header, records=records,
                     row_records_available=hierarchy.get('record_status') == 'structured',
                     kept_as='제목' if titles else '키값' if table_type == 'key_value_table' else '레코드' if records else '산문',
                     markdown=markdown, parent=parent, children=nested)
        self._tables[block.id] = result
        return result

    @staticmethod
    def table_markdown(cells, rows=None, cols=None, header_rows=(), *, sparse=False):
        if not cells:
            return None
        rows = rows if rows is not None else max(c.row + c.row_span for c in cells)
        cols = cols if cols is not None else max(c.col + c.col_span for c in cells)
        def escape(value):
            return value.replace('\\', '\\\\').replace('|', '\\|').replace('\r', ' ').replace('\n', '<br>')
        if not sparse:
            if rows * cols > 1_000_000:
                raise ValueError('Table grid is too large to render')
            grid = [[''] * cols for _ in range(rows)]
            for cell in cells:
                text = escape(cell.text)
                for row in range(cell.row, min(rows, cell.row + cell.row_span)):
                    for col in range(cell.col, min(cols, cell.col + cell.col_span)):
                        grid[row][col] = text
            separator = min(rows - 1, max(header_rows, default=0))
            lines = []
            for row, values in enumerate(grid):
                lines.append('| ' + ' | '.join(values) + ' |')
                if row == separator:
                    lines.append('| ' + ' | '.join(['---'] * cols) + ' |')
            return '\n'.join(lines)
        lines = ["| 행 | 열 | 행 병합 | 열 병합 | 내용 |", "| --- | --- | --- | --- | --- |"]
        for cell in cells:
            text = escape(cell.text)
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
        # An existing cell-style heading and its title box describe the same boundary.
        for heading in self.headings.values():
            cell_id = self.containing_cell.get(heading.block_id)
            owner = self.cell_owners.get(cell_id)
            if owner and owner not in self.in_cells and self.table(self.blocks[owner]).kind == '제목상자':
                self.heading_ids[heading.id] = owner
        self.heading_by_block = {self.heading_ids[h.id]: h for h in self.headings.values()}
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
                    depth=max(0, heading.level - 1), area="본문", kind="문단", role="제목",
                    searchable=True, text=heading.title, heading_path=heading_path,
                    heading_path_text=heading_titles,
                    child_headings=[self.heading_ids[c] for c in heading.children if c in self.heading_ids]))
                emitted.add(ident)
            heading = self.heading_by_block.get(source.id)
            area = "주변부" if source.scope in {"header", "footer", "footnote", "endnote"} else "본문"
            block = Block(source.id, len(model.blocks), self.section(source),
                          max(0, heading.level - 1) if heading else depth, area,
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
        restore_heading_paths(model, inferred_ids={h.block_id for h in self.headings.values()
            if h.source == 'auto_style' and h.evidence.get('rule') == 'style_cluster'})
        model.structure = self.document
        model.document_images = []
        if image_dir is not None:
            self.save_images(model, Path(image_dir))
        return model

    def save_images(self, model, directory):
        from .models import DocumentImage
        model.document_images = []
        # The production image cleanup service uses the document stem directory.
        root = directory / Path(self.document.metadata['filename']).stem
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
