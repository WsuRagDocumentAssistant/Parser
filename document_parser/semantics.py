"""Canonical table and heading semantics shared by all four formats."""
import re
from collections import defaultdict

from .models import TocEntry, TocRef
from hwpx_parser.headings import compact_title, is_title_text


ROMAN = re.compile(r'^(?:[Ⅰ-Ⅻ]+|[IVXLCDM]+)(?:\s*[-–]\s*\d+)*[.)]?\s+\S')
NUMBERED = re.compile(r'^(\d+(?:[.-]\d+)*)(?:[.)])?\s+\S')
HANGUL = re.compile(r'^[가나다라마바사아자차카타파하][.)]\s+\S')
_LEADERS = '.·…'
TOC_MAX_CHARS = 500


def toc_entry(text):
    r"""목차 줄("제목 ...... 12")이면 제목, 아니면 None. 정규식 대신 끝에서부터 한 번 훑는다.

    예전 정규식 ^(.+?)\s*[.·…]{2,}\s*(\d+)\s*$ 는 제목 끝 자리를 정할 때마다 점선 부분을 다시
    훑어, 쪽번호가 없는 긴 "A......X" 문단에서 제곱 시간이 걸렸다(ReDoS). 목차 줄은 길지 않으므로
    TOC_MAX_CHARS 를 넘으면 보지 않는다.
    """
    text = text.rstrip()
    if len(text) > TOC_MAX_CHARS:
        return None
    end = len(text)
    while end and text[end - 1].isdecimal():          # 정규식 \d 와 같은 범위
        end -= 1
    if end == len(text):
        return None                                    # 쪽번호 없음
    end = len(text[:end].rstrip())
    leaders = end
    while leaders and text[leaders - 1] in _LEADERS:
        leaders -= 1
    if end - leaders < 2:
        return None                                    # 점선 2개 이상
    title = text[:leaders].strip()
    return title or None
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


def restore_heading_paths(model, *, inferred_ids=()):
    """Title boxes are actual chunk boundaries; keep explicit format headings."""
    for table in model.tables():
        table.title = [compact_title(text) for text in table.title if compact_title(text)]
    for block in model.blocks:
        if block.role == '제목' and block.text:
            block.text = compact_title(block.text)
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
                if text := toc_entry(paragraph.strip()):
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
    last_box_depth = None
    for block in model.blocks:
        if block.area != '본문':
            continue
        text = title(block).strip()
        box = block.table is not None and block.table.kind == '제목상자' and bool(block.table.title)
        toc_depth = toc_titles.get(re.sub(r'\s+', ' ', text))
        # Unstyled prose/list items are not promoted from numbering alone.
        roman_paragraph = block.kind == '문단' and is_title_text(text) and ROMAN.match(text)
        heading = block.role == '제목' or box or toc_depth is not None or roman_paragraph
        if heading and block.role != '목차':
            depth = outline_depth(text)
            if box and depth is not None or block.role != '제목':
                block.depth = toc_depth if toc_depth is not None else depth if depth is not None else last_title_depth
            if block.id in inferred_ids and last_box_depth is not None and not ROMAN.match(text):
                # Existing HWPX correction anchors paragraph headings below the title box.
                relative = 1
                if text.startswith(('○', '◦', '●')):
                    relative = 2
                elif text.startswith(('-', '·', 'ㆍ')):
                    relative = 3
                block.depth = last_box_depth + relative
                if match := NUMBERED.match(text):
                    if len(re.split(r'[.-]', match[1])) > 1:
                        block.depth = max(last_box_depth + 1, outline_depth(text))
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
            if box:
                last_box_depth = block.depth
            elif ROMAN.match(text):
                last_box_depth = None
        elif stack:
            block.depth = stack[-1].depth + 1
        block.heading_path = [b.id for b in stack]
        block.heading_path_text = [title(b).strip() for b in stack]
