#================================================
# table_hierarchy/header_col_detector.py
# raw_rows + header_rows 기반 data_table header_cols 판정
#
# 목표: 모든 표에 행 헤더 열을 억지로 붙이지 않는다.
# 왼쪽 열이 각 데이터 행을 설명하는 행 헤더라고 안정적으로 판단되는 경우에만
# header_cols를 생성하고, 애매한 경우에는 header_cols를 비워 둔다.
#================================================

from __future__ import annotations

import re
from typing import Any

from .cell_utils import _avg_len, normalize_text
from .header_row_detector import _DATA_PATTERN, _is_numeric_like, is_key_value_like_table
from .keyword_config import get_keyword_config

_VALUE_MARKS = frozenset({"-", "O", "X", "o", "x", "△", "▲", "✓", "ㅇ", "ㅁ"})
_UNIT_VALUE_PATTERN = re.compile(r"\d+\s*(건|명|점|원|천원|만원|개|회|년|월|일)")

_INDEX_HEADER_KEYWORDS = frozenset({"번호", "순번", "No.", "No", "NO", "연번"})
_CODE_HEADER_KEYWORDS = frozenset({"코드", "ID", "Code", "기호"})
_DATE_HEADER_KEYWORDS = frozenset({"연도", "년도", "날짜", "일자", "기간"})

_CODE_PATTERN = re.compile(r"^[A-Za-z]{1,4}-?\d{1,6}$")
_DATE_PATTERN = re.compile(r"^\d{4}(\s*년)?$|^\d{4}[.\-/]\d{1,2}([.\-/]\d{1,2})?$")


def is_value_like_cell(text: str) -> bool:
    t = normalize_text(text)
    if not t:
        return False
    if t in _VALUE_MARKS:
        return True
    if _is_numeric_like(t):
        return True
    if _DATA_PATTERN.search(t):
        return True
    if _UNIT_VALUE_PATTERN.search(t):
        return True
    return False


def is_label_like_col_value(text: str) -> bool:
    t = normalize_text(text)
    if not t:
        return False
    if len(t) > 15:
        return False
    if is_value_like_cell(t):
        return False

    if any(keyword in t for keyword in get_keyword_config().effective_label_col_keywords):
        return True

    if not any(ch.isdigit() for ch in t) and len(t) <= 12:
        return True

    return False


def is_index_like_column(values: list[str], header_text: str | None = None) -> bool:
    header_t = normalize_text(header_text) if header_text else ""
    if any(keyword in header_t for keyword in _INDEX_HEADER_KEYWORDS):
        return True

    numbers: list[int] = []
    for value in values:
        s = normalize_text(value)
        if not s or not s.isascii() or not s.isdigit():
            return False
        numbers.append(int(s))

    if len(numbers) < 2:
        return False

    increasing = all(b - a == 1 for a, b in zip(numbers, numbers[1:]))
    starts_low = numbers[0] in (0, 1)
    return increasing and starts_low


def is_code_like_column(values: list[str], header_text: str | None = None) -> bool:
    header_t = normalize_text(header_text) if header_text else ""
    header_hit = any(keyword in header_t for keyword in _CODE_HEADER_KEYWORDS)

    non_empty = [normalize_text(value) for value in values if normalize_text(value)]
    if not non_empty:
        return header_hit

    matches = sum(1 for value in non_empty if _CODE_PATTERN.match(value))
    ratio = matches / len(non_empty)
    return header_hit or ratio >= 0.6


def is_date_like_column(values: list[str], header_text: str | None = None) -> bool:
    header_t = normalize_text(header_text) if header_text else ""
    header_hit = any(keyword in header_t for keyword in _DATE_HEADER_KEYWORDS)

    non_empty = [normalize_text(value) for value in values if normalize_text(value)]
    if not non_empty:
        return header_hit

    matches = sum(1 for value in non_empty if _DATE_PATTERN.match(value))
    ratio = matches / len(non_empty)
    return header_hit or ratio >= 0.6


def _estimate_col_count(raw_rows: list[dict[str, Any]]) -> int:
    max_extent = 0
    for row in raw_rows:
        for col_addr, col_span in zip(row.get("col_addrs", []), row.get("col_spans", [])):
            max_extent = max(max_extent, (col_addr or 0) + (col_span or 1))
    return max_extent


def _get_data_rows(raw_rows: list[dict[str, Any]], header_rows: list[int]) -> list[dict[str, Any]]:
    header_set = set(header_rows or [])
    return [row for row in raw_rows if row["row_addr"] not in header_set]


def _get_col_values(rows: list[dict[str, Any]], col_index: int) -> list[str]:
    values: list[str] = []
    for row in rows:
        cols = row.get("col_addrs", [])
        spans = row.get("col_spans", [])
        texts = row.get("texts", [])
        for col_addr, span, text in zip(cols, spans, texts):
            if col_addr <= col_index < col_addr + span:
                values.append(text)
                break
    return values


def _split_row_by_candidate(
    row: dict[str, Any],
    candidate_cols: list[int],
) -> tuple[list[str], list[str], list[int]]:
    max_col = max(candidate_cols)
    left_texts: list[str] = []
    right_texts: list[str] = []
    left_row_spans: list[int] = []

    cols = row.get("col_addrs", [])
    spans = row.get("col_spans", [])
    texts = row.get("texts", [])
    row_spans = row.get("row_spans", [])

    for col_addr, span, text, row_span in zip(cols, spans, texts, row_spans):
        if col_addr <= max_col:
            left_texts.append(text)
            left_row_spans.append(row_span)
        else:
            right_texts.append(text)

    return left_texts, right_texts, left_row_spans


def build_header_col_candidates(
    raw_rows: list[dict[str, Any]],
) -> list[list[int]]:
    """표 왼쪽의 연속된 header_cols 후보(0-based column index 목록)를 생성한다."""
    col_count = _estimate_col_count(raw_rows)

    if col_count <= 1:
        return []

    if col_count == 2:
        return [[0]]

    return [[0], [0, 1]]


def score_header_col_candidate(
    candidate_cols: list[int],
    raw_rows: list[dict[str, Any]],
    header_rows: list[int],
) -> dict[str, Any]:
    data_rows = _get_data_rows(raw_rows, header_rows)
    header_row_objs = [row for row in raw_rows if row["row_addr"] in set(header_rows or [])]

    left_values_per_row: list[list[str]] = []
    right_values_per_row: list[list[str]] = []
    left_row_spans_flat: list[int] = []

    for row in data_rows:
        left_texts, right_texts, left_row_spans = _split_row_by_candidate(row, candidate_cols)
        left_values_per_row.append(left_texts)
        right_values_per_row.append(right_texts)
        left_row_spans_flat.extend(left_row_spans)

    flat_left = [text for texts in left_values_per_row for text in texts if text]
    flat_right = [text for texts in right_values_per_row for text in texts if text]

    header_left_texts: list[str] = []
    for header_row in header_row_objs:
        left_texts, _, _ = _split_row_by_candidate(header_row, candidate_cols)
        header_left_texts.extend(left_texts)
    header_text_joined = " ".join(text for text in header_left_texts if text)

    col0_data_values = _get_col_values(data_rows, 0)
    col0_header_values = _get_col_values(header_row_objs, 0)
    col0_header_text = " ".join(text for text in col0_header_values if text)

    index_like = is_index_like_column(col0_data_values, col0_header_text)
    code_like = is_code_like_column(col0_data_values, col0_header_text)
    date_like = is_date_like_column(col0_data_values, col0_header_text)
    kv_like = is_key_value_like_table(raw_rows)

    score = 0
    reasons: list[str] = []

    label_count = sum(1 for text in flat_left if is_label_like_col_value(text))
    label_ratio = label_count / len(flat_left) if flat_left else 0.0
    if label_ratio >= 0.6:
        score += 2
        reasons.append("first column contains label-like row descriptors")

    value_count = sum(1 for text in flat_right if is_value_like_cell(text))
    value_ratio = value_count / len(flat_right) if flat_right else 0.0
    if value_ratio >= 0.6:
        score += 2
        reasons.append("right-side cells look like data values")

    if header_text_joined and any(
        keyword in header_text_joined
        for keyword in get_keyword_config().header_name_keywords
    ):
        score += 1
        reasons.append("header row label for this column is a descriptor keyword")

    if flat_left and _avg_len(flat_left) <= 8:
        score += 1
        reasons.append("candidate column values are short noun phrases")

    if flat_left:
        uniqueness_ratio = len(set(flat_left)) / len(flat_left)
        if uniqueness_ratio >= 0.8:
            score += 1
            reasons.append("candidate column values are distributed like unique item names")

    if any(span > 1 for span in left_row_spans_flat):
        score += 1
        reasons.append("candidate column has row-span grouping typical of grouped row headers")

    if len(candidate_cols) == 2:
        hierarchical_rows = sum(
            1
            for texts in left_values_per_row
            if len(texts) >= 2 and all(is_label_like_col_value(t) or not t for t in texts[:2])
        )
        if data_rows and hierarchical_rows / len(data_rows) >= 0.6 and value_ratio >= 0.5:
            score += 1
            reasons.append("both candidate columns act as hierarchical row labels")

    if index_like:
        score -= 4
        reasons.append("candidate column looks like an index/sequence column")

    if code_like:
        score -= 3
        reasons.append("candidate column looks like a code/ID column")

    if date_like:
        score -= 2
        reasons.append("candidate column looks like a date/year column")

    if kv_like:
        score -= 3
        reasons.append("table looks like a key-value table")

    if flat_right:
        long_right_ratio = sum(1 for text in flat_right if len(text) > 20) / len(flat_right)
        if long_right_ratio > 0.5 and value_ratio < 0.3:
            score -= 2
            reasons.append("right-side cells are long sentences with no structural distinction from the candidate column")

    if len(data_rows) <= 1:
        score -= 2
        reasons.append("too few data rows to evaluate header columns")

    if score >= 5:
        confidence = "high"
    elif score >= 4:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "cols": list(candidate_cols),
        "score": score,
        "confidence": confidence,
        "reasons": reasons,
    }


def _pick_best_candidate(accepted: list[dict[str, Any]]) -> dict[str, Any]:
    best_score = max(candidate["score"] for candidate in accepted)
    best_group = [candidate for candidate in accepted if candidate["score"] == best_score]
    best_group.sort(key=lambda candidate: len(candidate["cols"]))

    top = best_group[0]

    has_hierarchical_evidence = "both candidate columns act as hierarchical row labels" in top["reasons"]
    if len(top["cols"]) > 1 and not has_hierarchical_evidence:
        single_col_candidates = [candidate for candidate in accepted if len(candidate["cols"]) == 1]
        if single_col_candidates:
            best_single = max(single_col_candidates, key=lambda candidate: candidate["score"])
            if best_score - best_single["score"] <= 1:
                top = best_single

    return top


def _pick_rejection_reason(
    raw_rows: list[dict[str, Any]],
    header_rows: list[int],
    data_rows: list[dict[str, Any]],
    scored_candidates: list[dict[str, Any]],
    threshold: int,
) -> tuple[str, str] | tuple[None, None]:
    header_row_objs = [row for row in raw_rows if row["row_addr"] in set(header_rows or [])]
    col0_data_values = _get_col_values(data_rows, 0)
    col0_header_text = " ".join(text for text in _get_col_values(header_row_objs, 0) if text)

    if is_key_value_like_table(raw_rows):
        return (
            "KEY_VALUE_LIKE_TABLE",
            "Header columns were not assigned because the table looks like a key-value table.",
        )

    if is_index_like_column(col0_data_values, col0_header_text):
        return (
            "INDEX_LIKE_FIRST_COLUMN",
            "Header columns were not assigned because the first column looks like an index/sequence column.",
        )

    if is_code_like_column(col0_data_values, col0_header_text):
        return (
            "CODE_LIKE_FIRST_COLUMN",
            "Header columns were not assigned because the first column looks like a code/ID column.",
        )

    if is_date_like_column(col0_data_values, col0_header_text):
        return (
            "DATE_LIKE_FIRST_COLUMN",
            "Header columns were not assigned because the first column looks like a date/year column.",
        )

    best_score = max((candidate["score"] for candidate in scored_candidates), default=None)
    if best_score is not None and best_score >= threshold - 2:
        return (
            "UNSTABLE_HEADER_COLS",
            "Header columns were not assigned because the table structure is ambiguous.",
        )

    return None, None


def detect_header_cols(
    table: dict[str, Any],
    raw_rows: list[dict[str, Any]],
    header_rows: list[int],
) -> dict[str, Any]:
    """raw_rows와 header_rows를 기반으로 data_table의 header_cols를 판정한다."""
    result: dict[str, Any] = {
        "header_cols": [],
        "header_col_candidates": [],
        "warnings": [],
    }

    if not raw_rows:
        return result

    col_count = _estimate_col_count(raw_rows)

    if col_count <= 1:
        result["warnings"].append(
            {
                "code": "SINGLE_COLUMN_TABLE",
                "message": "Header columns were not assigned because the table has only one column.",
            }
        )
        return result

    data_rows = _get_data_rows(raw_rows, header_rows)
    if len(data_rows) <= 1:
        result["warnings"].append(
            {
                "code": "TOO_FEW_DATA_ROWS_FOR_HEADER_COLS",
                "message": "Header columns were not assigned because there are too few data rows to evaluate.",
            }
        )
        return result

    candidates = build_header_col_candidates(raw_rows)
    if not candidates:
        return result

    scored_candidates = [
        score_header_col_candidate(candidate, raw_rows, header_rows)
        for candidate in candidates
    ]
    result["header_col_candidates"] = scored_candidates

    threshold = 5 if col_count == 2 else 4
    accepted = [candidate for candidate in scored_candidates if candidate["score"] >= threshold]

    if not accepted:
        code, message = _pick_rejection_reason(raw_rows, header_rows, data_rows, scored_candidates, threshold)
        if code is not None:
            result["warnings"].append({"code": code, "message": message})
        return result

    best = _pick_best_candidate(accepted)
    result["header_cols"] = best["cols"]
    return result
