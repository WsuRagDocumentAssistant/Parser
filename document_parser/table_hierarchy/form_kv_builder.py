#================================================
# table_hierarchy/form_kv_builder.py
# form_kv 표의 section / block 구성 및 정형 반복표 헬퍼
#================================================

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .cell_utils import _as_list, _cell_col, _cell_col_span, _cell_row_span, get_cell_text
from .table_utils import get_table_size, group_origin_cells_by_row


def _normalize_label(text: str) -> str:
    """한글 자간 패딩 공백 제거 후 일반 공백 정규화.

    "대    학" → "대학", "사업\n총괄책임자" → "사업 총괄책임자"
    """
    # 한글 글자 사이 2칸 이상 공백은 시각적 패딩 → 제거
    text = re.sub(r"(?<=[가-힣])\s{2,}(?=[가-힣])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _cell_has_nested_tables(cell: dict[str, Any]) -> bool:
    nested = cell.get("nested_tables")
    return isinstance(nested, list) and len(nested) > 0


def _cell_has_nested_table_ref(table: dict[str, Any], cell: dict[str, Any]) -> bool:
    """cell 내부 nested_tables 배열뿐 아니라, table.children 중
    parent_cell_id가 이 cell과 일치하는 항목이 있는지도 확인한다.

    일부 입력 포맷은 nested table을 cell.nested_tables가 아니라
    표 상위 children 리스트(+parent_cell_id)로만 표현하므로, 이 경로도
    확인해야 has_nested_table이 정확해진다.
    """
    if _cell_has_nested_tables(cell):
        return True
    cell_id = cell.get("cell_id")
    if cell_id is None:
        return False
    return any(
        isinstance(child, dict) and child.get("parent_cell_id") == cell_id
        for child in _as_list(table.get("children"))
    )


def _label_group_signature_counts(table: dict[str, Any]) -> dict[int, int]:
    """col_addr=0의 row_span>1 그룹 라벨 범위 안에서, 각 행의 origin column
    구성(라벨 셀 제외)이 같은 그룹 안에서 몇 번 반복되는지를 매핑한다.

    반복 횟수가 크면(예: 5회, 7회) 다중 열 데이터가 반복되는 표(data-table류
    sub-block)로 보고, 작으면(예: 2회) 서로 다른 key/value 쌍이 나열된
    구조로 본다. 그룹 밖의 행은 매핑에 포함하지 않는다.
    """
    row_cells = group_origin_cells_by_row(table)
    sorted_rows = sorted(row_cells.keys())
    repeat_count: dict[int, int] = {}

    for r in sorted_rows:
        if r in repeat_count:
            continue
        cells = sorted(
            row_cells[r],
            key=lambda c: (_cell_col(c) if _cell_col(c) is not None else 9999),
        )
        if not cells:
            continue
        first = cells[0]
        if not (_cell_col(first) == 0 and _cell_row_span(first) > 1):
            continue

        span_end = r + _cell_row_span(first) - 1
        group_rows = [rr for rr in sorted_rows if r <= rr <= span_end]

        signatures: dict[int, tuple[int, ...]] = {}
        for rr in group_rows:
            rcells = sorted(
                row_cells[rr],
                key=lambda c: (_cell_col(c) if _cell_col(c) is not None else 9999),
            )
            if rr == r:
                rcells = rcells[1:]  # 라벨 셀 제외
            signatures[rr] = tuple(
                _cell_col(c) for c in rcells if get_cell_text(c)
            )

        counts = Counter(signatures.values())
        for rr, sig in signatures.items():
            repeat_count[rr] = counts[sig]

    return repeat_count


def _build_form_sections(
    table: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """form_kv 표에서 form_sections, full_width_blocks, raw_blocks를 만든다.

    - 첫 행 full-width 단일 셀 → "문서 제목" section
    - 비첫 행 full-width 단일 셀 → full_width_blocks (section에 넣지 않음)
    - col=0 row_span>1 셀 → section 그룹 라벨
    - 유효 origin cell이 정확히 2개인 행/sub-block은 (key, value) 쌍으로 파싱
    - 유효 origin cell이 3개 이상이면서 그룹 라벨 범위 안이고, 오른쪽 영역을
      짝수 개씩 나눌 수 있고, 같은 열 구성이 그룹 안에서 반복되지 않으면
      순서대로 2개씩 (key, value) 쌍으로 파싱
    - 그 외 3개 이상인 행/sub-block(그룹 밖, 홀수 개, 또는 다중 열 데이터가
      반복되는 구조)은 key/value로 페어링하지 않고 raw_blocks에 구조 그대로 보존
    - section명·key는 _normalize_label로 정규화
    - 연속·반복 동일 section명은 하나로 병합
    """
    _, col_count = get_table_size(table)
    row_cells = group_origin_cells_by_row(table)
    sorted_rows = sorted(row_cells.keys())
    first_row = sorted_rows[0] if sorted_rows else None
    signature_repeat_counts = _label_group_signature_counts(table)

    sections: list[dict[str, Any]] = []
    full_width_blocks: list[dict[str, Any]] = []
    raw_blocks: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    group_until: int = -1

    def _use_section(label: str) -> dict[str, Any]:
        nonlocal current_section
        norm = _normalize_label(label)
        if sections and sections[-1]["section"] == norm:
            current_section = sections[-1]
        else:
            current_section = {"section": norm, "items": []}
            sections.append(current_section)
        return current_section

    for r in sorted_rows:
        cells = sorted(
            row_cells.get(r, []),
            key=lambda c: (_cell_col(c) if _cell_col(c) is not None else 9999),
        )
        if not cells:
            continue

        # full-width 단일 셀 처리
        if len(cells) == 1:
            cell = cells[0]
            if (_cell_col(cell) == 0) and (_cell_col_span(cell) >= col_count):
                text = get_cell_text(cell) or ""
                if r == first_row:
                    # 첫 행만 "문서 제목"으로 저장
                    if text:
                        sec = _use_section("문서 제목")
                        sec["items"].append({
                            "key": "제목",
                            "value": text,
                            "key_cell_id": cell.get("cell_id"),
                            "value_cell_id": cell.get("cell_id"),
                            "row_addr": r,
                        })
                else:
                    # 나머지 full-width 셀은 full_width_blocks에 보존
                    full_width_blocks.append({
                        "row_addr": r,
                        "cell_id": cell.get("cell_id"),
                        "text": text,
                        "has_nested_table": _cell_has_nested_table_ref(table, cell),
                    })
                continue

        first = cells[0]
        first_col = _cell_col(first)
        first_rs = _cell_row_span(first)

        # 좌측 그룹 라벨 (col=0, row_span>1)
        is_label_row = first_col == 0 and first_rs > 1
        in_label_group = is_label_row or (current_section is not None and r <= group_until)

        if is_label_row:
            label = get_cell_text(first) or "기본 정보"
            _use_section(label)
            group_until = r + first_rs - 1
            kv_cells = cells[1:]
        else:
            if current_section is None or r > group_until:
                _use_section("기본 정보")
                group_until = -1
            kv_cells = cells

        if current_section is None or not kv_cells:
            continue

        if len(kv_cells) >= 3:
            not_repeated_pattern = signature_repeat_counts.get(r, 1) <= 2
            if in_label_group and len(kv_cells) % 2 == 0 and not_repeated_pattern:
                # 그룹 라벨 범위 안: 오른쪽 영역을 순서대로 2개씩 key/value 쌍으로 묶는다
                for i in range(0, len(kv_cells), 2):
                    key_cell, value_cell = kv_cells[i], kv_cells[i + 1]
                    key_text = _normalize_label(get_cell_text(key_cell) or "")
                    val_text = get_cell_text(value_cell) or ""
                    if key_text or val_text:
                        current_section["items"].append({
                            "key": key_text,
                            "value": val_text,
                            "key_cell_id": key_cell.get("cell_id"),
                            "value_cell_id": value_cell.get("cell_id"),
                            "row_addr": r,
                        })
                continue

            # 그룹 라벨 범위 밖(또는 홀수 개)의 다중 열 데이터 sub-block:
            # key/value로 페어링하지 않고 구조 그대로 보존
            raw_blocks.append({
                "row_addr": r,
                "cell_ids": [c.get("cell_id") for c in kv_cells],
                "texts": [get_cell_text(c) for c in kv_cells],
                "col_addrs": [_cell_col(c) for c in kv_cells],
                "col_spans": [_cell_col_span(c) for c in kv_cells],
                "row_spans": [_cell_row_span(c) for c in kv_cells],
            })
            continue

        if len(kv_cells) == 2:
            key_cell, value_cell = kv_cells[0], kv_cells[1]
            key_text = _normalize_label(get_cell_text(key_cell) or "")
            val_text = get_cell_text(value_cell) or ""
            if key_text or val_text:
                current_section["items"].append({
                    "key": key_text,
                    "value": val_text,
                    "key_cell_id": key_cell.get("cell_id"),
                    "value_cell_id": value_cell.get("cell_id"),
                    "row_addr": r,
                })
        else:  # len(kv_cells) == 1
            key_cell = kv_cells[0]
            key_text = _normalize_label(get_cell_text(key_cell) or "")
            if key_text:
                current_section["items"].append({
                    "key": key_text,
                    "value": "",
                    "key_cell_id": key_cell.get("cell_id"),
                    "value_cell_id": None,
                    "row_addr": r,
                })

    return [s for s in sections if s["items"]], full_width_blocks, raw_blocks


# ── form_kv 정형 반복표 판정 및 structured_records 생성 (미사용, 이동만 수행) ──
