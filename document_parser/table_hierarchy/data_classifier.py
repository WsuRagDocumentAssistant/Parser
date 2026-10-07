#================================================
# table_hierarchy/data_classifier.py
# data_table 판정
#================================================

from __future__ import annotations

from typing import Any

from .cell_utils import _cell_col, _cell_col_span, _cell_row_span, get_cell_text
from .table_utils import get_table_size, group_origin_cells_by_row


def _has_data_table_header_structure(table: dict[str, Any]) -> bool:
    """row0/row1이 multi-level 컬럼 헤더 구조(data_table)임을 감지한다.

    Condition A: row0에 cs>1 AND rs>1인 셀이 있으면 다차원 병합 컬럼 헤더.
    Condition B: row0에 col>0이고 cs>1인 셀이 있고,
                 row1이 그 column span 내에 ≥2개의 origin cell을 채우면 2단 컬럼 헤더.

    단, row0가 단일 전체폭 셀(제목 행)이면 제외.
    """
    _, col_count = get_table_size(table)
    row_cells = group_origin_cells_by_row(table)
    sorted_rows = sorted(row_cells.keys())
    if not sorted_rows:
        return False

    row0_cells = row_cells.get(sorted_rows[0], [])

    # 단일 전체폭 셀 → 제목 행이므로 컬럼 헤더 검사 제외
    if len(row0_cells) == 1:
        cell = row0_cells[0]
        if _cell_col(cell) == 0 and _cell_col_span(cell) >= col_count:
            return False

    # Condition A: cs>1 AND rs>1인 셀 → 행·열 모두 병합된 multi-dim 헤더
    for cell in row0_cells:
        if _cell_col_span(cell) > 1 and _cell_row_span(cell) > 1:
            return True

    # Condition B: col>0이고 cs>1인 셀의 span을 row1이 ≥2개 sub-cell로 채우면 2단 헤더
    if len(sorted_rows) < 2:
        return False
    row1_origin_cols = {
        _cell_col(c)
        for c in row_cells.get(sorted_rows[1], [])
        if _cell_col(c) is not None
    }
    for cell in row0_cells:
        col = _cell_col(cell)
        cs = _cell_col_span(cell)
        if col is None or col == 0 or cs <= 1:
            continue
        span_cols = set(range(col, col + cs))
        if len(span_cols & row1_origin_cols) >= 2:
            return True

    return False


def _is_section_group_label_cell(cell: dict[str, Any], row_count: int) -> bool:
    """col_addr=0의 row_span>1 셀이 표 내부 하위 섹션을 구분하는 그룹 라벨인지 확인한다.

    row_span이 표 전체 행 수와 같으면(테두리/프레임용 셀) 섹션 그룹 라벨로 보지 않는다.
    """
    if _cell_col(cell) != 0:
        return False
    row_span = _cell_row_span(cell)
    return row_span > 1 and row_span < row_count


def _label_spanned_kv_block_rows(table: dict[str, Any]) -> set[int]:
    """col_addr=0의 row_span>1 그룹 라벨이 덮는 행들이 서로 동일한 origin column
    구성을 공유하면(라벨 행 + 값 행이 짝을 이루는 구조), 그 행들의 집합을 반환한다.

    이런 행들은 "여러 독립 열 필드"가 아니라 라벨에 종속된 key/value 짝이므로
    _has_wide_data_row 판정에서 제외한다.
    """
    row_count, _ = get_table_size(table)
    row_cells = group_origin_cells_by_row(table)
    sorted_rows = sorted(row_cells.keys())
    skip_rows: set[int] = set()

    for r in sorted_rows:
        label_cell = next(
            (c for c in row_cells[r] if _is_section_group_label_cell(c, row_count)),
            None,
        )
        if label_cell is None:
            continue

        span_end = r + _cell_row_span(label_cell)
        covered_rows = [rr for rr in sorted_rows if r <= rr < span_end]
        if len(covered_rows) < 2:
            continue

        signatures = [
            frozenset(
                _cell_col(c)
                for c in row_cells.get(rr, [])
                if _cell_col(c) is not None and get_cell_text(c) and c is not label_cell
            )
            for rr in covered_rows
        ]
        non_empty_signatures = {sig for sig in signatures if sig}
        if len(non_empty_signatures) == 1:
            skip_rows.update(covered_rows)

    return skip_rows


def _has_wide_data_row(table: dict[str, Any]) -> bool:
    """col_count>=5에서 한 행에 origin cell(값 있는 독립 필드)이 3개 이상 있는지 확인한다.

    col_addr=0의 row_span>1 그룹 라벨 셀은 값 필드가 아니라 여러 행에 걸친
    섹션 표식이므로 독립 필드 수 계산에서 제외한다. 그 라벨이 덮는 행들이
    동일한 column 구성을 공유하는 라벨/값 짝 구조라면 해당 행들도 제외한다.
    """
    _, col_count = get_table_size(table)
    if col_count < 5:
        return False

    row_cells = group_origin_cells_by_row(table)
    skip_rows = _label_spanned_kv_block_rows(table)

    for row_index, cells in row_cells.items():
        if row_index in skip_rows:
            continue
        non_empty = [
            c for c in cells
            if get_cell_text(c) and not (_cell_col(c) == 0 and _cell_row_span(c) > 1)
        ]
        if len(non_empty) >= 3:
            return True
    return False


def _has_multi_level_header(table: dict[str, Any]) -> bool:
    """col_count>=5에서 상단 1~3행에 병합 셀로 구성된 다중 헤더 구조가 있는지 확인한다.

    첫 행이 full-width(제목 행)이면 그 아래 row1~row3를 헤더 후보로 본다.
    """
    _, col_count = get_table_size(table)
    if col_count < 5:
        return False

    row_cells = group_origin_cells_by_row(table)
    sorted_rows = sorted(row_cells.keys())
    if not sorted_rows:
        return False

    row0_cells = row_cells.get(sorted_rows[0], [])
    row0_is_full_width_title = (
        len(row0_cells) == 1
        and _cell_col(row0_cells[0]) == 0
        and _cell_col_span(row0_cells[0]) >= col_count
    )

    candidate_rows = sorted_rows[1:4] if row0_is_full_width_title else sorted_rows[0:3]

    for idx, r in enumerate(candidate_rows):
        cells = row_cells.get(r, [])

        for cell in cells:
            if _cell_col_span(cell) > 1 and _cell_row_span(cell) > 1:
                return True

        if idx + 1 < len(candidate_rows):
            next_cols = {
                _cell_col(c)
                for c in row_cells.get(candidate_rows[idx + 1], [])
                if _cell_col(c) is not None
            }
            for cell in cells:
                col = _cell_col(cell)
                cs = _cell_col_span(cell)
                if col is None or col == 0 or cs <= 1:
                    continue
                span_cols = set(range(col, col + cs))
                if len(span_cols & next_cols) >= 2:
                    return True

    return False


def _row_label_group_ids(table: dict[str, Any]) -> dict[int, int]:
    """각 행을 col_addr=0의 row_span>1 그룹 라벨이 덮는 구간별로 그룹핑한다.

    라벨이 없는 행은 자기 자신만의 단독 그룹으로 취급한다.
    """
    row_count, _ = get_table_size(table)
    row_cells = group_origin_cells_by_row(table)
    sorted_rows = sorted(row_cells.keys())
    group_id: dict[int, int] = {}

    for r in sorted_rows:
        label_cell = next(
            (c for c in row_cells[r] if _is_section_group_label_cell(c, row_count)),
            None,
        )
        if label_cell is None:
            continue
        span_end = r + _cell_row_span(label_cell)
        for rr in sorted_rows:
            if r <= rr < span_end:
                group_id[rr] = r

    for r in sorted_rows:
        group_id.setdefault(r, r)

    return group_id


def _has_repeated_wide_data_pattern(table: dict[str, Any]) -> bool:
    """body 영역에서 origin column 3개 이상인 다중 열 패턴이 반복되는지 확인한다.

    단순 key/value 2열 패턴(라벨+값)은 제외하고, 3개 이상 열을 채우는
    데이터 레코드형 패턴만 대상으로 한다. 동일한 col_addr=0 그룹 라벨이
    덮는 구간 안에서만 반복되는 패턴(폼 안의 한 섹션짜리 목록)은 제외하고,
    서로 다른 그룹(또는 라벨 없는 행)에 걸쳐 반복되는 패턴만 인정한다.
    """
    row_cells = group_origin_cells_by_row(table)
    group_id = _row_label_group_ids(table)

    pattern_groups: dict[tuple[int, ...], set[int]] = {}
    for row_index, cells in row_cells.items():
        pattern = tuple(sorted(
            col for c in cells
            if (col := _cell_col(c)) is not None and get_cell_text(c)
        ))
        if len(pattern) < 3:
            continue
        pattern_groups.setdefault(pattern, set()).add(group_id[row_index])

    return any(len(groups) >= 2 for groups in pattern_groups.values())


def _is_form_kv_exception(table: dict[str, Any]) -> bool:
    """col_count>=5 표에서 key_value_table(form_kv) 예외로 허용할 명확한 구조인지 확인한다.

    - col_addr=0에 row_span>1인 그룹 라벨 셀이 있고
    - 오른쪽 영역이 짧은 key + value 인접 쌍으로 배치되며
    - body 행들이 동일한 다중 열 데이터 패턴을 반복하지 않아야 한다.
    """
    from .key_value_classifier import _count_adjacent_kv_pairs

    row_count, _ = get_table_size(table)
    row_cells = group_origin_cells_by_row(table)

    has_group_label = any(
        _is_section_group_label_cell(cell, row_count)
        for cells in row_cells.values()
        for cell in cells
    )
    if not has_group_label:
        return False

    if _has_repeated_wide_data_pattern(table):
        return False

    return _count_adjacent_kv_pairs(table) >= 3


def _is_data_table_priority(table: dict[str, Any]) -> bool:
    """key_value_table 판정보다 우선하여 data_table로 분류할지 판단한다.

    col_count>=5인 표는 기본적으로 data_table 우선이며,
    명확한 form_kv 예외 구조(_is_form_kv_exception)가 있을 때만 제외한다.
    """
    _, col_count = get_table_size(table)
    if col_count < 5:
        return False

    if _has_wide_data_row(table):
        return True
    if _has_multi_level_header(table):
        return True
    if _has_repeated_wide_data_pattern(table):
        return True

    return not _is_form_kv_exception(table)
