#================================================
# table_hierarchy/warning_normalizer.py
# 7단계: data_table hierarchy의 warning 기록/정규화
#
# 이 모듈은 어떤 판정 로직도 다시 실행하지 않는다.
# table_type, raw_rows, header_rows, header_cols, header_col_candidates,
# columns, structured_records, record_status는 여기서 절대 재계산하지 않고
# 오직 이미 기록된 warning들을 정규화하고, record_status의 근거를
# 사람이 추적 가능하도록 record_warnings에 보강할 뿐이다.
#================================================

from __future__ import annotations

from typing import Any

from .header_col_detector import _estimate_col_count

DEFAULT_STAGE = "warning_normalization"
DEFAULT_SEVERITY = "info"

# code -> (stage, severity) 기본값. 이미 stage/severity가 있는 warning에는 쓰이지 않고,
# 누락된 경우에만 채워 넣는 최소한의 보강용 표다.
_CODE_DEFAULTS: dict[str, tuple[str, str]] = {
    # header_rows_detection
    "NO_RAW_ROWS": ("header_rows_detection", "info"),
    "SINGLE_ROW_TABLE": ("header_rows_detection", "info"),
    "FULL_WIDTH_TEXT_ROW": ("header_rows_detection", "info"),
    "KEY_VALUE_LIKE_TABLE": ("header_rows_detection", "info"),
    "DATA_LIKE_FIRST_ROW": ("header_rows_detection", "info"),
    "TOO_FEW_DATA_ROWS": ("header_rows_detection", "info"),
    "UNSTABLE_HEADER_ROWS": ("header_rows_detection", "info"),
    "AMBIGUOUS_HEADER_ROWS": ("header_rows_detection", "info"),
    # header_cols_detection
    "SINGLE_COLUMN_TABLE": ("header_cols_detection", "info"),
    "TOO_FEW_DATA_ROWS_FOR_HEADER_COLS": ("header_cols_detection", "info"),
    "INDEX_LIKE_FIRST_COLUMN": ("header_cols_detection", "info"),
    "CODE_LIKE_FIRST_COLUMN": ("header_cols_detection", "info"),
    "DATE_LIKE_FIRST_COLUMN": ("header_cols_detection", "info"),
    "UNSTABLE_HEADER_COLS": ("header_cols_detection", "info"),
    # columns_generation
    "HEADER_SPAN_CONFLICT": ("columns_generation", "warning"),
    "EMPTY_HEADER_TEXT": ("columns_generation", "info"),
    "FALLBACK_COLUMN_NAME": ("columns_generation", "warning"),
    # structured_records_generation
    "HEADER_ROWS_NOT_FOUND": ("structured_records_generation", "warning"),
    "COLUMN_COUNT_MISMATCH": ("structured_records_generation", "warning"),
    "LOW_CONFIDENCE_COLUMNS": ("structured_records_generation", "info"),
    "DATA_ROW_BEFORE_HEADER": ("structured_records_generation", "warning"),
    "DUPLICATED_COLUMN_NAME": ("structured_records_generation", "warning"),
    "EMPTY_DATA_ROW": ("structured_records_generation", "info"),
    "MISSING_CELL_FOR_COLUMN": ("structured_records_generation", "info"),
    "DATA_COL_SPAN_DETECTED": ("structured_records_generation", "info"),
    # structured_records_stability_filter
    "FIRST_RECORD_LOOKS_LIKE_HEADER_ROW": ("structured_records_stability_filter", "warning"),
    "EXCESSIVE_DUPLICATED_COLUMN_NAMES": ("structured_records_stability_filter", "warning"),
    "POSSIBLE_MISSING_MULTI_HEADER_ROW": ("structured_records_stability_filter", "warning"),
    "UNSTABLE_DATA_COL_SPAN": ("structured_records_stability_filter", "warning"),
    "TOO_MANY_EMPTY_VALUES": ("structured_records_stability_filter", "warning"),
    "MANUAL_RAW_ONLY_UNSTABLE_HEADER": ("structured_records_stability_filter", "warning"),
    "RAW_ONLY_WITHOUT_REASON": ("structured_records_stability_filter", "warning"),
    "COLUMNS_NOT_FOUND": ("structured_records_stability_filter", "info"),
    # warning_normalization
    "RECORD_STATUS_CONFLICT": ("warning_normalization", "error"),
    "STRUCTURED_RECORDS_EMPTY_BUT_STATUS_STRUCTURED": ("warning_normalization", "error"),
    "STRUCTURED_RECORDS_PRESENT_BUT_STATUS_NOT_APPLICABLE": ("warning_normalization", "error"),
    "STRUCTURED_RECORDS_PRESENT_BUT_STATUS_RAW_ONLY": ("warning_normalization", "error"),
    "UNKNOWN_RECORD_STATUS": ("warning_normalization", "error"),
    "TOO_FEW_DATA_ROWS_FOR_STRUCTURED_RECORDS": ("warning_normalization", "info"),
}

_NOT_APPLICABLE_REASON_CODES = (
    "HEADER_ROWS_NOT_FOUND",
    "COLUMNS_NOT_FOUND",
    "LOW_CONFIDENCE_COLUMNS",
    "COLUMN_COUNT_MISMATCH",
)


def _normalize_entry(entry: Any) -> dict[str, Any]:
    if isinstance(entry, str):
        code = entry.strip().upper()
        message = entry.replace("_", " ").strip()
        stage, severity = _CODE_DEFAULTS.get(code, (DEFAULT_STAGE, DEFAULT_SEVERITY))
        return {"code": code, "message": message, "stage": stage, "severity": severity}

    code = str(entry.get("code", "UNKNOWN_WARNING"))
    message = entry.get("message", "")
    default_stage, default_severity = _CODE_DEFAULTS.get(code, (DEFAULT_STAGE, DEFAULT_SEVERITY))
    return {
        "code": code,
        "message": message,
        "stage": entry.get("stage") or default_stage,
        "severity": entry.get("severity") or default_severity,
    }


def _normalize_and_dedupe(entries: list[Any]) -> list[dict[str, Any]]:
    normalized = [_normalize_entry(entry) for entry in entries]
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for warning in normalized:
        if warning["code"] in seen:
            continue
        seen.add(warning["code"])
        result.append(warning)
    return result


def _determine_not_applicable_reason(
    raw_rows: list[dict[str, Any]],
    header_rows: list[int],
    columns: list[dict[str, Any]],
) -> tuple[str, str] | None:
    if not header_rows:
        return (
            "HEADER_ROWS_NOT_FOUND",
            "Structured records are not applicable because header_rows is empty.",
        )

    if not columns:
        return (
            "COLUMNS_NOT_FOUND",
            "Structured records are not applicable because no columns were built.",
        )

    if any(column.get("confidence") == "low" for column in columns):
        return (
            "LOW_CONFIDENCE_COLUMNS",
            "Structured records are not applicable because some columns have low confidence.",
        )

    col_count = _estimate_col_count(raw_rows)
    if len(columns) != col_count:
        return (
            "COLUMN_COUNT_MISMATCH",
            "Structured records are not applicable because columns length does not match the column count.",
        )

    header_set = set(header_rows)
    data_rows = [row for row in raw_rows if row["row_addr"] not in header_set]
    if not data_rows:
        return (
            "TOO_FEW_DATA_ROWS_FOR_STRUCTURED_RECORDS",
            "Structured records are not applicable because there are no data rows after the header.",
        )

    if len(raw_rows) <= 1:
        return (
            "SINGLE_ROW_TABLE",
            "Structured records are not applicable because the table has only one row.",
        )

    if col_count <= 1:
        return (
            "SINGLE_COLUMN_TABLE",
            "Structured records are not applicable because the table has only one column.",
        )

    return None


def _find_carryover_reason(quality_warnings: list[dict[str, Any]]) -> tuple[str, str] | None:
    quality_by_code = {w["code"]: w for w in quality_warnings}
    for code in _NOT_APPLICABLE_REASON_CODES:
        if code in quality_by_code:
            return code, quality_by_code[code].get("message", "")
    return None


def _check_status_consistency(
    record_status: str,
    structured_records: list[dict[str, Any]],
) -> tuple[str, str, str] | None:
    if record_status == "structured" and not structured_records:
        return (
            "RECORD_STATUS_CONFLICT",
            "record_status is 'structured' but structured_records is empty.",
            "error",
        )
    if record_status == "raw_only" and structured_records:
        return (
            "STRUCTURED_RECORDS_PRESENT_BUT_STATUS_RAW_ONLY",
            "record_status is 'raw_only' but structured_records is not empty.",
            "error",
        )
    if record_status == "not_applicable" and structured_records:
        return (
            "STRUCTURED_RECORDS_PRESENT_BUT_STATUS_NOT_APPLICABLE",
            "record_status is 'not_applicable' but structured_records is not empty.",
            "error",
        )
    if record_status not in {"structured", "raw_only", "not_applicable"}:
        return (
            "UNKNOWN_RECORD_STATUS",
            f"Unknown record_status value: {record_status!r}.",
            "error",
        )
    return None


def _summarize_repeated_record_warnings(structured_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in structured_records:
        for code in record.get("warnings", []):
            counts[code] = counts.get(code, 0) + 1

    summaries: list[dict[str, Any]] = []
    for code in ("MISSING_CELL_FOR_COLUMN", "DATA_COL_SPAN_DETECTED"):
        if counts.get(code, 0) >= 2:
            stage, severity = _CODE_DEFAULTS.get(code, (DEFAULT_STAGE, DEFAULT_SEVERITY))
            summaries.append(
                {
                    "code": code,
                    "message": f"{code} occurred in {counts[code]} structured_records rows.",
                    "stage": stage,
                    "severity": severity,
                }
            )
    return summaries


def normalize_hierarchy_warnings(hierarchy: dict[str, Any]) -> None:
    """이미 기록된 quality.warnings/record_warnings를 정규화하고, record_status의 근거를 보강한다."""
    quality = hierarchy.setdefault("quality", {})
    quality["warnings"] = _normalize_and_dedupe(quality.get("warnings", []))

    record_warnings = _normalize_and_dedupe(hierarchy.get("record_warnings", []))
    had_reason_before = bool(record_warnings)

    record_status = hierarchy.get("record_status")
    structured_records = hierarchy.get("structured_records", [])
    columns = hierarchy.get("columns", [])
    header_rows = hierarchy.get("header_rows", [])
    raw_rows = hierarchy.get("raw_rows", [])

    if record_status == "raw_only" and not had_reason_before:
        record_warnings.append(
            {
                "code": "RAW_ONLY_WITHOUT_REASON",
                "message": "record_status is 'raw_only' but no reason warning was recorded.",
                "stage": "structured_records_stability_filter",
                "severity": "warning",
            }
        )

    if record_status == "not_applicable" and not had_reason_before:
        reason = _find_carryover_reason(quality["warnings"])
        if reason is None:
            reason = _determine_not_applicable_reason(raw_rows, header_rows, columns)
        if reason is not None:
            code, message = reason
            stage, severity = _CODE_DEFAULTS.get(code, (DEFAULT_STAGE, DEFAULT_SEVERITY))
            record_warnings.append(
                {"code": code, "message": message, "stage": stage, "severity": severity}
            )

    if record_status == "structured" and structured_records:
        record_warnings.extend(_summarize_repeated_record_warnings(structured_records))

        duplicated_name_warning = next(
            (w for w in quality["warnings"] if w["code"] == "DUPLICATED_COLUMN_NAME"),
            None,
        )
        if duplicated_name_warning is not None:
            record_warnings.append(dict(duplicated_name_warning))

    conflict = _check_status_consistency(record_status, structured_records)
    if conflict is not None:
        code, message, severity = conflict
        record_warnings.append(
            {"code": code, "message": message, "stage": "warning_normalization", "severity": severity}
        )

    hierarchy["record_warnings"] = _normalize_and_dedupe(record_warnings)
