#================================================
# table_hierarchy/caption_classifier.py
# 캡션 / 각주 표 판정
#================================================

from __future__ import annotations

from typing import Any

from .cell_utils import get_non_empty_cells
from .table_utils import get_table_size

# startswith 전용: 표 전체가 이 마커로 시작할 때만 캡션 판정
CAPTION_OR_NOTE_PREFIXES = (
    "표 ",
    "<표",
    "[표",
    "〈표",
    "Table ",
    "자료:",
    "자료 :",
    "출처:",
    "출처 :",
    "주:",
    "주 :",
    "※",
    "단위:",
    "단위 :",
)

# contains 전용: "표 "는 본문 단어 오탐 위험이 있어 제외
_CAPTION_CONTAINS_PREFIXES = (
    "<표",
    "[표",
    "〈표",
    "Table ",
    "자료:",
    "자료 :",
    "출처:",
    "출처 :",
    "주:",
    "주 :",
    "※",
    "단위:",
    "단위 :",
)


def is_caption_or_note_table(table: dict[str, Any], text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False

    row_count, col_count = get_table_size(table)
    non_empty_count = len(get_non_empty_cells(table))

    if row_count >= 3 or col_count >= 3:
        return False

    if non_empty_count >= 4:
        return False

    if _starts_with_caption_prefix(stripped):
        return True

    if non_empty_count <= 3 and _contains_caption_prefix(stripped):
        return True

    return False


def _starts_with_caption_prefix(text: str) -> bool:
    lower_text = text.lower()
    for prefix in CAPTION_OR_NOTE_PREFIXES:
        if lower_text.startswith(prefix.lower()):
            return True
    return False


def _contains_caption_prefix(text: str) -> bool:
    lower_text = text.lower()
    for prefix in _CAPTION_CONTAINS_PREFIXES:
        if prefix.lower() in lower_text:
            return True
    return False
