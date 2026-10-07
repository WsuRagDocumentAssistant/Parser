from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

from document_parser import parse
from document_parser.adapter import ModelAdapter
from document_parser.models import Cell
from hwpx_parser import HwpxParser, HwpxParseError
from hwpx_parser.models import Document, TableBlock, TextBlock, Cell as SourceCell
from tests.hwpx_parser.test_parser import package, para, table, SETUP, NS


class SemanticsTests(unittest.TestCase):
    def test_paragraph_style_titles_markers_whitespace_and_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            header = (f'<hh:head {NS}><hh:refList><hh:paraProperties>'
                      '<hh:paraPr id="0"><hh:heading type="NONE" level="0"/></hh:paraPr>'
                      '</hh:paraProperties><hh:charProperties><hh:charPr id="0" height="1000"/>'
                      '<hh:charPr id="1" height="1400"><hh:bold/></hh:charPr></hh:charProperties>'
                      '<hh:styles><hh:style id="0" name="바탕글"/></hh:styles></hh:refList></hh:head>')
            titles = ['□ 사업단 구성 및 운영계획', '○ 내부환경분석', '1) 학사구조 및 교육과정',
                      '② 세부내용5: 스마트교육혁신플랫폼 구축', '2.2.2. 1차년도 사업비 비목별 구성',
                      '【대학혁신지원사업 개요】', '세부과제명: 교수법 혁신 및 역량강화']
            prose = '3. 적정규모화 계획 - 발전계획 및 최근 대학 미충원 현황 분석/진단에 따른 ' + '본문 설명을 보존합니다. ' * 12
            body = para('', extra=SETUP + table('1 사업\n\n    추진'))
            for title in titles:
                body += para(title).replace('charPrIDRef="0"', 'charPrIDRef="1"')
                body += para('일반 설명 문장을 전달합니다. ' * 8) * 3
            body += para(prose).replace('charPrIDRef="0"', 'charPrIDRef="1"')
            body += para('1) 일반 목록입니다.') + para('', extra=table(prose))
            path = package(Path(directory) / 'styles.hwpx', body, replace={'Contents/header.xml': header})
            model = parse(path)
            headings = {b.text: b for b in model.blocks if b.role == '제목' and b.text}
            self.assertTrue(set(titles) <= headings.keys())
            self.assertNotIn(prose, headings)
            self.assertNotIn('1) 일반 목록입니다.', headings)
            self.assertEqual(headings[titles[1]].depth, headings[titles[0]].depth + 1)
            self.assertTrue(any(b.text == prose and b.role == '본문' for b in model.blocks))
            boxes = [b.table for b in model.blocks if b.table]
            self.assertEqual(boxes[0].title, ['1 사업 추진'])
            self.assertEqual(boxes[-1].kind, '데이터표')
            self.assertIn(prose, boxes[-1].cells[0].text)
            self.assertTrue(all('\n' not in text and '  ' not in text
                                for b in model.blocks for text in b.heading_path_text))
            raw = HwpxParser(auto_headings=False).parse(path)
            self.assertEqual(len(raw.headings), 1)

    def test_scripts_are_skipped_but_invalid_section_xml_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = package(Path(directory) / 'script.hwpx', para('본문', extra=SETUP))
            with ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            hpf = files['Contents/content.hpf'].decode('utf-8')
            hpf = hpf.replace('</opf:manifest>', '<opf:item id="script" href="../Scripts/headerScripts.js" media-type="application/javascript"/></opf:manifest>')
            hpf = hpf.replace('</opf:spine>', '<opf:itemref idref="script"/></opf:spine>')
            files['Contents/content.hpf'] = hpf.encode('utf-8')
            files['Scripts/headerScripts.js'] = b'throw new Error("must never execute");'
            with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
                for name, data in files.items(): archive.writestr(name, data)
            document = HwpxParser().parse(path)
            self.assertTrue(any(d['code'] == 'unsupported_spine_item' for d in document.diagnostics))
            files['Contents/section0.xml'] = b'<!DOCTYPE sec [<!ENTITY x SYSTEM "file:///secret">]><sec>&x;</sec>'
            with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
                for name, data in files.items(): archive.writestr(name, data)
            with self.assertRaises(HwpxParseError): HwpxParser().parse(path)

    def test_title_box_is_a_boundary_and_legacy_keywords_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = package(Path(directory) / 'title.hwpx',
                           para('앞', extra=SETUP + table('Ⅰ. 사업 개요')) + para('설명') +
                           para('', extra=table('1 사업 추진')) + para('상세 설명'))
            model = parse(path, unpack_dir=Path(directory) / 'unused', cleanup=True, recover_excluded=True)
            headings = [b for b in model.blocks if b.role == '제목']
            self.assertEqual([b.table.kind for b in headings], ['제목상자', '제목상자'])
            self.assertEqual([b.depth for b in headings], [0, 1])
            body = next(b for b in model.blocks if b.text == '상세 설명')
            self.assertEqual(body.heading_path, [b.id for b in headings])
            self.assertEqual(headings[0].child_headings, [headings[1].id])
            self.assertFalse((Path(directory) / 'unused').exists())

    def test_grid_markdown_merged_values_and_sparse_ceiling(self):
        cells = [Cell(0, 0, 2, 1, '항목'), Cell(0, 1, text='목표|값'), Cell(1, 1, text='10\n20')]
        markdown = ModelAdapter.table_markdown(cells, 2, 2)
        self.assertEqual(markdown, '| 항목 | 목표\\|값 |\n| --- | --- |\n| 항목 | 10<br>20 |')
        self.assertIn('| 1 | 1 |', ModelAdapter.table_markdown(cells, 1_048_576, 16_384, sparse=True))

    def test_all_formats_use_the_same_table_semantics(self):
        for format in ('hwpx', 'docx', 'xlsx', 'pdf'):
            with self.subTest(format=format):
                doc = Document({'filename': 'table.' + format, 'format': format})
                source = TableBlock(id='table', kind='table', order=0, scope='body', parent_id=None,
                                    source_ref={}, rows=4, columns=3)
                doc.blocks.append(source)
                values = [('구분', '목표', '실적'), ('과제 A', '10', '8'), ('과제 B', '20', '18'), ('합계', '30', '26')]
                for row, texts in enumerate(values):
                    for col, text in enumerate(texts):
                        ident = f'c{row}-{col}'
                        source.cells.append(SourceCell(ident, row, col, block_ids=[ident + '-text']))
                        doc.blocks.append(TextBlock(id=ident + '-text', kind='text', order=len(doc.blocks),
                            scope='cell', parent_id=ident, source_ref={}, text=text))
                result = ModelAdapter(doc).build().blocks[0].table
                self.assertEqual(result.kind, '데이터표')
                self.assertEqual(result.header.header_rows, [0])
                self.assertTrue(result.numeric)
                self.assertTrue(result.records)
                self.assertIn('| 과제 B | 20 | 18 |', result.markdown)


if __name__ == '__main__':
    unittest.main()
