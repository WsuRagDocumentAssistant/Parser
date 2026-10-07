#================================================
# table_hierarchy/classify.py
# 표 타입(classify_table) 종합 판정
#
# title/caption/data/key_value classifier를 기존 순서 그대로 호출하여 조합한다.
# 판정 순서를 절대 바꾸지 않는다.
#================================================

from __future__ import annotations

from typing import Any

from .caption_classifier import is_caption_or_note_table
from .cell_utils import get_all_text, get_non_empty_cells
from .data_classifier import _is_data_table_priority
from .key_value_classifier import is_key_value_table
from .title_classifier import (
    _is_priority_title_box,
    _is_title_box_condition1,
    _is_title_box_condition2,
    is_title_box,
)


def classify_table(table: dict[str, Any]) -> str:
    text = get_all_text(table)
    non_empty_cells = get_non_empty_cells(table)

    if _is_priority_title_box(table, text, non_empty_cells):
        return "title_box"

    if is_caption_or_note_table(table, text):
        return "caption_or_note_table"

    if is_title_box(table, text, non_empty_cells):
        return "title_box"

    if _is_title_box_condition1(table, text) or _is_title_box_condition2(table, text):
        return "title_box"

    if _is_data_table_priority(table):
        return "data_table"

    if is_key_value_table(table):
        return "key_value_table"

    return "data_table"
