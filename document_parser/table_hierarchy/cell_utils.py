#================================================
# table_hierarchy/cell_utils.py
# 텍스트 / 셀 접근 공통 유틸
#================================================

from __future__ import annotations

import re
from typing import Any


def normalize_text(text: Any) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def get_cell_text(cell: dict[str, Any]) -> str:
    text = cell.get("text")
    if isinstance(text, str) and text.strip():
        return normalize_text(text)
    if isinstance(text, dict):
        nested_text = text.get("text")
        if isinstance(nested_text, str) and nested_text.strip():
            return normalize_text(nested_text)

    parts: list[str] = []
    for paragraph in _as_list(cell.get("paragraphs")):
        if not isinstance(paragraph, dict):
            continue

        paragraph_text = paragraph.get("text")
        if isinstance(paragraph_text, str) and paragraph_text.strip():
            parts.append(paragraph_text)
            continue

        for run in _as_list(paragraph.get("runs")):
            if isinstance(run, dict):
                run_text = run.get("text")
                if isinstance(run_text, str) and run_text.strip():
                    parts.append(run_text)

    return normalize_text(" ".join(parts))


def get_direct_cells(table: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in _as_list(table.get("rows")):
        if not isinstance(row, dict):
            continue
        for cell in _as_list(row.get("cells")):
            if isinstance(cell, dict):
                result.append(cell)

    if result:
        return result

    cells = table.get("cells")
    if isinstance(cells, list):
        return [cell for cell in cells if isinstance(cell, dict)]

    return get_summary_cells(table)


def get_summary_cells(table: dict[str, Any]) -> list[dict[str, Any]]:
    preprocess = table.get("preprocess")
    if not isinstance(preprocess, dict):
        return []

    cells = preprocess.get("cells")
    if isinstance(cells, list):
        return [cell for cell in cells if isinstance(cell, dict)]

    return []


def get_non_empty_cells(table: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        cell for cell in get_direct_cells(table)
        if get_cell_text(cell) != ""
    ]


def get_all_text(table: dict[str, Any]) -> str:
    return normalize_text(" ".join(get_cell_text(cell) for cell in get_direct_cells(table)))


def _cell_row(cell: dict[str, Any]) -> int | None:
    row = _to_int(cell.get("row_addr"))
    if row is not None:
        return row

    position = cell.get("position")
    if isinstance(position, dict):
        return _to_int(position.get("row_addr"))

    return None


def _cell_col(cell: dict[str, Any]) -> int | None:
    col = _to_int(cell.get("col_addr"))
    if col is not None:
        return col

    position = cell.get("position")
    if isinstance(position, dict):
        return _to_int(position.get("col_addr"))

    return None


def _cell_row_span(cell: dict[str, Any]) -> int:
    v = cell.get("row_span")
    if v is None:
        v = (cell.get("position") or {}).get("row_span")
    return _to_int(v, default=1) or 1


def _cell_col_span(cell: dict[str, Any]) -> int:
    v = cell.get("col_span")
    if v is None:
        v = (cell.get("position") or {}).get("col_span")
    return _to_int(v, default=1) or 1


def _cell_ids(cells: list[dict[str, Any]]) -> list[Any]:
    return _unique([cell.get("cell_id") for cell in cells])


def _cell_has_image(cell: dict[str, Any]) -> bool:
    if cell.get("has_image"):
        return True
    images = cell.get("images")
    return isinstance(images, list) and len(images) > 0


def _to_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _avg_len(texts: list[str]) -> float:
    if not texts:
        return 0.0
    return sum(len(text) for text in texts) / len(texts)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
