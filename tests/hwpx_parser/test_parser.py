import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

from hwpx_parser import HwpxParser, HwpxParseError, ParseLimits, TableBlock, TextBlock


HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
HS = "http://www.hancom.co.kr/hwpml/2011/section"
HC = "http://www.hancom.co.kr/hwpml/2011/core"
OPF = "http://www.idpf.org/2007/opf/"
NS = f'xmlns:hp="{HP}" xmlns:hh="{HH}" xmlns:hs="{HS}" xmlns:hc="{HC}"'
PNG = bytes.fromhex("89504e470d0a1a0a") + b"fixture-bytes-not-decoded"
SETUP = '<hp:secPr textDirection="HORIZONTAL"><hp:pagePr width="60000" height="84000" landscape="WIDELY"><hp:margin top="4000" bottom="4000" header="1000" footer="1000"/></hp:pagePr></hp:secPr><hp:ctrl><hp:colPr colCount="1"/></hp:ctrl>'


def line(y=0, offset=0):
    return f'<hp:lineseg textpos="{offset}" vertpos="{y}" vertsize="1000" horzpos="0" horzsize="40000" flags="393216"/>'


def para(text="", prop="0", style="0", y=0, extra="", layout=True, break_page=False):
    return (f'<hp:p id="duplicate" paraPrIDRef="{prop}" styleIDRef="{style}" pageBreak="{int(break_page)}">'
            f'<hp:run charPrIDRef="0"><hp:t>{text}</hp:t>{extra}</hp:run>'
            + (f'<hp:linesegarray>{line(y)}</hp:linesegarray>' if layout else "") + '</hp:p>')


def picture():
    return '<hp:pic id="picture"><hp:sz height="1000" heightRelTo="ABSOLUTE"/><hp:pos treatAsChar="1"/><hc:img binaryItemIDRef="image1"/></hp:pic>'


def table(text="셀", inner="", span=2):
    return ('<hp:tbl rowCnt="1" colCnt="2" pageBreak="NONE"><hp:sz height="2000"/>'
            '<hp:pos treatAsChar="1"/><hp:tr><hp:tc><hp:cellAddr rowAddr="0" colAddr="0"/>'
            f'<hp:cellSpan rowSpan="1" colSpan="{span}"/><hp:subList>'
            + para(text, extra=inner) + '</hp:subList></hp:tc></hp:tr></hp:tbl>')


def package(path, body=None, sections=None, replace=None, extra=None):
    sections = sections or [body if body is not None else para("본문", extra=SETUP)]
    properties = ''.join(f'<hh:paraPr id="{i}"><hh:heading type="{typ}" level="{level}" idRef="0"/></hh:paraPr>'
                         for i, typ, level in [(0, 'NONE', 0), (1, 'OUTLINE', 0), (2, 'OUTLINE', 2), (3, 'NUMBER', 0)])
    header = (f'<hh:head {NS} version="1.5"><hh:refList><hh:paraProperties>{properties}</hh:paraProperties>'
              '<hh:charProperties><hh:charPr id="0" height="1000"/></hh:charProperties>'
              '<hh:styles><hh:style id="0" name="바탕글"/><hh:style id="1" name="제목"/></hh:styles></hh:refList></hh:head>')
    items = ''.join(f'<opf:item id="s{i}" href="Contents/section{i}.xml" media-type="application/xml"/>' for i in range(len(sections)))
    spine = ''.join(f'<opf:itemref idref="s{i}"/>' for i in range(len(sections)))
    hpf = (f'<opf:package xmlns:opf="{OPF}"><opf:metadata><opf:title>테스트</opf:title></opf:metadata>'
           '<opf:manifest><opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
           '<opf:item id="image1" href="BinData/a.png" media-type="image/png"/>' + items
           + '</opf:manifest><opf:spine>' + spine + '</opf:spine></opf:package>')
    files = {"mimetype": b"application/hwp+zip", "Contents/content.hpf": hpf, "Contents/header.xml": header, "BinData/a.png": PNG}
    files.update({f"Contents/section{i}.xml": f'<hs:sec {NS}>{content}</hs:sec>' for i, content in enumerate(sections)})
    files.update(replace or {})
    files.update(extra or {})
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for name, content in files.items():
            if content is not None:
                archive.writestr(name, content.encode('utf-8') if isinstance(content, str) else content)
    return path


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def parse(self, body=None, **kwargs):
        return HwpxParser(page_mode='layout').parse(package(self.root / 'test.hwpx', body, **kwargs))

    def test_nested_content_assets_and_unicode_roundtrip(self):
        body = para('앞<hp:tab/>뒤😀<hp:lineBreak/>끝', extra=SETUP + table(inner=picture()) + picture())
        document = self.parse(body)
        texts = [b for b in document.blocks if isinstance(b, TextBlock)]
        self.assertEqual([b.text for b in texts], ['앞\t뒤😀\n끝', '셀'])
        self.assertEqual(len({b.id for b in document.blocks}), len(document.blocks))
        table_block = next(b for b in document.blocks if isinstance(b, TableBlock))
        self.assertEqual(table_block.cells[0].col_span, 2)
        self.assertEqual(table_block.to_grid({b.id: b.text for b in texts}), [['셀', None]])
        self.assertEqual(len(document.assets[0]['used_by']), 2)
        self.assertEqual([c['type'] for c in texts[0].content], ['text', 'object', 'object'])
        out = document.export(self.root / 'output')
        result = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(result, document.to_dict())
        self.assertEqual(result['blocks'][0]['text'], texts[0].text)
        self.assertEqual((out.parent / result['assets'][0]['output_path']).read_bytes(), PNG)
        self.assertEqual(HwpxParser().parse(self.root / 'test.hwpx').blocks[0].id, texts[0].id)
        exported_texts = json.loads((out.parent / 'texts.json').read_text(encoding='utf-8'))
        self.assertEqual([b['text'] for b in exported_texts], [b.text for b in texts])
        exported_tables = json.loads((out.parent / 'tables.json').read_text(encoding='utf-8'))
        self.assertEqual(exported_tables[0]['cells'][0]['text'], '셀')
        self.assertEqual(exported_tables[0]['cells'][0]['col_span'], 2)
        self.assertNotIn('text', next(b for b in result['blocks'] if b['kind'] == 'table')['cells'][0])
        exported_images = json.loads((out.parent / 'images.json').read_text(encoding='utf-8'))
        self.assertEqual(len(exported_images), 2)
        self.assertEqual(exported_images[0]['asset']['output_path'], document.assets[0]['output_path'])
        self.assertEqual(exported_images[0]['location']['status'], 'estimated')
        self.assertIn('앞\t뒤😀\n끝', (out.parent / 'texts.txt').read_text(encoding='utf-8'))
        self.assertIn('| 1 | 1 | 1 | 2 | 셀 |', (out.parent / 'tables.md').read_text(encoding='utf-8'))

    def test_heading_dfs_skips_levels_lists_and_nested_headings(self):
        body = para('서문', extra=SETUP) + para('대제목', prop='1', y=1000)
        body += para('소제목', prop='2', y=2000) + para('번호 목록', prop='3', y=3000)
        body += para('담는 문단', y=4000, extra=table(inner=para('셀 개요', prop='1')))
        body += para('다음 장', prop='1', y=5000)
        doc = self.parse(body)
        headings = list(doc.iter_headings())
        self.assertEqual([h.title for h in headings], ['대제목', '소제목', '다음 장'])
        self.assertEqual((headings[1].level, headings[1].depth), (3, 2))
        self.assertEqual(len(list(doc.iter_headings(start_heading_id=headings[0].id, max_depth=0))), 1)
        subtree = list(doc.iter_blocks(heading_id=headings[0].id))
        self.assertTrue(any(b.kind == 'table' for b in subtree))
        self.assertFalse(any(getattr(b, 'text', '') == '다음 장' for b in subtree))
        self.assertEqual(len(list(doc.iter_blocks(heading_id=headings[0].id, include_descendants=False))), 1)
        out = doc.export(self.root / 'headings')
        exported = json.loads((out.parent / 'headings.json').read_text(encoding='utf-8'))
        self.assertEqual([h['title'] for h in exported], ['대제목', '소제목', '다음 장'])
        self.assertEqual(exported[1]['parent_id'], exported[0]['id'])
        self.assertEqual(exported[0]['children'], [exported[1]['id']])
        self.assertEqual(exported[0]['block_ids'], [b.id for b in doc.iter_blocks(
            heading_id=headings[0].id, include_descendants=False)])
        self.assertEqual(exported[0]['locations'][0]['physical_pages'], [1])
        self.assertIn('  - 소제목', (out.parent / 'headings.md').read_text(encoding='utf-8'))

    def test_style_mapping_and_scopes(self):
        path = package(self.root / 'a.hwpx', para('제목', style='1', extra=SETUP) + para('보통', extra=table(text='셀')))
        doc = HwpxParser(style_levels={'제목': 2}).parse(path)
        self.assertEqual([h.level for h in doc.iter_headings()], [2])
        doc = HwpxParser(style_levels={'제목': 2}, style_scopes={'제목': ['cell']}).parse(path)
        self.assertEqual(len(doc.headings), 1)

    def test_automatic_headings_join_cells_and_skip_prose(self):
        styles = [('0', 'Normal'), ('1', 'I.'), ('2', '1-1.'), ('3', 'Outline 1'), ('4', 'Outline 4')]
        header = f'<hh:head {NS}><hh:styles>' + ''.join(
            f'<hh:style id="{ident}" name="{name}"/>' for ident, name in styles
        ) + '</hh:styles><hh:charPr id="0"/><hh:paraPr id="0"/></hh:head>'
        cells = ''.join(
            f'<hp:tc><hp:cellAddr rowAddr="0" colAddr="{index}"/><hp:cellSpan rowSpan="1" colSpan="1"/>'
            f'<hp:subList>{para(text, style=style)}</hp:subList></hp:tc>'
            for index, (text, style) in enumerate([('1-1', '2'), ('', '0'), ('분리된 제목', '0')])
        )
        body = para('Ⅰ. 큰 제목', style='1', extra=SETUP)
        body += para('', extra=f'<hp:tbl rowCnt="1" colCnt="3"><hp:tr>{cells}</hp:tr></hp:tbl>')
        body += para('□ 작은 제목', style='3') + para('- 설명 문장', style='4')
        path = package(self.root / 'automatic.hwpx', body, replace={'Contents/header.xml': header})
        doc = HwpxParser().parse(path)
        headings = list(doc.iter_headings())
        self.assertEqual([h.title for h in headings], ['Ⅰ. 큰 제목', '1-1 분리된 제목', '□ 작은 제목'])
        self.assertEqual([h.level for h in headings], [1, 3, 4])
        self.assertTrue(all(h.evidence['classification'] == 'heuristic' for h in headings))
        self.assertEqual(len(headings[1].evidence['source_block_ids']), 2)
        self.assertEqual(len(HwpxParser(auto_headings=False).parse(path).headings), 1)

    def test_auto_page_offsets_and_explicit_break(self):
        path = package(self.root / 'pages.hwpx', sections=[
            para('앞', extra=SETUP, y=1000) + para('뒤', break_page=True, y=0),
            para('다음 구역', extra=SETUP),
        ])
        doc = HwpxParser().parse(path)
        self.assertEqual([b.location.physical_pages for b in doc.blocks], [[1], [2], [3]])
        self.assertEqual(doc.metadata['estimated_page_count'], 3)
        self.assertEqual(doc.page_status, 'estimated')
        self.assertEqual(doc.status, 'partial')
        self.assertTrue(all(b.location.evidence['confidence'] == 'low' for b in doc.blocks))

    def test_auto_table_rows_and_split_cell_candidates(self):
        rows = ''.join(
            f'<hp:tr><hp:tc><hp:cellAddr rowAddr="{row}" colAddr="0"/>'
            f'<hp:cellSpan rowSpan="1" colSpan="1"/><hp:cellSz height="50000"/>'
            f'<hp:subList>{para(str(row))}</hp:subList></hp:tc></hp:tr>' for row in range(2)
        )
        body = para('', extra=SETUP + '<hp:tbl rowCnt="2" colCnt="1" pageBreak="CELL">'
                    '<hp:pos treatAsChar="1" vertOffset="4294920257"/>' + rows + '</hp:tbl>')
        path = package(self.root / 'row-pages.hwpx', body)
        doc = HwpxParser().parse(path)
        tbl = next(doc.iter_blocks(kind='table'))
        self.assertEqual(tbl.location.physical_pages, [1, 2])
        texts = [b for b in doc.iter_blocks(kind='text') if b.scope == 'cell']
        self.assertEqual([b.location.physical_pages for b in texts], [[1], [2]])
        self.assertTrue(all(b.location.evidence['coverage'] == 'candidate' for b in texts))
        # One tall cell spans three pages; its text gets candidates, not verified placement.
        tall = body.replace('height="50000"', 'height="150000"', 1)
        tall_doc = HwpxParser().parse(package(self.root / 'tall.hwpx', tall))
        first_cell_text = next(b for b in tall_doc.iter_blocks(kind='text') if b.scope == 'cell')
        self.assertEqual(first_cell_text.location.physical_pages, [1, 2, 3])

        repeated_rows = ''.join(
            f'<hp:tr><hp:tc header="{int(row == 0)}"><hp:cellAddr rowAddr="{row}" colAddr="0"/>'
            f'<hp:cellSz height="{height}"/><hp:subList>{para(str(row))}</hp:subList></hp:tc></hp:tr>'
            for row, height in enumerate([10000, 60000, 60000])
        )
        repeated = para('', extra=SETUP + '<hp:tbl rowCnt="3" colCnt="1" pageBreak="CELL" repeatHeader="1">'
                        '<hp:pos treatAsChar="1"/>' + repeated_rows + '</hp:tbl>')
        repeated_doc = HwpxParser().parse(package(self.root / 'repeated.hwpx', repeated))
        repeated_table = next(repeated_doc.iter_blocks(kind='table'))
        self.assertEqual(repeated_table.location.physical_pages, [1, 2])
        self.assertEqual(repeated_table.location.evidence['repeat_header_height'], 10000)
        self.assertEqual(repeated_table.location.evidence['end_y'], 70000)
        header_text = next(b for b in repeated_doc.iter_blocks(kind='text') if b.scope == 'cell')
        self.assertEqual(header_text.location.physical_pages, [1, 2])

    def test_auto_huge_geometry_is_bounded_and_columns_remain_unknown(self):
        body = para('', extra=SETUP + '<hp:tbl rowCnt="1" colCnt="1" pageBreak="CELL">'
                    '<hp:pos treatAsChar="1"/><hp:tr><hp:tc><hp:cellAddr rowAddr="0" colAddr="0"/>'
                    '<hp:cellSz height="281474976710656"/><hp:subList>' + para('내용')
                    + '</hp:subList></hp:tc></hp:tr></hp:tbl>')
        doc = HwpxParser().parse(package(self.root / 'huge.hwpx', body))
        self.assertIsNone(next(doc.iter_blocks(kind='table')).location.physical_pages)
        self.assertIsNone(doc.metadata['estimated_page_count'])
        path = package(self.root / 'columns.hwpx', para('다단', extra=SETUP.replace('colCount="1"', 'colCount="2"')))
        self.assertIsNone(HwpxParser().parse(path).blocks[0].location.physical_pages)

    def test_number_and_title_cells_join_only_in_same_row(self):
        cells = ''.join('<hp:tc><hp:cellAddr rowAddr="0" colAddr="' + str(i) + '"/><hp:cellSpan rowSpan="1" colSpan="1"/><hp:subList>'
                        + para(text, style='1') + '</hp:subList></hp:tc>' for i, text in enumerate(['1-1', '검증 제목']))
        body = para('', extra=SETUP + '<hp:tbl rowCnt="1" colCnt="2"><hp:tr>' + cells + '</hp:tr></hp:tbl>')
        path = package(self.root / 'heading-cells.hwpx', body)
        doc = HwpxParser(style_levels={'제목': 2}, heading_scopes=('body', 'cell'), join_numbered_heading_cells=True).parse(path)
        self.assertEqual([h.title for h in doc.iter_headings()], ['1-1 검증 제목'])
        self.assertEqual(len(doc.headings[1].evidence['source_block_ids']), 2)

    def test_cli_rejects_verified_pages_but_writes_usable_output(self):
        import subprocess
        import sys
        path = package(self.root / 'cli.hwpx')
        result = subprocess.run([sys.executable, '-m', 'hwpx_parser', str(path), '--output', str(self.root/'out'), '--require-verified-pages'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 3)
        doc = json.loads((self.root/'out'/'document.json').read_text(encoding='utf-8'))
        self.assertEqual(doc['status'], 'partial')
        self.assertEqual(doc['structure_status'], 'complete')

    def test_parser_configuration_is_reusable(self):
        parser = HwpxParser()
        a = package(self.root/'a.hwpx', para('첫째', prop='1'))
        b = package(self.root/'b.hwpx', para('둘째'))
        self.assertEqual(len(parser.parse(a).headings), 2)
        self.assertEqual(len(parser.parse(b).headings), 1)

    def test_single_column_page_estimates_and_explicit_break_not_doubled(self):
        body = para('1쪽', extra=SETUP, y=1000) + para('2쪽', y=0, break_page=True) + para('3쪽', y=2000)
        body += para('4문단', y=0)
        doc = self.parse(body)
        self.assertEqual([b.location.physical_pages for b in doc.blocks], [[1], [2], [2], [3]])
        self.assertTrue(all(b.location.status == 'estimated' for b in doc.blocks))
        self.assertEqual(len(list(doc.iter_blocks(physical_page=2))), 2)

    def test_multiline_paragraph_spans_pages(self):
        body = para('문단', extra=SETUP).replace(line(), line(2000) + line(0, 1))
        doc = self.parse(body)
        self.assertEqual(doc.blocks[0].location.physical_pages, [1, 2])
        self.assertEqual(doc.blocks[0].location.fragments[1]['source_textpos'], 1)

    def test_unknown_layout_breaks_continuity(self):
        doc = self.parse(para('앞', extra=SETUP) + para('누락', layout=False) + para('뒤', y=0))
        self.assertEqual([b.location.status for b in doc.blocks], ['estimated', 'unknown', 'unknown'])
        doc = self.parse(para('다단', extra=SETUP.replace('colCount="1"', 'colCount="2"')))
        self.assertIsNone(doc.blocks[0].location.physical_pages)

    def test_section_does_not_equal_page_and_spine_order(self):
        path = package(self.root / 'sections.hwpx', sections=[para('첫 구역', extra=SETUP), para('둘째 구역', extra=SETUP)])
        doc = HwpxParser(page_mode='layout').parse(path)
        self.assertIsNone(doc.blocks[1].location.physical_pages)
        with ZipFile(path) as z:
            content = z.read('Contents/content.hpf').decode().replace('<opf:itemref idref="s0"/><opf:itemref idref="s1"/>', '<opf:itemref idref="s1"/><opf:itemref idref="s0"/>')
        package(path, sections=[para('첫 구역'), para('둘째 구역')], replace={'Contents/content.hpf': content})
        self.assertEqual([b.text for b in HwpxParser().parse(path).blocks], ['둘째 구역', '첫 구역'])

    def test_large_split_table_unknown_not_anchor_page(self):
        body = para('', extra=SETUP + table().replace('height="2000"', 'height="200000"')) + para('다음')
        doc = self.parse(body)
        self.assertTrue(all(b.location.physical_pages is None for b in doc.blocks))
        self.assertEqual(doc.blocks[1].location.anchor_page, 1)

    def test_namespace_prefix_is_irrelevant_unknown_namespace_not_accepted(self):
        body = para('문자', extra=SETUP).replace('hp:', 'x:')
        doc = self.parse(replace={'Contents/section0.xml': f'<hs:sec {NS} xmlns:x="{HP}">{body}</hs:sec>'})
        self.assertEqual(doc.blocks[0].text, '문자')
        with self.assertRaises(HwpxParseError):
            self.parse(replace={'Contents/section0.xml': '<sec xmlns="https://evil.invalid"/>'})
        with self.assertRaises(HwpxParseError):
            self.parse(replace={'Contents/section0.xml': '<sec/>'})

    def test_untrusted_zip_and_xml_rejected(self):
        for replacements, extras in [
            ({'mimetype': b'bad'}, {}),
            ({'Contents/header.xml': '<!DOCTYPE a [<!ENTITY e "bomb">]><a>&e;</a>'}, {}),
            ({}, {'../outside': b'bad'}),
            ({'Contents/section0.xml': '<bad'}, {}),
            ({}, {'META-INF/manifest.xml': '<root><EncryptedData/></root>'}),
        ]:
            with self.subTest(replacements=replacements, extras=extras), self.assertRaises(HwpxParseError):
                self.parse(replace=replacements, extra=extras)
        path = package(self.root / 'duplicate.hwpx')
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with ZipFile(path, 'a') as z:
                z.writestr('mimetype', 'application/hwp+zip')
        with self.assertRaises(HwpxParseError):
            HwpxParser().parse(path)

    def test_limits_and_malformed_table(self):
        path = package(self.root / 'limits.hwpx')
        for limits in (ParseLimits(max_file_bytes=10), ParseLimits(max_xml_bytes=10), ParseLimits(max_nodes=3)):
            with self.subTest(limits=limits), self.assertRaises(HwpxParseError):
                HwpxParser(limits=limits).parse(path)
        with self.assertRaises(HwpxParseError):
            self.parse(para(extra=table(span=3)))

    def test_missing_asset_is_partial_and_structural_mode_unknown(self):
        path = package(self.root / 'missing.hwpx', para(extra=picture()), replace={'BinData/a.png': None})
        doc = HwpxParser(page_mode='structural').parse(path)
        self.assertEqual(doc.status, 'partial')
        self.assertEqual(doc.page_status, 'incomplete')
        self.assertTrue(any(d['code'] == 'missing_asset' for d in doc.diagnostics))


if __name__ == '__main__':
    unittest.main()
