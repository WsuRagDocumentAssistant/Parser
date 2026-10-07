#================================================
# table_hierarchy/title_classifier.py
# 제목 상자(title_box) 판정
#================================================

from __future__ import annotations

import re
from typing import Any

from .caption_classifier import is_caption_or_note_table
from .cell_utils import _cell_col, get_cell_text, get_direct_cells
from .table_utils import _is_nested_table, get_table_size, has_repetitive_data_structure

TITLE_PATTERNS = (
    re.compile(r"^\s*\d+[\.\)]\s*.+"),
    re.compile(r"^\s*[가-힣][\.\)]\s*.+"),
    re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[\.\)]\s*.+"),
    re.compile(r"^\s*[□■○●\-]\s*.+"),
)

# caption 키워드보다 우선 적용되는 번호형 제목 패턴
# "1 제목", "1-1 제목", "Ⅱ-2 제목" 등 기존 TITLE_PATTERNS이 잡지 못하는 형식 포함
_PRIORITY_TITLE_PATTERNS = (
    re.compile(r"^\s*\d+[\.\)]\s*.+"),                      # 1. / 1)
    re.compile(r"^\s*\d+\s+\S"),                             # 1 제목
    re.compile(r"^\s*\d+[-]\d+"),                            # 1-1 / 2-5
    re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[-]\d+"),           # Ⅱ-2
    re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[\.\)]\s*.+"),     # Ⅱ.
    re.compile(r"^\s*[가-힣][\.\)]\s*.+"),                  # 가.
)


def _common_additional_title_preconditions(table: dict[str, Any], text: str) -> bool:
    if _is_nested_table(table):
        return False
    existing_hierarchy = table.get("hierarchy")
    if isinstance(existing_hierarchy, dict):
        if existing_hierarchy.get("header_rows"):
            return False
        if existing_hierarchy.get("header_cols"):
            return False
    if is_caption_or_note_table(table, text):
        return False
    return True


def _is_title_box_condition1(table: dict[str, Any], text: str) -> bool:
    if not _common_additional_title_preconditions(table, text):
        return False
    direct_cells = get_direct_cells(table)
    cell_count = len(direct_cells)
    non_empty_count = sum(1 for c in direct_cells if get_cell_text(c))
    empty_count = cell_count - non_empty_count
    if not (cell_count <= 4 and non_empty_count <= 2 and empty_count >= 1):
        return False
    direct_text = " ".join(get_cell_text(c) for c in direct_cells if get_cell_text(c))
    return 8 <= len(direct_text.strip()) <= 80


def _is_title_box_condition2(table: dict[str, Any], text: str) -> bool:
    if not _common_additional_title_preconditions(table, text):
        return False
    row_count, col_count = get_table_size(table)
    if row_count != 1 or col_count != 2:
        return False
    direct_cells = get_direct_cells(table)
    non_empty = [c for c in direct_cells if get_cell_text(c)]
    if len(non_empty) != 2:
        return False
    cells_by_col = sorted(non_empty, key=lambda c: _cell_col(c) or 0)
    first_text = get_cell_text(cells_by_col[0]).strip()
    second_text = get_cell_text(cells_by_col[1]).strip()
    first_len = len(first_text)
    if first_len == 0:
        return False
    return first_len <= 3 and len(second_text) >= first_len * 3 and len(second_text) <= 120


def _is_priority_title_box(
    table: dict[str, Any],
    text: str,
    non_empty_cells: list[dict[str, Any]],
) -> bool:
    """번호형 제목 패턴이면 caption 키워드 검사보다 우선하여 title_box로 판정한다."""
    if not text:
        return False
    if _is_nested_table(table):
        return False
    if not any(p.match(text) for p in _PRIORITY_TITLE_PATTERNS):
        return False
    row_count, col_count = get_table_size(table)
    if col_count >= 3 and row_count >= 3:
        return False
    if len(non_empty_cells) > 3:
        return False
    if not (row_count <= 2 or col_count <= 2):
        return False
    return not has_repetitive_data_structure(table)


def is_title_box(
    table: dict[str, Any],
    text: str,
    non_empty_cells: list[dict[str, Any]],
) -> bool:
    if not text or is_caption_or_note_table(table, text):
        return False

    row_count, col_count = get_table_size(table)
    if col_count >= 3 and row_count >= 3:
        return False

    if len(non_empty_cells) > 3:
        return False

    if not (row_count <= 2 or col_count <= 2):
        return False

    if not any(pattern.match(text) for pattern in TITLE_PATTERNS):
        return False

    return not has_repetitive_data_structure(table)
