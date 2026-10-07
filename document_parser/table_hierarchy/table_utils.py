#================================================
# table_hierarchy/table_utils.py
# 표 크기 / 행 그룹핑 / 구조 판정 공통 유틸
#================================================

from __future__ import annotations

from typing import Any

from .cell_utils import _cell_col, _cell_row, _to_int, _unique, get_cell_text, get_direct_cells


def get_table_size(table: dict[str, Any]) -> tuple[int, int]:
    row_count = _to_int(table.get("row_count"))
    col_count = _to_int(table.get("col_count"))

    grid = table.get("grid")
    if isinstance(grid, dict):
        grid_row_count = _to_int(grid.get("row_count"))
        grid_col_count = _to_int(grid.get("col_count"))
        row_count = row_count if row_count is not None else grid_row_count
        col_count = col_count if col_count is not None else grid_col_count

    if row_count is None or col_count is None:
        preprocess = table.get("preprocess")
        preprocess_grid = preprocess.get("grid") if isinstance(preprocess, dict) else None
        if isinstance(preprocess_grid, dict):
            row_count = row_count if row_count is not None else _to_int(preprocess_grid.get("row_count"))
            col_count = col_count if col_count is not None else _to_int(preprocess_grid.get("col_count"))

        layout = preprocess.get("layout") if isinstance(preprocess, dict) else None
        if isinstance(layout, dict):
            row_count = row_count if row_count is not None else _to_int(layout.get("row_count"))
            col_count = col_count if col_count is not None else _to_int(layout.get("col_count"))

    return row_count or 0, col_count or 0


def group_origin_cells_by_row(table: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for cell in get_direct_cells(table):
        row_index = _cell_row(cell)
        if row_index is None:
            continue
        result.setdefault(row_index, []).append(cell)

    for cells in result.values():
        cells.sort(key=lambda cell: _cell_col(cell) or 0)

    return dict(sorted(result.items()))


def build_body_cells(table: dict[str, Any], excluded_rows: set[int]) -> list[Any]:
    body_cells: list[Any] = []
    for row_index, cells in group_origin_cells_by_row(table).items():
        if row_index in excluded_rows:
            continue
        for cell in cells:
            if get_cell_text(cell):
                body_cells.append(cell.get("cell_id"))
    return _unique(body_cells)


def _is_nested_table(table: dict[str, Any]) -> bool:
    if table.get("is_nested"):
        return True
    if table.get("parent_table_id") is not None:
        return True
    if table.get("parent_cell_id") is not None:
        return True
    return "_nested_tbl" in str(table.get("table_id", ""))


def has_repetitive_data_structure(table: dict[str, Any]) -> bool:
    row_cells = group_origin_cells_by_row(table)
    if len(row_cells) < 3:
        return False

    non_empty_rows = 0
    for cells in row_cells.values():
        if sum(1 for cell in cells if get_cell_text(cell)) >= 2:
            non_empty_rows += 1

    _, col_count = get_table_size(table)
    return non_empty_rows >= 3 and col_count >= 2
