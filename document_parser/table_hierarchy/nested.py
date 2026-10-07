#================================================
# table_hierarchy/nested.py
# nested table 순회 및 hierarchy 재귀 연결
#================================================

from __future__ import annotations

from typing import Any

from .cell_utils import _as_list


def add_nested_hierarchy_from_rows(
    table: dict[str, Any],
    hierarchy: dict[str, Any],
    stats: "Counter[str] | None",
    depth: int,
) -> bool:
    from .orchestrator import add_hierarchy_recursive

    found = False
    for row in _as_list(table.get("rows")):
        if not isinstance(row, dict):
            continue

        for cell in _as_list(row.get("cells")):
            if not isinstance(cell, dict):
                continue

            nested_tables = cell.get("nested_tables")
            if not isinstance(nested_tables, list):
                continue

            for index, nested_table in enumerate(nested_tables):
                if not isinstance(nested_table, dict):
                    continue
                found = True
                hierarchy["nested_table_refs"].append(
                    {
                        "parent_cell_id": cell.get("cell_id"),
                        "nested_table_id": nested_table.get("table_id"),
                        "nested_table_index": index,
                    }
                )
                add_hierarchy_recursive(nested_table, stats=stats, depth=depth + 1)

    return found


def add_nested_hierarchy_from_direct_cells(
    table: dict[str, Any],
    hierarchy: dict[str, Any],
    stats: "Counter[str] | None",
    depth: int,
) -> bool:
    from .orchestrator import add_hierarchy_recursive

    found = False
    for cell in _as_list(table.get("cells")):
        if not isinstance(cell, dict):
            continue

        nested_tables = cell.get("nested_tables")
        if not isinstance(nested_tables, list):
            continue

        for index, nested_table in enumerate(nested_tables):
            if not isinstance(nested_table, dict):
                continue
            found = True
            hierarchy["nested_table_refs"].append(
                {
                    "parent_cell_id": cell.get("cell_id"),
                    "nested_table_id": nested_table.get("table_id"),
                    "nested_table_index": index,
                }
            )
            add_hierarchy_recursive(nested_table, stats=stats, depth=depth + 1)

    return found


def add_nested_hierarchy_from_summary_children(
    table: dict[str, Any],
    hierarchy: dict[str, Any],
    stats: "Counter[str] | None",
    depth: int,
) -> bool:
    from .orchestrator import add_hierarchy_recursive

    found = False
    for index, child in enumerate(_as_list(table.get("children"))):
        if not isinstance(child, dict):
            continue
        found = True
        hierarchy["nested_table_refs"].append(
            {
                "parent_cell_id": child.get("parent_cell_id"),
                "nested_table_id": child.get("table_id"),
                "nested_table_index": index,
            }
        )
        add_hierarchy_recursive(child, stats=stats, depth=depth + 1)
    return found
