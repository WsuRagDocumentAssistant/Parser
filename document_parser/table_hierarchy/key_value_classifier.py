#================================================
# table_hierarchy/key_value_classifier.py
# key_value_table 판정
#================================================

from __future__ import annotations

from typing import Any

from .cell_utils import (
    _avg_len,
    _cell_col,
    _cell_col_span,
    _cell_has_image,
    _cell_row_span,
    get_cell_text,
)
from .table_utils import _is_nested_table, get_table_size, group_origin_cells_by_row

KEY_VALUE_HEADERS = {
    ("항목", "내용"),
    ("구분", "내용"),
    ("구분", "주요 내용"),
    ("제도", "내용"),
    ("항목", "세부내용"),
    ("구분", "세부내용"),
    ("항목", "설명"),
    ("구분", "설명"),
    ("key", "value"),
}


def get_key_value_row_pairs(table: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for _, cells in sorted(group_origin_cells_by_row(table).items()):
        cells_with_col = [
            (col, cell)
            for cell in cells
            if (col := _cell_col(cell)) is not None
        ]
        if len(cells_with_col) < 2:
            continue
        cells_with_col.sort(key=lambda item: item[0])
        pairs.append((cells_with_col[0][1], cells_with_col[1][1]))
    return pairs


def _is_simple_1x2_kv(table: dict[str, Any]) -> bool:
    """row_count==1, col_count==2인 단순 1x2 key-value 표인지 확인한다.

    nested table(페이지 참조표, 서명란 등 부모 셀에 삽입된 소형 표)은
    텍스트 내용과 무관하게 제외한다 — is_nested / parent_table_id /
    parent_cell_id 등 구조 정보로만 판정한다.
    """
    if _is_nested_table(table):
        return False

    row_count, col_count = get_table_size(table)
    if row_count != 1 or col_count != 2:
        return False

    from .cell_utils import _cell_row, get_direct_cells

    origin_cells = [
        cell for cell in get_direct_cells(table)
        if _cell_row(cell) is not None and _cell_col(cell) is not None
    ]
    if len(origin_cells) != 2:
        return False

    left, right = sorted(origin_cells, key=lambda c: _cell_col(c) or 0)

    left_text = get_cell_text(left)
    right_text = get_cell_text(right)
    if not left_text or not right_text:
        return False

    if _cell_col_span(left) != 1 or _cell_col_span(right) != 1:
        return False
    if _cell_row_span(left) != 1 or _cell_row_span(right) != 1:
        return False

    from .form_kv_builder import _cell_has_nested_tables

    if _cell_has_nested_tables(left) or _cell_has_nested_tables(right):
        return False
    if _cell_has_image(left) or _cell_has_image(right):
        return False

    return True


def _count_adjacent_kv_pairs(table: dict[str, Any], key_max_len: int = 20) -> int:
    """각 행에서 짧은 key + 비어있지 않은 value 인접 쌍 수를 반환한다."""
    count = 0
    for cells in group_origin_cells_by_row(table).values():
        cols_cells = sorted(
            [(col, c) for c in cells if (col := _cell_col(c)) is not None],
            key=lambda x: x[0],
        )
        for i in range(len(cols_cells) - 1):
            _, left = cols_cells[i]
            _, right = cols_cells[i + 1]
            left_text = get_cell_text(left)
            right_text = get_cell_text(right)
            if left_text and right_text and len(left_text) <= key_max_len:
                count += 1
    return count


def _is_form_kv_table(table: dict[str, Any]) -> bool:
    """병합 포함 서식형 key-value 표 감지.

    - 행 구조가 다양하고 (다중 열 데이터 패턴이 반복되지 않음)
    - 짧은 key + value 인접 쌍이 3개 이상이면 True.
    """
    from .data_classifier import _has_data_table_header_structure, _has_repeated_wide_data_pattern

    row_count, col_count = get_table_size(table)
    if row_count < 3 or col_count < 2:
        return False
    if len(group_origin_cells_by_row(table)) < 3:
        return False
    # 다중 열(3열 이상) 데이터 패턴이 반복되면 data_table
    if _has_repeated_wide_data_pattern(table):
        return False
    # multi-level 컬럼 헤더 구조이면 data_table
    if _has_data_table_header_structure(table):
        return False
    return _count_adjacent_kv_pairs(table) >= 3


def _detect_kv_orientation(table: dict[str, Any]) -> str | None:
    """key-value 방향을 감지한다.

    반환값:
      "row_pairs"    — 2열 구조: 각 행이 key/value 쌍
      "column_pairs" — 2행 구조: 첫 행이 key, 둘째 행이 value
      "form_kv"      — 병합 포함 서식형 표
      None           — key_value_table 아님
    """
    row_count, col_count = get_table_size(table)

    # ── 1x2 단순 row_pairs ──────────────────────────────────────
    if col_count == 2 and row_count == 1:
        if _is_simple_1x2_kv(table):
            return "row_pairs"
        return None

    # ── 단순 2열 row_pairs ──────────────────────────────────────
    if col_count == 2 and row_count >= 2:
        row_pairs = get_key_value_row_pairs(table)
        if len(row_pairs) < 2:
            return None

        left_texts = [get_cell_text(l) for l, _ in row_pairs]
        right_texts = [get_cell_text(r) for _, r in row_pairs]
        left_non_empty = [t for t in left_texts if t]
        right_non_empty = [t for t in right_texts if t]

        if len(left_non_empty) < 2 or len(right_non_empty) < 2:
            return None
        if (1 - len(left_non_empty) / len(left_texts)) > 0.25:
            return None
        if (1 - len(right_non_empty) / len(right_texts)) > 0.35:
            return None

        left_avg = _avg_len(left_non_empty)
        right_avg = _avg_len(right_non_empty)
        if left_avg <= 12 and right_avg >= left_avg:
            return "row_pairs"
        return None

    # ── 단순 2행 column_pairs ───────────────────────────────────
    if row_count == 2 and col_count >= 2:
        row_cells = group_origin_cells_by_row(table)
        if len(row_cells) != 2:
            return None
        rows = sorted(row_cells.items())
        key_cells = rows[0][1]
        val_cells = rows[1][1]

        key_texts = [get_cell_text(c) for c in key_cells]
        val_texts = [get_cell_text(c) for c in val_cells]
        key_non_empty = [t for t in key_texts if t]
        val_non_empty = [t for t in val_texts if t]

        if len(key_non_empty) < 2 or len(val_non_empty) < 2:
            return None
        if len(key_non_empty) != len(val_non_empty):
            return None

        key_avg = _avg_len(key_non_empty)
        val_avg = _avg_len(val_non_empty)
        val_min = min(len(t) for t in val_non_empty)

        if key_avg > 6:
            return None
        if val_avg < key_avg * 2:
            return None
        if val_min < 3:
            return None
        return "column_pairs"

    # ── 서식형 form-style (3행+, 2열+) ─────────────────────────
    if _is_form_kv_table(table):
        return "form_kv"

    return None


def is_key_value_table(table: dict[str, Any]) -> bool:
    return _detect_kv_orientation(table) is not None


def _is_structural_kv_header(
    row_pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> bool:
    """첫 행이 구조적으로 헤더처럼 보이는지 판단한다.

    조건:
    - 전체 쌍이 3개 이상 (헤더 후보 1 + 데이터 최소 2)
    - 첫 행 왼쪽/오른쪽 모두 짧음 (왼쪽 ≤10, 오른쪽 ≤20)
    - 2행 이후 오른쪽 평균이 30 이상이고 첫 행 오른쪽의 3배 이상
    - 2행 이후 왼쪽 평균이 15 이하 (짧은 라벨 패턴)
    """
    if len(row_pairs) < 3:
        return False

    first_left_text = get_cell_text(row_pairs[0][0]) or ""
    first_right_text = get_cell_text(row_pairs[0][1]) or ""

    if len(first_left_text) > 10 or len(first_right_text) > 20:
        return False

    body_pairs = row_pairs[1:]
    body_right_lens = [len(get_cell_text(r) or "") for _, r in body_pairs]
    body_left_lens = [len(get_cell_text(l) or "") for l, _ in body_pairs]

    body_right_avg = sum(body_right_lens) / len(body_right_lens)
    body_left_avg = sum(body_left_lens) / len(body_left_lens)

    if body_right_avg < 30:
        return False
    if len(first_right_text) > 0 and body_right_avg < len(first_right_text) * 3:
        return False
    if body_left_avg > 15:
        return False

    return True
