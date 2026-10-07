from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from pdf_parser import PdfParser, PdfParseError, ParseLimits, PaddleOcr
from pdf_parser.__main__ import main as cli

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample.pdf"


class ParserTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("PDF_PARSER_TEST_SERVER_OCR") == "1", "Opt-in real server model/download test")
    def test_real_server_cpu_scan(self):
        from PIL import Image, ImageDraw, ImageFont
        from document_parser import DocumentParser
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'server-scan.pdf'
            image = Image.new('RGB', (1200, 400), 'white')
            ImageDraw.Draw(image).text((80, 130), 'CPU OCR TEST 123',
                                      fill='black', font=ImageFont.load_default(size=64))
            image.save(source, 'PDF', resolution=144)
            image.close()
            engine = PaddleOcr(model='server', dpi=144)
            model = DocumentParser(pdf={'limits': ParseLimits(timeout_seconds=600), 'ocr': engine}).parse(source)
            text = ' '.join(b.text or '' for b in model.blocks)
            # OCR spacing is probabilistic; verify the recognized characters/numbers.
            self.assertIn('CPUOCRTEST123', ''.join(text.upper().split()))
            self.assertEqual(model.structure.page_status, 'verified')
            section = model.structure.sections[0]
            self.assertFalse(section['has_text_layer'])
            self.assertEqual(section['ocr']['detection_model'], 'PP-OCRv5_server_det')
            self.assertEqual(section['ocr']['recognition_model'], 'PP-OCRv5_server_rec')

    @unittest.skipUnless(os.environ.get("PDF_PARSER_TEST_OCR") == "1", "Opt-in real model/download test")
    def test_real_paddle_korean_scan(self):
        import pdfplumber
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.pdf"
            with pdfplumber.open(SAMPLE) as pdf:
                image = pdf.pages[0].to_image(resolution=200).original
                image.save(source, "PDF", resolution=200)
                image.close()
            doc = PdfParser(ParseLimits(timeout_seconds=600), ocr=PaddleOcr(model="korean")).parse(source)
            texts = [b.text for b in doc.iter_blocks(kind="text")]
            self.assertTrue(any("한글" in text for text in texts), texts)
            self.assertEqual(doc.page_status, "verified")

    def test_text_table_outline_images_and_export(self):
        parser = PdfParser()
        doc = parser.parse(SAMPLE)
        texts = {b.id: b.text for b in doc.iter_blocks(kind="text")}
        self.assertIn("한글", "\n".join(texts.values()))
        self.assertIn("보고서", "\n".join(texts.values()))
        self.assertEqual(doc.metadata["page_count"], 2)
        self.assertEqual(doc.page_status, "verified")
        self.assertEqual(doc.structure_status, "partial")
        headings = list(doc.iter_headings())
        self.assertEqual([h.title for h in headings], ["PDF 파서 예제", "두 번째 페이지"])
        self.assertEqual(headings[1].parent_id, headings[0].id)
        tables = list(doc.iter_blocks(kind="table"))
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual((table.rows, table.columns), (3, 2))
        merged = next(c for c in table.cells if c.row == 0)
        self.assertEqual(merged.col_span, 2)
        self.assertIn("Merged", table.to_grid(texts)[0][0])
        self.assertIsNone(table.to_grid(texts)[0][1])
        self.assertEqual(sum("Merged" in t for t in texts.values()), 1)
        images = list(doc.iter_blocks(kind="image"))
        self.assertEqual(len(images), 2)
        self.assertEqual(len(doc.assets), 1)
        self.assertEqual(images[0].asset_id, images[1].asset_id)
        self.assertTrue(list(doc.iter_blocks(heading_id=headings[1].id, physical_page=2)))
        self.assertEqual([b.order for b in doc.blocks], list(range(len(doc.blocks))))
        for b in doc.blocks:
            self.assertEqual(b.location.physical_pages, [b.source_ref["page"]])
        with tempfile.TemporaryDirectory() as directory:
            doc.export(directory)
            for name in ("document", "texts", "tables", "images", "headings", "metadata"):
                json.loads((Path(directory) / f"{name}.json").read_text(encoding="utf-8"))
            self.assertIn("한글", (Path(directory) / "texts.txt").read_text(encoding="utf-8"))
            self.assertIn("Merged", (Path(directory) / "tables.md").read_text(encoding="utf-8"))
            self.assertEqual((Path(directory) / doc.assets[0]["output_path"]).read_bytes(),
                             doc._asset_bytes[doc.assets[0]["id"]])
        self.assertEqual(doc.to_dict()["blocks"], parser.parse(SAMPLE).to_dict()["blocks"])

    def test_font_headings_and_disabled_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "no-outline.pdf"
            writer = PdfWriter()
            for page in PdfReader(SAMPLE).pages:
                writer.add_page(page)
            writer.write(source)
            doc = PdfParser().parse(source)
            self.assertTrue(list(doc.iter_headings()))
            self.assertTrue(all(h.source == "font_size_heuristic" for h in doc.iter_headings()))
            self.assertFalse(list(PdfParser(auto_headings=False).parse(source).iter_headings()))
            no_tables = PdfParser(table_strategy="none").parse(source)
            self.assertFalse(list(no_tables.iter_blocks(kind="table")))
            self.assertIn("Merged", "\n".join(b.text for b in no_tables.iter_blocks(kind="text")))

    def test_blank_scan_and_vector_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)
            writer.write(source)
            doc = PdfParser().parse(source)
            self.assertEqual(doc.page_status, "verified")
            self.assertEqual(doc.status, "complete")
            self.assertFalse(doc.blocks)
            page = writer.pages[0]
            stream = DecodedStreamObject()
            stream.set_data(b"20 20 100 100 re S")
            page[NameObject("/Contents")] = writer._add_object(stream)
            writer.write(source)
            self.assertIn("no_text_layer", [d["code"] for d in PdfParser().parse(source).diagnostics])
            writer = PdfWriter()
            writer.add_page(PdfReader(SAMPLE).pages[0])
            page = writer.pages[0]
            image_name = next(iter(page["/Resources"]["/XObject"]))
            stream = DecodedStreamObject()
            stream.set_data(f"q 100 0 0 100 30 30 cm {image_name} Do Q".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
            writer.write(source)
            doc = PdfParser().parse(source)
            self.assertFalse(list(doc.iter_blocks(kind="text")))
            self.assertEqual(len(doc.assets), 1)
            self.assertIn("ocr_required", [d["code"] for d in doc.diagnostics])

    def test_encrypted_input_and_password_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "encrypted.pdf"
            writer = PdfWriter(clone_from=SAMPLE)
            writer.encrypt("secret", algorithm="AES-256")
            writer.write(source)
            for password in (None, "incorrect"):
                with self.assertRaises(PdfParseError):
                    PdfParser().parse(source, password=password)
            self.assertEqual(PdfParser().parse(source, password="secret").metadata["page_count"], 2)
            password_file = Path(directory) / "password.txt"
            password_file.write_text("secret\n", encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(cli([str(source), "--password-file", str(password_file),
                                      "--output", str(Path(directory) / "out")]), 0)

    def test_invalid_input_limits_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.pdf"
            for data in (b"not pdf", b"%PDF-1.7\nbroken"):
                source.write_bytes(data)
                with self.assertRaises(PdfParseError):
                    PdfParser().parse(source)
        for limits in (ParseLimits(max_file_bytes=10), ParseLimits(max_pages=1), ParseLimits(max_chars=1),
                       ParseLimits(max_page_objects=1), ParseLimits(max_blocks=1), ParseLimits(max_cells=1),
                       ParseLimits(max_image_pixels=1), ParseLimits(max_total_asset_bytes=1),
                       ParseLimits(max_headings=1)):
            with self.subTest(limits=limits), self.assertRaises(PdfParseError):
                PdfParser(limits).parse(SAMPLE)
        with patch("pdf_parser.parser.time.monotonic", side_effect=[0.0, 2.0]), self.assertRaises(PdfParseError):
            PdfParser(ParseLimits(timeout_seconds=1)).parse(SAMPLE)
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                ParseLimits(timeout_seconds=value)

    def test_cli_and_batch_continue_after_failure(self):
        import main as batch
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            root = Path(directory)
            (root / "doc").mkdir()
            source = root / "doc" / "good.PDF"
            source.write_bytes(SAMPLE.read_bytes())
            (root / "doc" / "bad.pdf").write_bytes(b"bad")
            self.assertEqual(cli([str(source), "--output", str(root / "single"), "--require-verified-pages"]), 0)
            self.assertEqual(cli([str(root / "doc" / "bad.pdf")]), 2)
            with patch.object(batch, "__file__", str(root / "main.py")):
                self.assertEqual(batch.main(), 1)
            self.assertTrue((root / "output" / "good.PDF" / "document.json").exists())
            with patch.object(batch, "__file__", str(root / "empty" / "main.py")):
                self.assertEqual(batch.main(), 1)

    def test_paddle_adapter_models_and_validation(self):
        from PIL import Image
        pipeline = MagicMock()
        pipeline.predict.return_value = [{"rec_texts": ["한글", "low"], "rec_scores": [0.95, 0.1],
                                          "rec_polys": [[[1, 2], [31, 2], [31, 12], [1, 12]]] * 2}]
        module = MagicMock()
        module.PaddleOCR.return_value = pipeline
        image = Image.new("RGB", (40, 20))
        with patch.dict("sys.modules", {"paddleocr": module, "numpy": MagicMock()}):
            engine = PaddleOcr()
            self.assertEqual(engine.recognize(image)[0]["text"], "한글")
            self.assertEqual(len(engine.recognize(image)), 1)
            self.assertEqual(module.PaddleOCR.call_count, 1)
            settings = module.PaddleOCR.call_args.kwargs
            self.assertEqual(settings["text_detection_model_name"], "PP-OCRv5_server_det")
            self.assertEqual(settings["text_recognition_model_name"], "PP-OCRv5_server_rec")
            self.assertEqual(settings["device"], "cpu")
            self.assertEqual(settings['text_det_limit_type'], 'max')
            self.assertEqual(settings['text_det_limit_side_len'], 960)
            self.assertFalse(settings["use_doc_unwarping"])
            pipeline.predict.return_value = [{"rec_texts": ["a"], "rec_scores": [], "rec_polys": []}]
            with self.assertRaises(ValueError):
                engine.recognize(image)
        image.close()
        korean = PaddleOcr(model="korean")
        self.assertEqual(korean.detection_model, 'PP-OCRv5_server_det')
        self.assertEqual(korean.recognition_model, 'korean_PP-OCRv5_mobile_rec')
        for config in ({"dpi": 0}, {"dpi": 601}, {"min_confidence": float("nan")}, {"min_confidence": 2},
                       {"model": "invalid"}, {"det_limit_side_len": 0}, {"det_limit_side_len": 2049}):
            with self.assertRaises(ValueError):
                PaddleOcr(**config)

    def test_scan_ocr_mapping_and_native_text_bypass(self):
        import pdfplumber
        class FakeOcr:
            dpi = 144
            detection_model = "PP-OCRv5_mobile_det"
            recognition_model = "korean_PP-OCRv5_mobile_rec"
            calls = 0
            def recognize(self, image):
                self.calls += 1
                return [{"text": "한글 OCR", "confidence": 0.97,
                         "polygon": [[20, 40], [220, 40], [220, 80], [20, 80]]}]
        engine = FakeOcr()
        PdfParser(ocr=engine).parse(SAMPLE)
        self.assertEqual(engine.calls, 0)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.pdf"
            with pdfplumber.open(SAMPLE) as pdf:
                image = pdf.pages[0].to_image(resolution=144).original
                image.save(source, "PDF", resolution=144)
                image.close()
            doc = PdfParser(ocr=engine).parse(source)
            block = next(doc.iter_blocks(kind="text"))
            self.assertEqual(block.text, "한글 OCR")
            self.assertEqual(block.source_ref["bbox"], [10, 20, 110, 40])
            self.assertTrue(block.source_ref["ocr"])
            self.assertAlmostEqual(block.metadata["confidence"], 0.97)
            self.assertEqual(block.location.physical_pages, [1])
            self.assertEqual(doc.page_status, "verified")
            self.assertFalse(doc.sections[0]["has_text_layer"])
            self.assertEqual(doc.sections[0]["ocr"]["line_count"], 1)
            self.assertIn("ocr_text_unverified", [d["code"] for d in doc.diagnostics])
            self.assertNotIn("ocr_required", [d["code"] for d in doc.diagnostics])
            with self.assertRaises(PdfParseError):
                PdfParser(ParseLimits(max_image_pixels=16 * 16), ocr=engine).parse(source)
            with self.assertRaises(PdfParseError):
                PdfParser(ParseLimits(max_chars=1), ocr=engine).parse(source)
            with redirect_stdout(StringIO()), patch("pdf_parser.__main__.PaddleOcr", return_value=engine):
                self.assertEqual(cli([str(source), "--ocr", "paddle", "--output", str(Path(directory) / "out")]), 0)
            writer = PdfWriter(clone_from=source)
            writer.encrypt("secret", algorithm="AES-256")
            writer.write(Path(directory) / "encrypted.pdf")
            self.assertTrue(list(PdfParser(ocr=engine).parse(Path(directory) / "encrypted.pdf",
                                                           password="secret").iter_blocks(kind="text")))


if __name__ == "__main__":
    unittest.main()
