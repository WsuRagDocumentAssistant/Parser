import base64
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from xlsx_parser import XlsxParser, XlsxParseError, ParseLimits
from xlsx_parser.__main__ import main as cli
from xlsx_parser.parser import coordinates

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def relationships(items):
    return f'<Relationships xmlns="{P}">' + ''.join(
        f'<Relationship Id="{ident}" Type="{R}/{kind}" Target="{target}" {mode}/>'
        for ident, kind, target, mode in items) + '</Relationships>'


def package(path, changes=None, strict=False):
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>',
        "_rels/.rels": relationships([('book', 'officeDocument', 'xl/workbook.xml', '')]),
        "xl/workbook.xml": f'<workbook xmlns="{S}" xmlns:r="{R}"><workbookPr date1904="1"/>'
        '<sheets><sheet name="매출 😀" sheetId="1" r:id="s1"/><sheet name="숨김" sheetId="2" state="hidden" r:id="s2"/></sheets>'
        '<definedNames><definedName name="합계">\'매출 😀\'!$C$3</definedName></definedNames><calcPr fullCalcOnLoad="1"/></workbook>',
        "xl/_rels/workbook.xml.rels": relationships([
            ('s1', 'worksheet', 'worksheets/sheet1.xml', ''), ('s2', 'worksheet', 'worksheets/sheet2.xml', ''),
            ('strings', 'sharedStrings', 'sharedStrings.xml', ''), ('styles', 'styles', 'styles.xml', '')]),
        "xl/sharedStrings.xml": f'<sst xmlns="{S}"><si><r><rPr><b/></rPr><t>한글</t></r><r><t> 😀</t></r>'
        '<rPh sb="0" eb="2"><t>발음 제외</t></rPh></si></sst>',
        "xl/styles.xml": f'<styleSheet xmlns="{S}"><numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>'
        '<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{S}" xmlns:r="{R}"><dimension ref="A1:XFD1048576"/>'
        '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="inlineStr"><is><t>항목|값\n둘째줄</t></is></c></row>'
        '<row r="3"><c r="A3"><v>9007199254740993</v></c><c r="B3"><v>0.10</v></c>'
        '<c r="C3"><f>A3+B3</f><v>9007199254740993.10</v></c><c r="D3"><f t="shared" si="0" ref="D3:D4">B3*2</f><v>0.2</v></c>'
        '<c r="E3" t="b"><v>1</v></c><c r="F3" t="e"><v>#DIV/0!</v></c><c r="G3" s="1"><v>45000</v></c>'
        '<c r="H3"><f>SUM(A3:B3)</f></c></row><row r="4"><c r="D4"><f t="shared" si="0"/><v>0</v></c></row></sheetData>'
        '<mergeCells count="1"><mergeCell ref="A1:B2"/></mergeCells>'
        '<hyperlinks><hyperlink ref="C1" r:id="link"/></hyperlinks><drawing r:id="draw"/>'
        '<tableParts count="1"><tablePart r:id="table"/></tableParts><pageSetup orientation="landscape"/></worksheet>',
        "xl/worksheets/sheet2.xml": f'<worksheet xmlns="{S}"><sheetData><row><c t="inlineStr"><is><t>숨긴 값</t></is></c></row></sheetData></worksheet>',
        "xl/worksheets/_rels/sheet1.xml.rels": relationships([
            ('link', 'hyperlink', 'https://example.org', 'TargetMode="External"'),
            ('draw', 'drawing', '../drawings/drawing1.xml', ''), ('comment', 'comments', '../comments1.xml', ''),
            ('table', 'table', '../tables/table1.xml', '')]),
        "xl/comments1.xml": f'<comments xmlns="{S}"><authors><author>작성자</author></authors><commentList>'
        '<comment ref="C1" authorId="0"><text><t>주석</t></text></comment></commentList></comments>',
        "xl/tables/table1.xml": f'<table xmlns="{S}" id="1" name="Table1" displayName="Table1" ref="A3:B3"><tableColumns count="2">'
        '<tableColumn id="1" name="첫째"/><tableColumn id="2" name="둘째"/></tableColumns></table>',
        "xl/drawings/drawing1.xml": f'<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        f'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="{R}"><xdr:twoCellAnchor>'
        '<xdr:from><xdr:col>0</xdr:col><xdr:row>5</xdr:row></xdr:from><xdr:to><xdr:col>2</xdr:col><xdr:row>8</xdr:row></xdr:to>'
        '<xdr:pic><xdr:blipFill><a:blip r:embed="image"/></xdr:blipFill></xdr:pic><xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>',
        "xl/drawings/_rels/drawing1.xml.rels": relationships([('image', 'image', '../media/image1.png', '')]),
        "xl/media/image1.png": PNG,
    }
    files.update(changes or {})
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for name, value in files.items():
            if value is None:
                continue
            if strict and isinstance(value, str):
                value = value.replace(S, 'http://purl.oclc.org/ooxml/spreadsheetml/main').replace(R, 'http://purl.oclc.org/ooxml/officeDocument/relationships')
                value = value.replace('http://schemas.openxmlformats.org/drawingml/2006/', 'http://purl.oclc.org/ooxml/drawingml/')
            archive.writestr(name, value)


class ParserTests(unittest.TestCase):
    def test_round_trip_structure_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / '보고서.xlsx'
            package(source)
            parser = XlsxParser()
            doc = parser.parse(source)
            table = next(doc.iter_blocks(kind='table'))
            cells = {c.metadata['address']: c for c in table.cells}
            self.assertEqual((table.rows, table.columns), (4, 8))
            self.assertEqual((cells['A1'].row_span, cells['A1'].col_span), (2, 2))
            self.assertEqual(cells['A3'].metadata['value'], '9007199254740993')
            self.assertEqual(cells['B3'].metadata['value'], '0.10')
            self.assertEqual(cells['C3'].metadata['formula']['text'], 'A3+B3')
            self.assertEqual(cells['C3'].metadata['cache_status'], 'unverified')
            self.assertEqual(cells['H3'].metadata['cache_status'], 'missing')
            self.assertIsNone(cells['D4'].metadata['formula']['text'])
            self.assertEqual(cells['D4'].metadata['formula']['attributes']['si'], '0')
            self.assertIs(cells['E3'].metadata['value'], True)
            self.assertEqual(cells['G3'].metadata['style']['attributes']['numFmtId'], '164')
            self.assertEqual(cells['C1'].metadata['hyperlinks'][0]['relationship']['target'], 'https://example.org')
            self.assertEqual(len(table.metadata['structured_tables']), 1)
            self.assertEqual(doc.sections[1]['state'], 'hidden')
            self.assertEqual([h.title for h in doc.iter_headings()], ['매출 😀', '숨김'])
            texts = {b.id: b.text for b in doc.iter_blocks(kind='text')}
            self.assertEqual(table.to_grid(texts)[0][:2], ['한글 😀', None])
            self.assertEqual(len(list(doc.iter_blocks(heading_id='sheet-1', kind='text'))), 1)
            self.assertEqual(doc.page_status, 'incomplete')
            output = Path(directory) / 'out'
            doc.export(output)
            for name in ('document.json', 'texts.json', 'tables.json', 'headings.json', 'images.json', 'metadata.json'):
                json.loads((output / name).read_text(encoding='utf-8'))
            self.assertIn('한글 😀', (output / 'texts.txt').read_text(encoding='utf-8'))
            self.assertIn('\\|', (output / 'tables.md').read_text(encoding='utf-8'))
            self.assertEqual((output / doc.assets[0]['output_path']).read_bytes(), PNG)
            self.assertEqual(doc.to_dict()['blocks'], parser.parse(source).to_dict()['blocks'])
            self.assertEqual(len(doc.blocks), len(parser.parse(source).blocks))

    def test_strict_and_sparse(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'strict.xlsx'
            package(source, strict=True)
            self.assertEqual(len(XlsxParser().parse(source).assets), 1)
            package(source, {'xl/worksheets/sheet2.xml': f'<worksheet xmlns="{S}"><sheetData><row r="1048576"><c r="XFD1048576"><v>1</v></c></row></sheetData></worksheet>'})
            doc = XlsxParser().parse(source)
            table = list(doc.iter_blocks(kind='table'))[1]
            self.assertEqual(len(table.cells), 1)
            self.assertEqual((table.rows, table.columns), (1048576, 16384))
            with self.assertRaises(ValueError):
                table.to_grid({})
            doc.export(Path(directory) / 'sparse')

    def test_rejects_unsafe_and_malformed_inputs(self):
        invalid_sheets = [
            '<sheetData><row r="1"><c r="A1" t="s"><v>-1</v></c></row></sheetData>',
            '<sheetData><row r="1"><c r="A1" t="b"><v>2</v></c></row></sheetData>',
            '<sheetData><row r="1"><c r="A1"><v>NaN</v></c></row></sheetData>',
            '<sheetData><row r="1"><c r="A2"><v>1</v></c></row></sheetData>',
            '<sheetData/><mergeCells><mergeCell ref="A1:B2"/><mergeCell ref="B2:C3"/></mergeCells>',
            '<sheetData/><mergeCells><mergeCell ref="B2:A1"/></mergeCells>',
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'bad.xlsx'
            for content in invalid_sheets:
                with self.subTest(content=content):
                    package(source, {'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{S}">{content}</worksheet>'})
                    with self.assertRaises(XlsxParseError):
                        XlsxParser().parse(source)
            for changes in [
                {'../escape': 'bad'},
                {'xl/workbook.xml': '<!DOCTYPE root [<!ENTITY x "unsafe">]><root>&x;</root>'},
                {'xl/workbook.xml': '<broken>'},
                {'xl/sharedStrings.xml': None},
                {'_rels/.rels': relationships([('book', 'officeDocument', '../../escape', '')])},
            ]:
                with self.subTest(changes=changes):
                    package(source, changes)
                    with self.assertRaises(XlsxParseError):
                        XlsxParser().parse(source)
            source.write_bytes(b'not zip')
            with self.assertRaises(XlsxParseError):
                XlsxParser().parse(source)
            package(source)
            for limits in (ParseLimits(max_file_bytes=10), ParseLimits(max_nodes=10), ParseLimits(max_blocks=1), ParseLimits(max_entries=2)):
                with self.assertRaises(XlsxParseError):
                    XlsxParser(limits).parse(source)
        for ref in ('A0', 'XFE1', 'A1048577', 'bad', 'A-1'):
            with self.assertRaises(XlsxParseError):
                coordinates(ref)

    def test_missing_and_external_images_are_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'partial.xlsx'
            for changes in ({'xl/media/image1.png': None}, {'xl/drawings/_rels/drawing1.xml.rels': relationships([
                ('image', 'image', 'https://example.org/image.png', 'TargetMode="External"')])}):
                package(source, changes)
                doc = XlsxParser().parse(source)
                self.assertEqual(doc.structure_status, 'partial')
                self.assertFalse(doc._asset_bytes)
                doc.export(Path(directory) / 'out')

    def test_cli_and_batch_continue_after_failure(self):
        import main as batch
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            root = Path(directory)
            (root / 'doc').mkdir()
            source = root / 'doc' / 'good.XLSX'
            package(source)
            self.assertEqual(cli([str(source), '--output', str(root / 'single')]), 0)
            (root / 'doc' / 'bad.xlsx').write_bytes(b'bad')
            (root / 'doc' / '~$lock.xlsx').write_bytes(b'bad')
            with patch.object(batch, '__file__', str(root / 'main.py')):
                self.assertEqual(batch.main(), 1)
            self.assertTrue((root / 'output' / 'good.XLSX' / 'document.json').exists())
            self.assertFalse((root / 'output' / '~$lock.xlsx').exists())
            with patch.object(batch, '__file__', str(root / 'empty' / 'main.py')):
                self.assertEqual(batch.main(), 1)


if __name__ == '__main__':
    unittest.main()
