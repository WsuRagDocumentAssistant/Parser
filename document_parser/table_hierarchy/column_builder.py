#================================================
# table_hierarchy/column_builder.py
# raw_rows + header_rows + header_cols 기반 data_table columns 생성
#
# 목표: header_rows가 있는 data_table에 한해서만 columns를 만든다.
# structured_records는 이 단계에서 생성하지 않는다.
#================================================

from __future__ import annotations

from typing import Any

from .cell_utils import normalize_text
from .header_col_detector import _estimate_col_count
from .table_utils import get_table_size


def _header_row_lookup(
    raw_rows: list[dict[str, Any]],
    header_rows: list[int],
) -> tuple[list[dict[str, Any]], list[int]]:
    row_by_addr = {row["row_addr"]: row for row in raw_rows}
    found_rows: list[dict[str, Any]] = []
    missing: list[int] = []

    for addr in header_rows:
        row = row_by_addr.get(addr)
        if row is None:
            missing.append(addr)
        else:
            found_rows.append(row)

    return found_rows, missing


def _cell_text_at_col(row: dict[str, Any], col_index: int) -> str | None:
    cols = row.get("col_addrs", [])
    spans = row.get("col_spans", [])
    texts = row.get("texts", [])

    for col_addr, span, text in zip(cols, spans, texts):
        if col_addr <= col_index < col_addr + span:
            return text

    return None


def _row_max_extent(row: dict[str, Any]) -> int:
    max_extent = 0
    for col_addr, span in zip(row.get("col_addrs", []), row.get("col_spans", [])):
        max_extent = max(max_extent, (col_addr or 0) + (span or 1))
    return max_extent


def build_columns(
    table: dict[str, Any],
    raw_rows: list[dict[str, Any]],
    header_rows: list[int],
    header_cols: list[int],
) -> dict[str, Any]:
    """header_rows가 있는 data_table에 대해 columns를 생성한다."""
    result: dict[str, Any] = {"columns": [], "warnings": []}

    if not header_rows:
        return result

    header_row_objs, missing_rows = _header_row_lookup(raw_rows, header_rows)
    if missing_rows:
        result["warnings"].append(
            {
                "code": "HEADER_ROWS_NOT_FOUND",
                "message": f"header_rows {missing_rows} were not found in raw_rows.",
            }
        )

    if not header_row_objs:
        return result

    col_count = _estimate_col_count(raw_rows)
    if col_count <= 0:
        return result

    _, declared_col_count = get_table_size(table)
    if declared_col_count and declared_col_count != col_count:
        result["warnings"].append(
            {
                "code": "COLUMN_COUNT_MISMATCH",
                "message": (
                    f"Estimated column count {col_count} does not match "
                    f"declared col_count {declared_col_count}."
                ),
            }
        )

    if any(_row_max_extent(header_row) < col_count for header_row in header_row_objs):
        result["warnings"].append(
            {
                "code": "HEADER_SPAN_CONFLICT",
                "message": "A header row does not cover the full column width of the table.",
            }
        )

    header_cols_set = set(header_cols or [])
    columns: list[dict[str, Any]] = []

    for col_index in range(col_count):
        header_texts: list[str] = []
        covered_any = False
        empty_in_header = False

        for header_row in header_row_objs:
            text = _cell_text_at_col(header_row, col_index)
            if text is None:
                continue

            covered_any = True
            normalized = normalize_text(text)
            if normalized:
                header_texts.append(normalized)
            else:
                empty_in_header = True

        col_warnings: list[str] = []
        if empty_in_header:
            col_warnings.append("EMPTY_HEADER_TEXT")

        name = " ".join(header_texts)
        used_fallback = False
        if not name:
            name = f"column_{col_index}"
            used_fallback = True
            col_warnings.append("FALLBACK_COLUMN_NAME")

        if not covered_any:
            confidence = "low"
        elif used_fallback or empty_in_header:
            confidence = "medium"
        else:
            confidence = "high"

        columns.append(
            {
                "col_index": col_index,
                "name": name,
                "header_texts": header_texts,
                "source_header_rows": list(header_rows),
                "is_row_header": col_index in header_cols_set,
                "confidence": confidence,
                "warnings": col_warnings,
            }
        )

    result["columns"] = columns
    return result
