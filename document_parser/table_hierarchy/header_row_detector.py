#================================================
# table_hierarchy/header_row_detector.py
# raw_rows 기반 data_table header_rows 판정
#
# 목표: 모든 표에 헤더를 억지로 붙이지 않는다.
# 안정적으로 컬럼 헤더라고 판단되는 경우에만 header_rows를 생성하고,
# 애매한 경우에는 header_rows를 비워 두고 warnings에 이유를 남긴다.
#================================================

from __future__ import annotations

import re
from typing import Any

from .cell_utils import _avg_len, normalize_text
from .keyword_config import get_keyword_config
from .table_utils import get_table_size

_DATA_PATTERN = re.compile(
    r"(\d{4}\s*[.\-/년]\s*\d{1,2}|\d{1,4}[.\-/]\d{1,2}([.\-/]\d{1,4})?|\d+(\.\d+)?\s*%|\d+\s*(원|건|명|개))"
)
_NUMERIC_PATTERN = re.compile(r"^[+-]?\d{1,3}(,\d{3})*(\.\d+)?$|^[+-]?\d+(\.\d+)?$")


def _is_numeric_like(text: str) -> bool:
    stripped = normalize_text(text).replace(" ", "")
    if not stripped or stripped == "-":
        return False
    return bool(_NUMERIC_PATTERN.match(stripped))


def is_label_like_cell(text: str) -> bool:
    t = normalize_text(text)
    if not t:
        return False
    if len(t) > 12:
        return False

    if any(keyword in t for keyword in get_keyword_config().effective_label_keywords):
        return True

    if len(t) <= 6 and " " not in t and not any(ch.isdigit() for ch in t):
        return True

    return False


def is_data_like_row(row: dict[str, Any]) -> bool:
    texts = [t for t in row.get("texts", []) if t]
    if not texts:
        return False

    data_like_count = 0
    for text in texts:
        if _DATA_PATTERN.search(text) or _is_numeric_like(text):
            data_like_count += 1
        elif len(text) > 20:
            data_like_count += 1

    return data_like_count >= max(1, len(texts) // 2)


def is_full_width_text_row(row: dict[str, Any], table: dict[str, Any]) -> bool:
    cell_ids = row.get("cell_ids", [])
    col_spans = row.get("col_spans", [])
    texts = row.get("texts", [])

    if len(cell_ids) != 1:
        return False

    _, col_count = get_table_size(table)
    text = texts[0] if texts else ""
    col_span = col_spans[0] if col_spans else 1

    is_wide = bool(col_count) and col_span >= col_count
    is_sentence_like = len(text) > 15 or " " in text

    return is_wide and is_sentence_like


def is_key_value_like_table(raw_rows: list[dict[str, Any]]) -> bool:
    if len(raw_rows) < 2:
        return False

    two_col_rows = 0
    label_first_rows = 0

    for row in raw_rows:
        cell_ids = row.get("cell_ids", [])
        texts = row.get("texts", [])
        if len(cell_ids) != 2:
            continue

        two_col_rows += 1
        first_text = texts[0] if texts else ""
        second_text = texts[1] if len(texts) > 1 else ""
        if is_label_like_cell(first_text) and len(second_text) > len(first_text):
            label_first_rows += 1

    if two_col_rows == 0:
        return False

    ratio_two_col = two_col_rows / len(raw_rows)
    ratio_label_first = label_first_rows / two_col_rows

    return ratio_two_col >= 0.7 and ratio_label_first >= 0.7


def _is_natural_multirow_header(candidate_rows: list[dict[str, Any]]) -> bool:
    if len(candidate_rows) < 2:
        return False

    first, second = candidate_rows[0], candidate_rows[1]
    first_has_span = any(span > 1 for span in first.get("col_spans", []))
    second_more_cells = len(second.get("cell_ids", [])) > len(first.get("cell_ids", []))
    second_not_data_like = not is_data_like_row(second)

    return first_has_span and second_more_cells and second_not_data_like


def build_header_row_candidates(raw_rows: list[dict[str, Any]]) -> list[list[int]]:
    """표 상단의 연속된 header 후보(row_addr 목록)를 생성한다."""
    sorted_addrs = sorted(row["row_addr"] for row in raw_rows)
    total_rows = len(sorted_addrs)

    if total_rows <= 1:
        return []

    if total_rows == 2:
        return [[sorted_addrs[0]]]

    candidates: list[list[int]] = []
    for length in (1, 2, 3):
        if length >= total_rows:
            break
        candidates.append(sorted_addrs[:length])

    return candidates


def score_header_row_candidate(
    candidate: list[int],
    raw_rows: list[dict[str, Any]],
    table: dict[str, Any],
) -> dict[str, Any]:
    row_by_addr = {row["row_addr"]: row for row in raw_rows}
    candidate_rows = [row_by_addr[addr] for addr in candidate if addr in row_by_addr]
    below_rows = [row for row in raw_rows if row["row_addr"] > max(candidate)]

    score = 0
    reasons: list[str] = []

    all_texts = [text for row in candidate_rows for text in row.get("texts", []) if text]
    label_count = sum(1 for text in all_texts if is_label_like_cell(text))
    label_ratio = label_count / len(all_texts) if all_texts else 0.0

    if label_ratio >= 0.5:
        score += 2
        reasons.append("candidate rows have a high ratio of label-like cells")

    if below_rows:
        data_like_count = sum(1 for row in below_rows if is_data_like_row(row))
        if data_like_count >= max(1, len(below_rows) // 2):
            score += 2
            reasons.append("following rows look like data rows")

    if all_texts and _avg_len(all_texts) <= 8 and label_ratio > 0:
        score += 1
        reasons.append("candidate rows are short and noun-centric")

    if _is_natural_multirow_header(candidate_rows):
        score += 1
        reasons.append("candidate rows form a natural multi-level header")

    if below_rows:
        column_shapes = {len(row.get("cell_ids", [])) for row in below_rows[:5]}
        if len(column_shapes) <= 1:
            score += 1
            reasons.append("data rows below repeat the same column structure")

    long_text_count = sum(1 for text in all_texts if len(text) > 20)
    if all_texts and long_text_count / len(all_texts) > 0.3:
        score -= 2
        reasons.append("candidate rows contain long sentence-like text")

    if any(is_full_width_text_row(row, table) for row in candidate_rows):
        score -= 3
        reasons.append("candidate row looks like a full-width description row")

    if len(raw_rows) <= 2:
        score -= 2
        reasons.append("table has too few rows overall")

    if is_key_value_like_table(raw_rows):
        score -= 3
        reasons.append("table looks like a key-value table")

    if candidate_rows and is_data_like_row(candidate_rows[0]):
        score -= 3
        reasons.append("first row looks like a data row rather than a header")

    if len(below_rows) <= 1:
        score -= 1
        reasons.append("too few data rows exist below the header candidate")

    if score >= 5:
        confidence = "high"
    elif score >= 4:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "rows": list(candidate),
        "score": score,
        "confidence": confidence,
        "reasons": reasons,
    }


def _pick_best_candidate(accepted: list[dict[str, Any]]) -> dict[str, Any]:
    best_score = max(candidate["score"] for candidate in accepted)
    best_group = [candidate for candidate in accepted if candidate["score"] == best_score]
    best_group.sort(key=lambda candidate: len(candidate["rows"]))

    # rule 2: equal score -> prefer the shorter candidate
    top = best_group[0]

    # rule 3/4: a multi-row candidate is only trusted outright when it carries
    # explicit merge evidence. Otherwise fall back to a near-scoring single-row
    # candidate (this only fires when the multi-row candidate was the unique
    # top scorer, since ties already resolved to the shorter candidate above).
    has_merge_evidence = "candidate rows form a natural multi-level header" in top["reasons"]
    if len(top["rows"]) > 1 and not has_merge_evidence:
        single_row_candidates = [candidate for candidate in accepted if len(candidate["rows"]) == 1]
        if single_row_candidates:
            best_single = max(single_row_candidates, key=lambda candidate: candidate["score"])
            if best_score - best_single["score"] <= 1:
                top = best_single

    return top


def _pick_rejection_reason(
    raw_rows: list[dict[str, Any]],
    table: dict[str, Any],
    scored_candidates: list[dict[str, Any]],
) -> tuple[str, str]:
    row_by_addr = {row["row_addr"]: row for row in raw_rows}
    first_row = row_by_addr.get(min(row_by_addr)) if row_by_addr else None

    if is_key_value_like_table(raw_rows):
        return (
            "KEY_VALUE_LIKE_TABLE",
            "Header rows were not assigned because the table looks like a key-value table.",
        )

    if first_row is not None and is_full_width_text_row(first_row, table):
        return (
            "FULL_WIDTH_TEXT_ROW",
            "Header rows were not assigned because the first row looks like a full-width description row.",
        )

    if first_row is not None and is_data_like_row(first_row):
        return (
            "DATA_LIKE_FIRST_ROW",
            "Header rows were not assigned because the first row looks like a data row.",
        )

    if scored_candidates and all(
        len([row for row in raw_rows if row["row_addr"] > max(candidate["rows"])]) <= 1
        for candidate in scored_candidates
    ):
        return (
            "TOO_FEW_DATA_ROWS",
            "Header rows were not assigned because there are too few data rows below the header candidate.",
        )

    return (
        "UNSTABLE_HEADER_ROWS",
        "Header rows were not assigned because the table structure is ambiguous.",
    )


def detect_header_rows(table: dict[str, Any], raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """raw_rows를 기반으로 data_table의 header_rows를 판정한다."""
    result: dict[str, Any] = {
        "header_rows": [],
        "header_row_candidates": [],
        "warnings": [],
    }

    if not raw_rows:
        result["warnings"].append(
            {
                "code": "NO_RAW_ROWS",
                "message": "Header rows were not assigned because raw_rows is empty.",
            }
        )
        return result

    total_rows = len(raw_rows)

    if total_rows == 1:
        result["warnings"].append(
            {
                "code": "SINGLE_ROW_TABLE",
                "message": "Header rows were not assigned because the table has only one row.",
            }
        )
        return result

    candidates = build_header_row_candidates(raw_rows)
    scored_candidates = [
        score_header_row_candidate(candidate, raw_rows, table)
        for candidate in candidates
    ]
    result["header_row_candidates"] = scored_candidates

    threshold = 5 if total_rows == 2 else 4
    accepted = [candidate for candidate in scored_candidates if candidate["score"] >= threshold]

    if not accepted:
        code, message = _pick_rejection_reason(raw_rows, table, scored_candidates)
        result["warnings"].append({"code": code, "message": message})
        return result

    best = _pick_best_candidate(accepted)
    result["header_rows"] = best["rows"]
    return result


def append_warning(
    hierarchy: dict[str, Any],
    code: str,
    message: str,
    stage: str | None = None,
    severity: str | None = None,
) -> None:
    quality = hierarchy.setdefault("quality", {})
    warnings = quality.setdefault("warnings", [])

    for existing in warnings:
        if isinstance(existing, dict) and existing.get("code") == code:
            return

    warning: dict[str, Any] = {"code": code, "message": message}
    if stage is not None:
        warning["stage"] = stage
    if severity is not None:
        warning["severity"] = severity
    warnings.append(warning)
