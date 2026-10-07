#================================================
# table_hierarchy/key_value_builder.py
# key_value_table hierarchy 레코드 생성
#================================================

from __future__ import annotations

from typing import Any

from .cell_utils import _cell_col, _cell_row, _unique, get_cell_text, normalize_text
from .key_value_classifier import (
    KEY_VALUE_HEADERS,
    _detect_kv_orientation,
    _is_structural_kv_header,
    get_key_value_row_pairs,
)
from .table_utils import group_origin_cells_by_row


def build_key_value_records(
    table: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[int], list[Any], str, dict[str, Any] | None]:
    """key_value_table의 pairs, header_rows, body_cells, orientation, header_content를 반환한다."""
    records: list[dict[str, Any]] = []
    header_rows: list[int] = []
    body_cells: list[Any] = []
    header_content: dict[str, Any] | None = None

    orientation = _detect_kv_orientation(table) or "row_pairs"

    if orientation == "row_pairs":
        row_pairs = get_key_value_row_pairs(table)
        start_index = 0

        if row_pairs:
            first_left, first_right = row_pairs[0]
            first_pair = (
                normalize_text(get_cell_text(first_left)).lower(),
                normalize_text(get_cell_text(first_right)).lower(),
            )
            is_header = (
                first_pair in KEY_VALUE_HEADERS
                or _is_structural_kv_header(row_pairs)
            )
            if is_header:
                row_index = _cell_row(first_left)
                if row_index is not None:
                    header_rows.append(row_index)
                header_content = {
                    "key": get_cell_text(first_left),
                    "value": get_cell_text(first_right),
                }
                start_index = 1

        for left_cell, right_cell in row_pairs[start_index:]:
            key = get_cell_text(left_cell)
            value = get_cell_text(right_cell)
            if not key and not value:
                continue
            key_cell_id = left_cell.get("cell_id")
            value_cell_id = right_cell.get("cell_id")
            records.append({
                "key": key,
                "value": value,
                "key_cell_id": key_cell_id,
                "value_cell_id": value_cell_id,
                "row_addr": _cell_row(left_cell),
                "source_cells": [key_cell_id, value_cell_id],
            })
            body_cells.extend([key_cell_id, value_cell_id])

    elif orientation == "column_pairs":
        row_cells = group_origin_cells_by_row(table)
        rows = sorted(row_cells.items())
        key_row_index, key_cells = rows[0]
        val_row_index, val_cells = rows[1]

        header_rows.append(key_row_index)

        key_by_col = {_cell_col(c): c for c in key_cells if _cell_col(c) is not None}
        val_by_col = {_cell_col(c): c for c in val_cells if _cell_col(c) is not None}

        for col in sorted(key_by_col):
            key_cell = key_by_col[col]
            val_cell = val_by_col.get(col)
            if val_cell is None:
                continue
            key = get_cell_text(key_cell)
            value = get_cell_text(val_cell)
            if not key and not value:
                continue
            key_cell_id = key_cell.get("cell_id")
            value_cell_id = val_cell.get("cell_id")
            records.append({
                "key": key,
                "value": value,
                "key_cell_id": key_cell_id,
                "value_cell_id": value_cell_id,
                "source_cells": [key_cell_id, value_cell_id],
            })
            body_cells.extend([key_cell_id, value_cell_id])

    else:  # form_kv
        # form_kv는 form_sections 중심으로 구성되며 인접 셀 기반 key_value_records는
        # 생성하지 않는다 — body_cells만 실제 텍스트가 있는 origin cell로 채운다.
        for cells in group_origin_cells_by_row(table).values():
            for cell in cells:
                if get_cell_text(cell):
                    body_cells.append(cell.get("cell_id"))

    return records, _unique(header_rows), _unique(body_cells), orientation, header_content
