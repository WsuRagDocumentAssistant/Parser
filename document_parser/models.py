"""WSU DocumentModel field contract, adapted from HwpxParser_sub commit 8056258."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


#------------------------------------------------
# 파일 정보
#------------------------------------------------

@dataclass
class FileInfo:
    """문서 자체에 대한 정보.

    title 은 파일명을 쓴다. 원본(content.hpf)의 title 이 비어 있는 문서가 있고
    sample 이 그렇다.
    """
    title: str
    filename: str
    creator: str | None = None
    last_saved_by: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    language: str | None = None
    application: str | None = None
    app_version: str | None = None
    section_count: int = 0
    table_count: int = 0


@dataclass
class ImageFile:
    """BinData 의 실제 파일. content.hpf 의 manifest 에서 온다."""
    ref: str
    path: str
    media_type: str | None = None
    size_bytes: int | None = None


@dataclass
class ImageRef:
    """셀·도형이 가리키는 이미지. 경로는 manifest 에서 풀어 넣는다."""
    ref: str
    path: str | None = None
    media_type: str | None = None


#------------------------------------------------
# 표
#------------------------------------------------

@dataclass
class TableColumn:
    index: int
    name: str
    is_row_header: bool = False


@dataclass
class TableHeader:
    header_rows: list[int] = field(default_factory=list)
    header_cols: list[int] = field(default_factory=list)
    columns: list[TableColumn] = field(default_factory=list)


@dataclass
class TableRecord:
    """행 하나가 자립하는 형태. 병합 빈칸은 채워 넣는다.

    inherited 는 그 값이 이 행 고유가 아니라 위칸에서 물려받은 것임을 알린다.
    이 표시가 없으면 같은 값이 여러 행에 나오는 것이 진짜 반복인지 병합인지
    구분할 수 없다.
    """
    index: int
    values: dict[str, str] = field(default_factory=dict)
    inherited: list[str] = field(default_factory=list)


@dataclass
class Cell:
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    text: str = ''
    paragraphs: list[str] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    child_tables: list[str] = field(default_factory=list)


@dataclass
class TableParent:
    """중첩 표가 어느 표의 어느 칸에 들어 있었는지."""
    table_id: str
    cell_id: str


@dataclass
class Table:
    """
    kept_as 는 이 표가 어떤 근거로 결과에 남았는지다.

      레코드  행마다 값이 다 채워져 레코드로 선다. 행 단위로 쪼개도 된다.
      키값    키-값 쌍으로 선다.
      산문    격자가 아니라 헤더 개념이 성립하지 않는다. 통짜로 쓴다.
      제목    제목상자.

    kind 만으로는 이 구분이 안 된다. 헤더가 없는 데이터표가 '산문이라
    헤더가 필요 없는 표' 인지 '헤더가 있어야 하는데 못 잡은 표' 인지
    가릴 수 없기 때문이다. 후자는 애초에 결과에 안 들어오지만, 그 판단
    근거가 객체에 남아 있어야 쓰는 쪽에서 되물을 수 있다.
    """
    id: str
    kind: str                       # 데이터표 / 제목상자 / 키값표
    kept_as: str = ''               # 레코드 / 키값 / 산문 / 제목
    rows: int = 0
    cols: int = 0
    numeric: bool = False
    numeric_verdict: str = ''       # 수치표 / 아님 / 판정불가 / 대상아님
    row_records_available: bool = False
    title: list[str] = field(default_factory=list)
    header: TableHeader | None = None
    records: list[TableRecord] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    markdown: str | None = None
    raw_row_count: int | None = None
    parent: TableParent | None = None
    children: list['Table'] = field(default_factory=list)


#------------------------------------------------
# 블록
#------------------------------------------------

@dataclass
class Figure:
    """이미지·도형. 위치와 실제 파일 참조."""
    shape: str                      # 이미지 / 도형 / 도형묶음
    z_order: str | None = None
    placement: str = ''             # 글자처럼 / 떠있음
    paragraph_index: int | None = None
    image: ImageRef | None = None
    shape_type: str | None = None
    width: str | None = None
    height: str | None = None
    contains: dict[str, int] = field(default_factory=dict)


@dataclass
class TocRef:
    """이 블록이 목차의 어느 항목인지."""
    title: str
    numbering: str | None = None


@dataclass
class TocEntry:
    """목차 표를 펼친 항목. depth 는 번호 성분 수에서 나온다."""
    text: str
    depth: int


@dataclass
class ExcludedTable:
    """구조를 믿을 수 없어 뺀 표의 자리. OCR 결과가 들어올 곳."""
    table_id: str
    reason: str


@dataclass
class Block:
    id: str
    order: int
    section: int
    depth: int
    area: str                       # 본문 / 주변부
    kind: str                       # 문단 / 표 / 이미지 / 도형 / 도형묶음 …
    role: str                       # 제목 / 본문 / 표 / 그림 …
    searchable: bool = False
    text: str | None = None
    heading_path: list[str] = field(default_factory=list)
    heading_path_text: list[str] = field(default_factory=list)
    child_headings: list[str] = field(default_factory=list)
    toc: TocRef | None = None
    toc_entries: list[TocEntry] = field(default_factory=list)
    excluded_table: ExcludedTable | None = None
    figure: Figure | None = None
    table: Table | None = None


@dataclass
class DocumentModel:
    file: FileInfo
    images: dict[str, ImageFile] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """직렬화가 필요할 때만 쓴다. 모델이 정본이고 dict 는 파생물이다."""
        return asdict(self)

    # -- 조회 도우미 -------------------------------------------------
    def tables(self):
        """중첩 표까지 전부."""
        def walk(t):
            yield t
            for child in t.children:
                yield from walk(child)
        for block in self.blocks:
            if block.table is not None:
                yield from walk(block.table)

    def numeric_tables(self):
        return [t for t in self.tables() if t.numeric]

    def searchable_blocks(self):
        return [b for b in self.blocks if b.searchable]

    def to_json(self, path):
        import json
        from pathlib import Path
        from hwpx_parser.models import atomic_write
        atomic_write(Path(path), (json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + '\n').encode('utf-8'))

    def export(self, directory):
        from pathlib import Path
        from .adapter import ModelAdapter
        root = Path(directory)
        self.structure.export(root / 'structure')
        ModelAdapter(self.structure).save_images(self, root)
        self.to_json(root / 'document.json')
        return root / 'document.json'


@dataclass
class DocumentImage:
    """Field-compatible with RagModuls.ragmodul.models.image_model.DocumentImage."""
    ref: str
    path: str
    order: int
    section: int = 0
    media_type: str | None = None
    heading_path: list[str] = field(default_factory=list)
    caption: str | None = None
