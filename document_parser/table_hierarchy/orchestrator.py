#================================================
# table_hierarchy/orchestrator.py
# 표 hierarchy 생성 오케스트레이션
#================================================

from __future__ import annotations

from collections import Counter
from typing import Any

from .base import _base_hierarchy
from .cell_utils import _cell_ids, get_non_empty_cells
from .classify import classify_table
from .column_builder import build_columns
from .data_builder import build_data_table_hierarchy, build_raw_rows
from .form_kv_builder import _build_form_sections
from .header_col_detector import detect_header_cols
from .header_row_detector import append_warning, detect_header_rows
from .key_value_builder import build_key_value_records
from .record_stability_filter import apply_record_stability_filter
from .structured_record_builder import build_structured_records
from .warning_normalizer import normalize_hierarchy_warnings
from .nested import (
    add_nested_hierarchy_from_direct_cells,
    add_nested_hierarchy_from_rows,
    add_nested_hierarchy_from_summary_children,
)


def add_hierarchy_recursive(
    table: dict[str, Any],
    stats: Counter[str] | None = None,
    depth: int = 0,
) -> None:
    hierarchy = build_hierarchy(table)
    table["hierarchy"] = hierarchy

    if stats is not None:
        stats["total_tables"] += 1
        stats[f"type:{hierarchy['table_type']}"] += 1
        stats["warnings"] += len(hierarchy["quality"]["warnings"])
        if depth > 0:
            stats["nested_tables"] += 1

    recursed = add_nested_hierarchy_from_rows(table, hierarchy, stats, depth)
    if not recursed:
        recursed = add_nested_hierarchy_from_direct_cells(table, hierarchy, stats, depth)
    if not recursed:
        add_nested_hierarchy_from_summary_children(table, hierarchy, stats, depth)

    if hierarchy["table_type"] == "data_table":
        hierarchy["raw_rows"] = build_raw_rows(table, hierarchy["nested_table_refs"])

        detection = detect_header_rows(table, hierarchy["raw_rows"])
        hierarchy["header_rows"] = detection["header_rows"]
        hierarchy["header_row_candidates"] = detection["header_row_candidates"]
        for warning in detection["warnings"]:
            append_warning(hierarchy, warning["code"], warning["message"], stage="header_rows_detection")

        col_detection = detect_header_cols(table, hierarchy["raw_rows"], hierarchy["header_rows"])
        hierarchy["header_cols"] = col_detection["header_cols"]
        hierarchy["header_col_candidates"] = col_detection["header_col_candidates"]
        for warning in col_detection["warnings"]:
            append_warning(hierarchy, warning["code"], warning["message"], stage="header_cols_detection")

        if hierarchy["header_rows"]:
            column_result = build_columns(
                table,
                hierarchy["raw_rows"],
                hierarchy["header_rows"],
                hierarchy["header_cols"],
            )
            hierarchy["columns"] = column_result["columns"]
            for warning in column_result["warnings"]:
                append_warning(hierarchy, warning["code"], warning["message"], stage="columns_generation")
        else:
            hierarchy["columns"] = []

        record_result = build_structured_records(
            hierarchy["raw_rows"],
            hierarchy["header_rows"],
            hierarchy["columns"],
        )
        hierarchy["structured_records"] = record_result["structured_records"]
        for warning in record_result["warnings"]:
            append_warning(hierarchy, warning["code"], warning["message"], stage="structured_records_generation")

        stability_result = apply_record_stability_filter(
            table,
            hierarchy["header_rows"],
            hierarchy["header_row_candidates"],
            hierarchy["columns"],
            hierarchy["structured_records"],
            hierarchy["quality"]["warnings"],
        )
        hierarchy["record_status"] = stability_result["record_status"]
        hierarchy["record_warnings"] = stability_result["record_warnings"]
        hierarchy["structured_records"] = stability_result["structured_records"]

        normalize_hierarchy_warnings(hierarchy)


def build_hierarchy(table: dict[str, Any]) -> dict[str, Any]:
    hierarchy = _base_hierarchy()
    table_type = classify_table(table)
    hierarchy["table_type"] = table_type

    if table_type == "caption_or_note_table":
        hierarchy["caption_or_note_cells"] = _cell_ids(get_non_empty_cells(table))
        hierarchy["quality"]["warnings"].append("records_skipped_for_caption_or_note_table")
        return hierarchy

    if table_type == "title_box":
        hierarchy["title_cells"] = _cell_ids(get_non_empty_cells(table))
        hierarchy["quality"]["warnings"].append("records_skipped_for_title_box")
        return hierarchy

    if table_type == "key_value_table":
        records, header_rows, body_cells, orientation, header_content = build_key_value_records(table)
        hierarchy["key_value_records"] = records
        hierarchy["key_value_orientation"] = orientation
        hierarchy["header_rows"] = header_rows
        hierarchy["body_cells"] = body_cells
        if header_content is not None:
            hierarchy["key_value_header"] = header_content
        if orientation == "row_pairs":
            hierarchy["key_value_items"] = [{"key": r["key"], "value": r["value"]} for r in records]
        elif orientation == "form_kv":
            hierarchy["key_value_records"] = []
            form_sections, full_width_blocks, raw_blocks = _build_form_sections(table)
            if form_sections:
                hierarchy["form_sections"] = form_sections
            if full_width_blocks:
                hierarchy["full_width_blocks"] = full_width_blocks
            if raw_blocks:
                hierarchy["raw_blocks"] = raw_blocks
        if orientation != "form_kv" and not records:
            hierarchy["quality"]["warnings"].append("no_key_value_records_created")
        return hierarchy

    header_rows, body_cells, ambiguous = build_data_table_hierarchy(table)
    hierarchy["header_rows"] = header_rows
    hierarchy["body_cells"] = body_cells
    if ambiguous:
        hierarchy["quality"]["warnings"].append("ambiguous_header_rows")
    return hierarchy
