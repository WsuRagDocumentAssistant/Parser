from contextlib import redirect_stdout, redirect_stderr
from dataclasses import fields
from io import StringIO
import importlib.util
import json
from pathlib import Path
import pickle
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from document_parser import DocumentParser, DocumentModel, parse
from document_parser.__main__ import main as cli
from document_parser import models
from tests.hwpx_parser.test_parser import package as hwpx, para, table, picture, SETUP
from tests.docx_parser.test_parser import package as docx, p
from tests.xlsx_parser.test_parser import package as xlsx

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def test_four_formats_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hwpx(root / '한글.HWPX', para('제목', prop='1', extra=SETUP) + para('본문', extra=table(inner=picture()) + picture()))
            (root / '문서.docx').write_bytes(docx(p('제목', properties='<w:outlineLvl w:val="0"/>') + p('본문')))
            xlsx(root / '계산.xlsx')
            (root / '문서.pdf').write_bytes((ROOT / 'tests/pdf_parser/fixtures/sample.pdf').read_bytes())
            parser = DocumentParser()
            for source in list(root.iterdir()):
                with self.subTest(format=source.suffix):
                    model = parser.parse(source)
                    self.assertIsInstance(model, DocumentModel)
                    self.assertEqual(set(model.to_dict()), {'file', 'images', 'blocks'})
                    self.assertEqual(model.file.filename, source.name)
                    self.assertEqual([b.order for b in model.blocks], list(range(len(model.blocks))))
                    self.assertEqual(len({b.id for b in model.blocks}), len(model.blocks))
                    self.assertEqual(model.file.table_count, len(list(model.tables())))
                    self.assertTrue(model.searchable_blocks())
                    headings = {b.id for b in model.blocks if b.role == '제목'}
                    self.assertTrue(all(set(b.heading_path) <= headings for b in model.blocks))
                    self.assertEqual(pickle.loads(pickle.dumps(model)).to_dict(), model.to_dict())
                    path = model.export(root / (source.name + '.out'))
                    self.assertEqual(json.loads(path.read_text(encoding='utf-8')), model.to_dict())
                    self.assertTrue((path.parent / 'structure/document.json').exists())
                    for image in model.document_images:
                        self.assertTrue(Path(image.path).is_file())
                    self.assertEqual(model.structure.metadata['format'], source.suffix.lower()[1:])
                    for block in model.blocks:
                        if block.figure and block.figure.image and block.figure.image.ref in model.structure._asset_bytes:
                            self.assertEqual(block.figure.image.path, model.images[block.figure.image.ref].path)
                    first_images = list(model.document_images)
                    model.export(path.parent)
                    self.assertEqual(model.document_images, first_images)

    def test_cells_nested_tables_and_image_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'nested.hwpx'
            hwpx(source, para('본문', extra=SETUP + table('외부 셀', inner=table('내부 셀') + picture()) + picture()))
            model = parse(source, image_dir=Path(directory) / 'images')
            self.assertEqual(len(list(model.tables())), 2)
            self.assertEqual(sum(b.table is not None for b in model.blocks), 1)
            self.assertFalse(any(b.text in {'외부 셀', '내부 셀'} for b in model.blocks))
            outer = next(b.table for b in model.blocks if b.table)
            self.assertEqual(outer.cells[0].text, '외부 셀')
            self.assertEqual(outer.children[0].cells[0].text, '내부 셀')
            self.assertIn('내부 셀', outer.markdown)
            self.assertEqual(outer.children[0].parent.table_id, outer.id)
            self.assertTrue(outer.cells[0].images)
            self.assertTrue(Path(outer.cells[0].images[0].path).is_file())
            self.assertEqual(sum(b.figure is not None for b in model.blocks), 1)

    def test_pdf_does_not_load_ocr_by_default(self):
        with patch('pdf_parser.PaddleOcr', side_effect=AssertionError('OCR must stay disabled')):
            model = parse(ROOT / 'tests/pdf_parser/fixtures/sample.pdf')
            self.assertEqual(model.structure.page_status, 'verified')
            self.assertTrue(any('한글' in (b.text or '') for b in model.blocks))

    def test_file_property_mapping(self):
        from hwpx_parser.models import Document
        from document_parser.adapter import ModelAdapter
        core = {'tag': 'coreProperties', 'children': [
            {'tag': 'dc:creator', 'text': '작성자', 'children': []},
            {'tag': 'cp:lastModifiedBy', 'text': '수정자', 'children': []}]}
        document = Document({'filename': '문서.xlsx', 'format': 'xlsx',
                             'properties': {'docProps/core.xml': core}})
        info = ModelAdapter(document).build().file
        self.assertEqual((info.creator, info.last_saved_by), ('작성자', '수정자'))
        document.metadata = {'filename': '문서.pdf', 'format': 'pdf',
                             'properties': {'/Author': 'PDF 작성자', '/Title': '원본 제목'}}
        info = ModelAdapter(document).build().file
        self.assertEqual((info.creator, info.title), ('PDF 작성자', '원본 제목'))

    def test_unified_cli_forwards_server_and_korean_ocr(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            for name in ('server', 'korean'):
                with patch('pdf_parser.PaddleOcr') as backend:
                    self.assertEqual(cli([str(ROOT / 'tests/pdf_parser/fixtures/sample.pdf'),
                        '--ocr', 'paddle', '--ocr-model', name, '--ocr-dpi', '144',
                        '--ocr-min-confidence', '0.7', '--output', directory]), 0)
                    backend.assert_called_once_with(model=name, dpi=144, min_confidence=0.7, det_limit_side_len=960)
                    backend.return_value.recognize.assert_not_called()

    def test_cli_batch_and_boundaries(self):
        import main as batch
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            root = Path(directory)
            (root / 'doc').mkdir()
            hwpx(root / 'doc/a.hwpx')
            (root / 'doc/bad.pdf').write_bytes(b'bad')
            (root / 'doc/~$lock.docx').write_bytes(b'lock')
            with patch.object(batch, '__file__', str(root / 'main.py')):
                self.assertEqual(batch.main(), 1)
            self.assertTrue((root / 'output/a.hwpx/document.json').is_file())
            self.assertFalse((root / 'output/~$lock.docx').exists())
            self.assertEqual(cli([str(root / 'doc/a.hwpx'), '--output', str(root / 'single')]), 0)
            self.assertEqual(cli([str(root / 'doc/bad.pdf')]), 2)
            with self.assertRaises(ValueError):
                parse(root / 'bad.hwp')
            with self.assertRaises(ValueError):
                parse(root / 'doc/a.hwpx', password='secret')
            with self.assertRaises(ValueError):
                DocumentParser(unknown={})

    def test_upstream_contract_and_real_chunker(self):
        reference = ROOT / '.reference'
        upstream = reference / 'HwpxParser_sub/hwpx/analysis/document_model.py'
        chunker = reference / 'RagModuls/ragmodul/service/chunker_service.py'
        if not upstream.is_file() or not chunker.is_file():
            self.skipTest('Read-only organization checkouts are not included in the distribution')
        spec = importlib.util.spec_from_file_location('wsu_upstream_models', upstream)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        for name in ('FileInfo', 'ImageFile', 'ImageRef', 'TableColumn', 'TableHeader',
                     'TableRecord', 'Cell', 'TableParent', 'Table', 'Figure', 'TocRef',
                     'TocEntry', 'ExcludedTable', 'Block', 'DocumentModel'):
            self.assertEqual([(f.name, str(f.type)) for f in fields(getattr(models, name))],
                             [(f.name, str(f.type)) for f in fields(getattr(module, name))])
        # Load only the actual chunker and its stdlib dataclasses, avoiding model/DB startup.
        package = types.ModuleType('wsu_contract')
        package.__path__ = [str(reference / 'RagModuls/ragmodul')]
        sys.modules['wsu_contract'] = package
        service = types.ModuleType('wsu_contract.service')
        service.__path__ = [str(chunker.parent)]
        sys.modules[service.__name__] = service
        chunk = __import__('wsu_contract.service.chunker_service', fromlist=['chunk']).chunk
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hwpx(root / 'sample.hwpx', para('', extra=SETUP + table('Ⅰ. 사업 개요')) + para('개요 본문') +
                 para('', extra=table('Ⅱ. 추진 계획')) + para('계획 본문'))
            (root / 'sample.docx').write_bytes(docx(p('제목', properties='<w:outlineLvl w:val="0"/>') + p('본문')))
            xlsx(root / 'sample.xlsx')
            sources = list(root.iterdir()) + [ROOT / 'tests/pdf_parser/fixtures/sample.pdf']
            for source in sources:
                with self.subTest(format=source.suffix):
                    result = chunk(parse(source))
                    self.assertTrue(result.parents)
                    self.assertTrue(any(parent.children for parent in result.parents))
                    self.assertEqual(result.file.filename, source.name)
                    if source.suffix == '.hwpx':
                        self.assertGreaterEqual(len(result.parents), 2)


if __name__ == '__main__':
    unittest.main()
