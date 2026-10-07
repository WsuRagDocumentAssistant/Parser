#================================================
# table_hierarchy/base.py
# 기본 hierarchy 스켈레톤
#================================================

from __future__ import annotations

from typing import Any


def _base_hierarchy() -> dict[str, Any]:
    return {
        "table_type": "data_table",
        "title_cells": [],
        "caption_or_note_cells": [],
        "key_value_records": [],
        "header_rows": [],
        "header_row_candidates": [],
        "header_cols": [],
        "header_col_candidates": [],
        "body_cells": [],
        "raw_rows": [],
        "nested_table_refs": [],
        "quality": {
            "warnings": [],
        },
    }
