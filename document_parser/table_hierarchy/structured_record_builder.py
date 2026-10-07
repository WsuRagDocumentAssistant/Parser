#================================================
# table_hierarchy/structured_record_builder.py
# raw_rows + header_rows + columns 기반 data_table structured_records 생성
#
# 목표: columns까지 만들어진 data_table의 실제 데이터 행을
# AI가 읽기 쉬운 행 단위 record(row_headers/values)로 변환한다.
# table_type, raw_rows, header_rows, header_cols, columns는 건드리지 않는다.
#================================================

from __future__ import annotations

from typing import Any

from .header_col_detector import _estimate_col_count


def _header_row_addrs_missing(raw_rows: list[dict[str, Any]], header_rows: list[int]) -> list[int]:
    present = {row["row_addr"] for row in raw_rows}
    return [addr for addr in header_rows if addr not in present]


def _build_column_keys(columns: list[dict[str, Any]]) -> dict[int, str]:
    """column.name 중복 시 __2, __3 접미사를 붙인 col_index -> key 매핑을 만든다."""
    name_counts: dict[str, int] = {}
    keys: dict[int, str] = {}

    for column in columns:
        name = column["name"]
        occurrence = name_counts.get(name, 0) + 1
        name_counts[name] = occurrence
        keys[column["col_index"]] = name if occurrence == 1 else f"{name}__{occurrence}"

    return keys


def _build_row_value_map(row: dict[str, Any]) -> tuple[dict[int, str], dict[int, Any], set[int], bool]:
    """row 안에서 col_index -> (원본 셀 시작 위치) 값/셀ID 매핑과, col_span으로 덮인 col_index 집합을 만든다."""
    value_by_col: dict[int, str] = {}
    cellid_by_col: dict[int, Any] = {}
    covered_cols: set[int] = set()
    col_span_detected = False

    cols = row.get("col_addrs", [])
    spans = row.get("col_spans", [])
    texts = row.get("texts", [])
    cell_ids = row.get("cell_ids", [])

    for col_addr, span, text, cell_id in zip(cols, spans, texts, cell_ids):
        value_by_col[col_addr] = text
        cellid_by_col[col_addr] = cell_id
        if span and span > 1:
            col_span_detected = True
            for extra_col in range(col_addr + 1, col_addr + span):
                covered_cols.add(extra_col)

    return value_by_col, cellid_by_col, covered_cols, col_span_detected


def build_structured_records(
    raw_rows: list[dict[str, Any]],
    header_rows: list[int],
    columns: list[dict[str, Any]],
) -> dict[str, Any]:
    """columns까지 준비된 data_table에서 행 단위 structured_records를 만든다."""
    result: dict[str, Any] = {"structured_records": [], "warnings": []}

    if not header_rows or not columns:
        return result

    col_count = _estimate_col_count(raw_rows)
    if len(columns) != col_count:
        result["warnings"].append(
            {
                "code": "COLUMN_COUNT_MISMATCH",
                "message": (
                    f"columns length {len(columns)} does not match "
                    f"estimated col_count {col_count}."
                ),
            }
        )
        return result

    if any(column.get("confidence") == "low" for column in columns):
        result["warnings"].append(
            {
                "code": "LOW_CONFIDENCE_COLUMNS",
                "message": "Structured records were not built because some columns have low confidence.",
            }
        )
        return result

    missing_header_addrs = _header_row_addrs_missing(raw_rows, header_rows)
    if missing_header_addrs:
        result["warnings"].append(
            {
                "code": "HEADER_ROWS_NOT_FOUND",
                "message": f"header_rows {missing_header_addrs} were not found in raw_rows.",
            }
        )

    header_set = set(header_rows)
    max_header_row = max(header_rows)

    data_rows: list[dict[str, Any]] = []
    found_row_before_header = False
    for row in raw_rows:
        if row["row_addr"] in header_set:
            continue
        if row["row_addr"] < max_header_row:
            found_row_before_header = True
            continue
        data_rows.append(row)

    if found_row_before_header:
        result["warnings"].append(
            {
                "code": "DATA_ROW_BEFORE_HEADER",
                "message": "Some rows before the last header row were excluded from structured_records.",
            }
        )

    if not data_rows:
        return result

    name_counts: dict[str, int] = {}
    for column in columns:
        name_counts[column["name"]] = name_counts.get(column["name"], 0) + 1
    if any(count > 1 for count in name_counts.values()):
        result["warnings"].append(
            {
                "code": "DUPLICATED_COLUMN_NAME",
                "message": "Duplicate column names were disambiguated with __2, __3 suffixes.",
            }
        )

    column_keys = _build_column_keys(columns)

    records: list[dict[str, Any]] = []
    found_empty_data_row = False

    for row in data_rows:
        value_by_col, cellid_by_col, covered_cols, col_span_detected = _build_row_value_map(row)

        row_headers: dict[str, str] = {}
        values: dict[str, str] = {}
        source_cell_ids: dict[str, Any] = {}
        row_warnings: list[str] = []
        missing_cell_for_column = False

        for column in columns:
            col_index = column["col_index"]
            key = column_keys[col_index]

            if col_index in value_by_col:
                text = value_by_col[col_index]
                cell_id = cellid_by_col[col_index]
            elif col_index in covered_cols:
                text = ""
                cell_id = None
            else:
                text = ""
                cell_id = None
                missing_cell_for_column = True

            if column["is_row_header"]:
                row_headers[key] = text
                source_cell_ids[key] = cell_id
            else:
                values[key] = text
                source_cell_ids[key] = cell_id

        if not any(v for v in values.values()) and not any(v for v in row_headers.values()):
            found_empty_data_row = True
            continue

        if missing_cell_for_column:
            row_warnings.append("MISSING_CELL_FOR_COLUMN")
        if col_span_detected:
            row_warnings.append("DATA_COL_SPAN_DETECTED")

        records.append(
            {
                "row_index": len(records),
                "source_row_addr": row["row_addr"],
                "row_headers": row_headers,
                "values": values,
                "source_cell_ids": source_cell_ids,
                "warnings": row_warnings,
            }
        )

    if found_empty_data_row:
        result["warnings"].append(
            {
                "code": "EMPTY_DATA_ROW",
                "message": "Some completely empty data rows were excluded from structured_records.",
            }
        )

    result["structured_records"] = records
    return result
