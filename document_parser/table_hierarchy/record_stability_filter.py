#================================================
# table_hierarchy/record_stability_filter.py
# structured_records 생성 이후 안정성 필터(6단계)
#
# 목표: 이미 생성된 structured_records를 새로 만들지 않고 검토만 하여,
# 의미적으로 불안정하다고 판단되는 경우 structured_records를 비우고
# raw_rows만 신뢰 데이터로 남긴다(record_status="raw_only").
# table_type, raw_rows, header_rows, header_cols, header_col_candidates,
# columns는 이 모듈에서 절대 변경하지 않는다.
#================================================

from __future__ import annotations

from typing import Any

from .header_col_detector import is_value_like_cell
from .keyword_config import get_keyword_config

_NOT_APPLICABLE_CARRYOVER_CODES = frozenset(
    {"HEADER_ROWS_NOT_FOUND", "COLUMN_COUNT_MISMATCH", "LOW_CONFIDENCE_COLUMNS"}
)


def _first_record_looks_like_header(first_record: dict[str, Any]) -> bool:
    values = [v for v in first_record.get("values", {}).values() if v]
    if not values:
        return False

    non_value_texts = [v for v in values if not is_value_like_cell(v)]
    label_ratio = len(non_value_texts) / len(values)
    mostly_short_labels = label_ratio >= 0.6 and all(len(v) <= 15 for v in non_value_texts)

    header_like_keywords = get_keyword_config().header_like_keywords
    keyword_hits = sum(
        1 for v in values
        if any(keyword in v for keyword in header_like_keywords)
    )

    return mostly_short_labels or keyword_hits >= 2


def _count_duplicate_name_markers(columns: list[dict[str, Any]]) -> int:
    name_counts: dict[str, int] = {}
    for column in columns:
        name_counts[column["name"]] = name_counts.get(column["name"], 0) + 1
    return sum(count - 1 for count in name_counts.values() if count > 1)


def _has_possible_missing_multi_header(
    header_rows: list[int],
    header_row_candidates: list[dict[str, Any]],
) -> bool:
    if len(header_rows) != 1:
        return False

    two_row_candidates = [c for c in header_row_candidates if len(c.get("rows", [])) == 2]
    if not two_row_candidates:
        return False

    best_two_row = max(two_row_candidates, key=lambda c: c["score"])

    # A middling score alone is common noise (the same "below rows look like
    # data" bonus applies to almost any candidate). Only treat this as a real
    # missed multi-header when there is explicit merge-structure evidence -
    # the same signal header_row_detector itself uses to justify a 2-row header.
    has_merge_evidence = "candidate rows form a natural multi-level header" in best_two_row["reasons"]
    return has_merge_evidence and best_two_row["score"] >= 3


def _count_col_span_detected_records(structured_records: list[dict[str, Any]]) -> int:
    return sum(
        1
        for record in structured_records
        if "DATA_COL_SPAN_DETECTED" in record.get("warnings", [])
    )


def _empty_value_ratio(structured_records: list[dict[str, Any]]) -> float:
    total = 0
    empty = 0
    for record in structured_records:
        for value in record.get("values", {}).values():
            total += 1
            if value == "":
                empty += 1
    return (empty / total) if total else 0.0


def _carry_over_not_applicable_warnings(existing_warnings: list[Any]) -> list[dict[str, Any]]:
    return [
        warning
        for warning in existing_warnings
        if isinstance(warning, dict) and warning.get("code") in _NOT_APPLICABLE_CARRYOVER_CODES
    ]


def apply_record_stability_filter(
    table: dict[str, Any],
    header_rows: list[int],
    header_row_candidates: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    structured_records: list[dict[str, Any]],
    existing_warnings: list[Any],
) -> dict[str, Any]:
    """이미 생성된 structured_records를 검토해 유지/비움 상태를 결정한다."""
    result: dict[str, Any] = {
        "record_status": "not_applicable",
        "record_warnings": [],
        "structured_records": [],
    }

    if not header_rows:
        result["record_warnings"] = _carry_over_not_applicable_warnings(existing_warnings)
        return result

    if not columns:
        result["record_warnings"] = _carry_over_not_applicable_warnings(existing_warnings)
        result["record_warnings"].append(
            {
                "code": "COLUMNS_NOT_FOUND",
                "message": "Structured records were not applicable because no columns were built.",
            }
        )
        return result

    if not structured_records:
        result["record_warnings"] = _carry_over_not_applicable_warnings(existing_warnings)
        return result

    if table.get("table_id") in get_keyword_config().forced_raw_only_table_ids:
        result["record_status"] = "raw_only"
        result["record_warnings"] = [
            {
                "code": "MANUAL_RAW_ONLY_UNSTABLE_HEADER",
                "message": (
                    "Structured records were removed for a known unstable header "
                    "case confirmed against the original document (manual override)."
                ),
            }
        ]
        return result

    instability_warnings: list[dict[str, Any]] = []

    if _first_record_looks_like_header(structured_records[0]):
        instability_warnings.append(
            {
                "code": "FIRST_RECORD_LOOKS_LIKE_HEADER_ROW",
                "message": "Structured records were removed because the first record appears to be a lower header row.",
            }
        )

    if _count_duplicate_name_markers(columns) >= 2:
        instability_warnings.append(
            {
                "code": "EXCESSIVE_DUPLICATED_COLUMN_NAMES",
                "message": "Structured records were removed because column names were duplicated too many times.",
            }
        )

    if _has_possible_missing_multi_header(header_rows, header_row_candidates):
        instability_warnings.append(
            {
                "code": "POSSIBLE_MISSING_MULTI_HEADER_ROW",
                "message": "Structured records were removed because a multi-row header candidate may have been missed.",
            }
        )

    if _count_col_span_detected_records(structured_records) >= 2:
        instability_warnings.append(
            {
                "code": "UNSTABLE_DATA_COL_SPAN",
                "message": "Structured records were removed because too many data rows contain merged (col_span) cells.",
            }
        )

    if _empty_value_ratio(structured_records) >= 0.5:
        instability_warnings.append(
            {
                "code": "TOO_MANY_EMPTY_VALUES",
                "message": "Structured records were removed because too many values were empty.",
            }
        )

    if instability_warnings:
        result["record_status"] = "raw_only"
        result["record_warnings"] = instability_warnings
        return result

    result["record_status"] = "structured"
    result["structured_records"] = structured_records
    return result
