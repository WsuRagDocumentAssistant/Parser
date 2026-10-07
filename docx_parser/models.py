from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import tempfile


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never follow an existing final-file symlink outside the output directory.
    if path.is_symlink():
        raise ValueError(f"Refusing output symlink: {path}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass
class PageLocation:
    physical_pages: list[int] | None = None
    status: str = "unknown"
    method: str = "none"
    reason: str | None = "No reliable page information in the XML."
    printed_page_labels: list[str] | None = None
    anchor_page: int | None = None
    fragments: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


@dataclass(kw_only=True)
class Block:
    id: str
    kind: str
    order: int
    scope: str
    parent_id: str | None
    source_ref: dict
    heading_id: str = "root"
    location: PageLocation = field(default_factory=PageLocation)
    metadata: dict = field(default_factory=dict)


@dataclass(kw_only=True)
class TextBlock(Block):
    text: str = ""
    runs: list[dict] = field(default_factory=list)
    # Text offsets are Python Unicode code points. Object entries preserve interleaving.
    content: list[dict] = field(default_factory=list)


@dataclass
class Cell:
    id: str
    row: int
    column: int
    row_span: int = 1
    col_span: int = 1
    block_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass(kw_only=True)
class TableBlock(Block):
    rows: int = 0
    columns: int = 0
    cells: list[Cell] = field(default_factory=list)

    def to_grid(self, texts: dict[str, str]) -> list[list[str | None]]:
        """Merged cells have text at their origin only; covered slots are None."""
        if self.rows * self.columns > 1_000_000:
            raise ValueError("Table grid is too large to materialize")
        grid: list[list[str | None]] = [[None] * self.columns for _ in range(self.rows)]
        for cell in self.cells:
            grid[cell.row][cell.column] = "\n".join(
                texts[ident] for ident in cell.block_ids if ident in texts
            )
        return grid


@dataclass(kw_only=True)
class ImageBlock(Block):
    asset_id: str | None = None


@dataclass
class HeadingNode:
    id: str
    title: str
    level: int
    depth: int
    parent_id: str | None
    block_id: str | None = None
    raw_level: int | None = None
    source: str = "root"
    evidence: dict = field(default_factory=dict)
    children: list[str] = field(default_factory=list)
    path_ids: list[str] = field(default_factory=list)
    path_titles: list[str] = field(default_factory=list)
    display_number: str | None = None


@dataclass
class Document:
    metadata: dict
    blocks: list[Block] = field(default_factory=list)
    headings: list[HeadingNode] = field(default_factory=list)
    assets: list[dict] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    schema_version: str = "1.0"
    render_context: dict | None = None
    _asset_bytes: dict[str, bytes] = field(default_factory=dict, repr=False)

    @property
    def structure_status(self) -> str:
        return "partial" if any(d["severity"] == "warning" for d in self.diagnostics) else "complete"

    @property
    def status(self) -> str:
        return "complete" if self.structure_status == "complete" and self.page_status == "verified" else "partial"

    @property
    def page_status(self) -> str:
        states = {block.location.status for block in self.blocks if block.kind in {"text", "table", "image"}}
        if not states or "unknown" in states:
            return "incomplete"
        return "verified" if states == {"verified"} else "estimated"

    def iter_headings(
        self, order: str = "dfs", start_heading_id: str = "root",
        max_depth: int | None = None,
    ) -> Iterator[HeadingNode]:
        if order != "dfs":
            raise ValueError("Only preorder DFS is supported")
        if max_depth is not None and max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        lookup = {h.id: h for h in self.headings}
        if start_heading_id not in lookup:
            raise KeyError(start_heading_id)
        stack = [(start_heading_id, 0)]
        while stack:
            ident, depth = stack.pop()
            node = lookup[ident]
            if ident != "root":
                yield node
            if max_depth is None or depth < max_depth:
                stack.extend((child, depth + 1) for child in reversed(node.children))

    def iter_blocks(
        self, heading_id: str | None = None, include_descendants: bool = True,
        kind: str | None = None, physical_page: int | None = None,
    ) -> Iterator[Block]:
        selected = None
        if heading_id is not None:
            lookup = {h.id: h for h in self.headings}
            if heading_id not in lookup:
                raise KeyError(heading_id)
            selected = {heading_id}
            if include_descendants:
                selected.update(h.id for h in self.iter_headings(start_heading_id=heading_id))
        for block in self.blocks:
            if selected is not None and block.heading_id not in selected:
                continue
            if kind is not None and block.kind != kind:
                continue
            if physical_page is not None and physical_page not in (block.location.physical_pages or []):
                continue
            yield block

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "status": self.status, "structure_status": self.structure_status, "page_status": self.page_status,
            "metadata": self.metadata, "sections": self.sections,
            "blocks": [asdict(b) for b in self.blocks],
            "headings": [asdict(h) for h in self.headings],
            "assets": self.assets, "render_context": self.render_context,
            "diagnostics": self.diagnostics,
        }

    def to_json(self, path: str | Path) -> None:
        atomic_write(Path(path), (json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    def export(self, directory: str | Path) -> Path:
        from .exporter import DocumentExporter

        return DocumentExporter(self).export(directory)
