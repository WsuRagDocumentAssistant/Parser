#================================================
# table_hierarchy/data_builder.py
# data_table hierarchy 생성
#
# 현재는 build_hierarchy()의 data_table 처리(header_rows 추론 + body_cells 계산)를
# 그대로 옮겨 담은 껍데기이며, 결과는 리팩토링 전과 동일하다.
#================================================

from __future__ import annotations

from typing import Any

from .cell_utils import _avg_len, _cell_col, _cell_col_span, _cell_row_span, get_cell_text
from .table_utils import build_body_cells, get_table_size, group_origin_cells_by_row


def infer_header_rows(table: dict[str, Any]) -> tuple[list[int], bool]:
    row_cells = group_origin_cells_by_row(table)
    if not row_cells:
        return [], True

    first_row_index = min(row_cells)
    first_cells = row_cells[first_row_index]
    first_texts = [get_cell_text(cell) for cell in first_cells]
    non_empty_count = sum(1 for text in first_texts if text)
    row_count, col_count = get_table_size(table)
    ratio = non_empty_count / max(len(first_cells), col_count, 1)
    avg_first = _avg_len([text for text in first_texts if text])

    later_texts = [
        get_cell_text(cell)
        for row_index, cells in row_cells.items()
        if row_index != first_row_index
        for cell in cells
        if get_cell_text(cell)
    ]
    later_avg = _avg_len(later_texts)

    repeat_header = table.get("repeat_header")
    is_repeat_header = repeat_header is True or str(repeat_header).lower() == "true"
    short_label_row = non_empty_count > 0 and avg_first <= 18

    if is_repeat_header and non_empty_count:
        return [first_row_index], False

    if row_count > 1 and ratio >= 0.5 and short_label_row and (later_avg == 0 or avg_first <= later_avg):
        return [first_row_index], False

    # 텍스트 휴리스틱이 실패했을 때 테두리 두께 기반 경계 사용
    border_indices = (
        table.get("preprocess", {}).get("validation", {}).get("header_border_row_indices")
        or []
    )
    if border_indices and row_count > 1:
        return list(border_indices), False

    return [], row_count > 1


def build_data_table_hierarchy(table: dict[str, Any]) -> tuple[list[int], list[Any], bool]:
    """data_table의 header_rows, body_cells, ambiguous 여부를 반환한다.

    build_hierarchy()의 기존 data_table 처리 결과와 동일하다.
    """
    header_rows, ambiguous = infer_header_rows(table)
    body_cells = build_body_cells(table, excluded_rows=set(header_rows))
    return header_rows, body_cells, ambiguous


def _cell_has_nested_table(
    table: dict[str, Any],
    cell: dict[str, Any],
    nested_table_refs: list[dict[str, Any]],
) -> bool:
    nested_tables = cell.get("nested_tables")
    if isinstance(nested_tables, list) and len(nested_tables) > 0:
        return True

    cell_id = cell.get("cell_id")

    for ref in nested_table_refs:
        if isinstance(ref, dict) and ref.get("parent_cell_id") == cell_id:
            return True

    children = table.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict) and child.get("parent_cell_id") == cell_id:
                return True

    return False


def build_raw_rows(
    table: dict[str, Any],
    nested_table_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """data_table의 원형 행 구조(raw_rows)를 row_addr 단위 origin cell만으로 구성한다."""
    raw_rows: list[dict[str, Any]] = []

    for row_index, cells in group_origin_cells_by_row(table).items():
        cell_ids: list[Any] = []
        texts: list[str] = []
        col_addrs: list[int] = []
        row_spans: list[int] = []
        col_spans: list[int] = []
        has_nested_table = False

        for cell in cells:
            cell_ids.append(cell.get("cell_id"))
            texts.append(get_cell_text(cell))
            col_addrs.append(_cell_col(cell) or 0)
            row_spans.append(_cell_row_span(cell))
            col_spans.append(_cell_col_span(cell))

            if _cell_has_nested_table(table, cell, nested_table_refs):
                has_nested_table = True

        raw_rows.append(
            {
                "row_addr": row_index,
                "cell_ids": cell_ids,
                "texts": texts,
                "col_addrs": col_addrs,
                "row_spans": row_spans,
                "col_spans": col_spans,
                "has_nested_table": has_nested_table,
            }
        )

    return raw_rows
