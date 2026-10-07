"""Canonical table and heading semantics shared by all four formats."""
import re
from collections import defaultdict

from .models import TocEntry, TocRef


ROMAN = re.compile(r'^(?:[Ⅰ-Ⅻ]+|[IVXLCDM]+)(?:\s*[-–]\s*\d+)*[.)]?\s+\S')
NUMBERED = re.compile(r'^(\d+(?:[.-]\d+)*)(?:[.)])?\s+\S')
HANGUL = re.compile(r'^[가나다라마바사아자차카타파하][.)]\s+\S')
TOC = re.compile(r'^(.+?)\s*[.·…]{2,}\s*(\d+)\s*$')
NUMBER = re.compile(r'^\(?\d[\d.,]*\s*(?:명|건|개|원|점|시간|회|위|억원|백만원|천원|%|％|배|일|년|월|주|시|분|초|㎡|km|kg|톤|권|매|팀|과목|학점|주차)?\)?$')
ITEM_NUMBER = re.compile(r'^[\dⅠ-Ⅻ]+\s*[-·.]\s*\d+(?:[-·.]\d+)*$')


def numeric_verdict(cells, table_type, header):
    """Same column-based verdict as HwpxParser_sub analysis/table_filter.py."""
    if table_type not in {'data_table', 'key_value_table'}:
        return '대상아님'
    if header is None or not (header.header_rows or header.header_cols):
        return '판정불가'
    columns = defaultdict(list)
    for cell in cells:
        if cell.row in header.header_rows or cell.col in header.header_cols:
            continue
        value = cell.text.strip()
        if value and not re.fullmatch(r'[-–—.\s]*', value):
            columns[cell.col].append(value)
    if not columns:
        return '판정불가'
    for values in columns.values():
        if not all(NUMBER.fullmatch(v) and not ITEM_NUMBER.fullmatch(v) for v in values):
            continue
        try:
            numbers = [int(v.replace(',', '')) for v in values]
            serial = len(numbers) >= 2 and numbers[0] in (0, 1) and numbers == list(range(numbers[0], numbers[0] + len(numbers)))
        except ValueError:
            serial = False
        if not serial:
            return '수치표'
    return '아님'


def restore_heading_paths(model):
    """Title boxes are actual chunk boundaries; keep explicit format headings."""
    def title(block):
        return ' '.join(block.table.title) if block.table and block.table.kind == '제목상자' else block.text or ''

    has_roman = any(ROMAN.match(title(b).strip()) for b in model.blocks if b.area == '본문')

    def outline_depth(text):
        if ROMAN.match(text):
            return 0
        if match := NUMBERED.match(text):
            return len(re.split(r'[.-]', match[1])) - 1 + int(has_roman)
        if HANGUL.match(text):
            return 2 if has_roman else 1
        return None

    toc_titles = {}
    for block in model.blocks:
        if not block.table:
            continue
        entries = []
        for cell in block.table.cells:
            for paragraph in cell.paragraphs:
                if match := TOC.fullmatch(paragraph.strip()):
                    text = match[1].strip()
                    entries.append(TocEntry(text, outline_depth(text) or 0))
        if len(entries) >= 2:
            block.role = '목차'
            block.toc_entries = entries
            block.table.title = []
            if block.table.kind == '제목상자':
                block.table.kind = '데이터표'
                block.table.kept_as = '산문'
            toc_titles.update({re.sub(r'\s+', ' ', e.text): e.depth for e in entries})

    stack = []
    last_title_depth = 0
    for block in model.blocks:
        if block.area != '본문':
            continue
        text = title(block).strip()
        box = block.table is not None and block.table.kind == '제목상자' and bool(block.table.title)
        toc_depth = toc_titles.get(re.sub(r'\s+', ' ', text))
        # Unstyled prose/list items are not promoted from numbering alone.
        roman_paragraph = block.kind == '문단' and 0 < len(text) <= 120 and ROMAN.match(text)
        heading = block.role == '제목' or box or toc_depth is not None or roman_paragraph
        if heading and block.role != '목차':
            depth = outline_depth(text)
            if box and depth is not None or block.role != '제목':
                block.depth = toc_depth if toc_depth is not None else depth if depth is not None else last_title_depth
            block.role = '제목'
            block.searchable = True
            if toc_depth is not None:
                block.toc = TocRef(text)
            while stack and stack[-1].depth >= block.depth:
                stack.pop()
            if stack:
                stack[-1].child_headings.append(block.id)
            block.child_headings = []
            stack.append(block)
            last_title_depth = block.depth
        elif stack:
            block.depth = stack[-1].depth + 1
        block.heading_path = [b.id for b in stack]
        block.heading_path_text = [title(b).strip() for b in stack]
