"""Small OOXML packages exercise the public API, CLI and input boundaries."""
from contextlib import redirect_stdout, redirect_stderr
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from docx_parser import DocxParser, DocxParseError, ParseLimits
from docx_parser.__main__ import main as cli
from docx_parser.package import NS
import main as batch

W, R, A = NS["w"][0], NS["r"][0], NS["a"][0]
REL = NS["rel"][0]
CT = NS["ct"][0]
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def relationship(ident, kind, target, external=False):
    return f'<Relationship Id="{ident}" Type="{R}/{kind}" Target="{target}"' + (' TargetMode="External"' if external else '') + '/>'


def package(body, *, styles=None, relationships="", parts=None, main="word/document.xml", strict=False):
    contents = {
        "[Content_Types].xml": f'<Types xmlns="{CT}"><Default Extension="xml" ContentType="application/xml"/>'
                              '<Default Extension="png" ContentType="image/png"/>'
                              f'<Override PartName="/{main}" ContentType="{MAIN_TYPE}"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="{REL}">' + relationship("main", "officeDocument", main) + '</Relationships>',
        main: f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" '
              f'xmlns:wp="{NS["wp"][0]}" xmlns:mc="{NS["mc"][0]}" xmlns:v="{NS["v"][0]}">'
              f'<w:body>{body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>',
    }
    main_path = Path(main)
    if styles is not None:
        contents[str(main_path.parent / "styles.xml").replace("\\", "/")] = f'<w:styles xmlns:w="{W}">{styles}</w:styles>'
        relationships += relationship("styles", "styles", "styles.xml")
    rel_path = str(main_path.parent / "_rels" / (main_path.name + ".rels")).replace("\\", "/")
    contents[rel_path] = f'<Relationships xmlns="{REL}">{relationships}</Relationships>'
    contents.update(parts or {})
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, value in contents.items():
            if strict and isinstance(value, str):
                for key in ("w", "r", "a", "wp"):
                    value = value.replace(NS[key][0], NS[key][1])
            archive.writestr(name, value.encode("utf-8") if isinstance(value, str) else value)
    return stream.getvalue()


def p(text, properties=""):
    return f'<w:p><w:pPr>{properties}</w:pPr><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "문서.docx"

    def parse(self, data, **options):
        self.source.write_bytes(data)
        return DocxParser(**options).parse(self.source)

    def test_text_headings_and_export(self):
        styles = '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
        styles += '<w:style w:type="paragraph" w:styleId="Derived"><w:name w:val="Custom"/><w:basedOn w:val="Heading1"/></w:style>'
        document = self.parse(package(p("제목 😀", '<w:pStyle w:val="Derived"/>') + p(" 본문 &amp; 내용 "), styles=styles))
        texts = list(document.iter_blocks(kind="text"))
        self.assertEqual([b.text for b in texts], ["제목 😀", " 본문 & 내용 "])
        heading = next(document.iter_headings())
        self.assertEqual((heading.title, heading.level, heading.source), ("제목 😀", 1, "outline"))
        self.assertEqual(list(document.iter_blocks(heading_id=heading.id)), texts)
        self.assertEqual(document.structure_status, "complete")
        self.assertEqual(document.page_status, "incomplete")
        output = document.export(self.root / "output")
        for name in ("document.json", "texts.json", "tables.json", "headings.json", "images.json", "metadata.json",
                     "texts.txt", "tables.md", "headings.md"):
            self.assertTrue((output.parent / name).is_file(), name)
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["blocks"][0]["text"], "제목 😀")
        again = DocxParser().parse(self.source)
        self.assertEqual(again.to_dict(), document.to_dict())

    def test_outline_body_level_mapping_and_local_scope(self):
        styles = '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
        body = p("본문 수준", '<w:pStyle w:val="Heading1"/><w:outlineLvl w:val="9"/>')
        body += p("실제 제목", '<w:outlineLvl w:val="0"/>')
        body += '<w:tbl><w:tr><w:tc>' + p("셀 제목", '<w:outlineLvl w:val="1"/>') + '</w:tc></w:tr></w:tbl>'
        body += p("다음 본문")
        doc = self.parse(package(body, styles=styles), heading_scopes=("body", "cell"))
        self.assertEqual([h.title for h in doc.iter_headings()], ["실제 제목", "셀 제목"])
        self.assertEqual(doc.blocks[-1].heading_id, next(doc.iter_headings()).id)
        doc = DocxParser(style_levels={"Heading1": 3}).parse(self.source)
        self.assertEqual(next(doc.iter_headings()).level, 3)
        with self.assertRaises(ValueError):
            DocxParser(style_levels={"x": True})

    def test_merged_nested_tables(self):
        nested = '<w:tbl><w:tr><w:tc>' + p("중첩") + '</w:tc></w:tr></w:tbl>'
        body = '<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid><w:tr><w:tc><w:tcPr>'
        body += '<w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>' + p("병합") + nested
        body += '</w:tc><w:tc>' + p("오른쪽") + '</w:tc></w:tr><w:tr><w:tc><w:tcPr>'
        body += '<w:gridSpan w:val="2"/><w:vMerge/></w:tcPr>' + p("")
        body += '</w:tc><w:tc>' + p("아래") + '</w:tc></w:tr></w:tbl>'
        doc = self.parse(package(body))
        table, inside = list(doc.iter_blocks(kind="table"))
        self.assertEqual((table.rows, table.columns, len(table.cells)), (2, 3, 3))
        cell = table.cells[0]
        self.assertEqual((cell.row_span, cell.col_span), (2, 2))
        self.assertIn(inside.id, cell.block_ids)
        grid = table.to_grid({b.id: b.text for b in doc.iter_blocks(kind="text")})
        self.assertEqual(grid[0], ["병합\n", None, "오른쪽"])
        self.assertEqual(grid[1], [None, None, "아래"])
        self.assertEqual([b.text for b in doc.iter_blocks(kind="text")].count("중첩"), 1)

    def test_story_relationships_and_images(self):
        drawing = '<w:drawing><wp:inline><wp:docPr id="1" name="Picture"/><a:blip r:embed="img"/></wp:inline></w:drawing>'
        header = f'<w:hdr xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="{NS["wp"][0]}"><w:p><w:r>{drawing}</w:r></w:p></w:hdr>'
        notes = f'<w:footnotes xmlns:w="{W}"><w:footnote w:type="separator" w:id="-1">{p("제외")}</w:footnote><w:footnote w:id="1">{p("각주")}</w:footnote></w:footnotes>'
        rels = relationship("img", "image", "media/shared.png") + relationship("hdr", "header", "header1.xml")
        rels += relationship("fn", "footnotes", "footnotes.xml")
        parts = {"word/header1.xml": header, "word/footnotes.xml": notes, "word/media/shared.png": b"\x89PNG\r\n\x1a\nfixture",
                 "word/_rels/header1.xml.rels": f'<Relationships xmlns="{REL}">' + relationship("img", "image", "media/shared.png") + '</Relationships>'}
        doc = self.parse(package('<w:p><w:r><w:t>A</w:t>' + drawing + '<w:t>B</w:t><w:footnoteReference w:id="1"/></w:r></w:p>', relationships=rels, parts=parts))
        self.assertEqual(doc.blocks[0].text, "AB")
        images = list(doc.iter_blocks(kind="image"))
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0].asset_id, images[1].asset_id)
        self.assertEqual(len(doc.assets), 1)
        self.assertEqual([b.text for b in doc.iter_blocks(kind="text") if b.scope == "footnote"], ["각주"])
        doc.export(self.root / "output")
        self.assertEqual((self.root / "output" / doc.assets[0]["output_path"]).read_bytes(), parts["word/media/shared.png"])

    def test_revisions_fields_and_alternate_content(self):
        body = '<w:p><w:r><w:t>한😀</w:t><w:tab/><w:t>글</w:t><w:br/><w:noBreakHyphen/><w:softHyphen/>'
        body += '<w:instrText> PAGE </w:instrText></w:r><w:del><w:r><w:delText>삭제</w:delText></w:r></w:del>'
        body += '<w:ins><w:r><w:t>추가</w:t></w:r></w:ins><mc:AlternateContent><mc:Choice Requires="future" xmlns:future="urn:unsupported">'
        body += '<w:r><w:t>미지원</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>대체</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p>'
        doc = self.parse(package(body))
        self.assertEqual(doc.blocks[0].text, "한😀\t글\n\u2011\u00ad추가대체")
        self.assertEqual(doc.blocks[0].runs[0]["end"], 7)
        self.assertTrue(any(e["kind"] == "instrText" for e in doc.blocks[0].content))
        self.assertFalse(any(d["severity"] == "warning" for d in doc.diagnostics))

    def test_cached_pages_and_explicit_breaks(self):
        body = p("첫 페이지") + '<w:p><w:r><w:lastRenderedPageBreak/><w:t>둘째</w:t><w:br w:type="page"/>'
        body += '<w:lastRenderedPageBreak/><w:t>셋째</w:t></w:r></w:p>'
        doc = self.parse(package(body))
        self.assertEqual(doc.metadata["estimated_page_count"], 3)
        self.assertEqual(doc.blocks[0].location.physical_pages, [1])
        self.assertEqual(doc.blocks[1].location.physical_pages, [2, 3])
        self.assertEqual(doc.blocks[1].location.status, "estimated")
        self.assertEqual(DocxParser(page_mode="structural").parse(self.source).page_status, "incomplete")
        doc = self.parse(package('<w:p><w:r><w:br w:type="page"/><w:t>본문</w:t></w:r></w:p>'))
        self.assertIsNone(doc.blocks[0].location.physical_pages)

    def test_strict_and_relocated_main_part(self):
        doc = self.parse(package(p("Strict 문서"), main="custom/content.xml", strict=True))
        self.assertEqual(doc.blocks[0].text, "Strict 문서")
        self.assertEqual(doc.metadata["main_part"], "custom/content.xml")

    def test_missing_external_images_and_unknown_content(self):
        body = '<w:p><w:r><w:drawing><a:blip r:link="img"/></w:drawing></w:r></w:p><w:altChunk r:id="html"/>'
        doc = self.parse(package(body, relationships=relationship("img", "image", "https://example.com/image.png", True)))
        self.assertEqual(doc.structure_status, "partial")
        self.assertIsNone(doc.assets[0]["sha256"])
        self.assertEqual({d["code"] for d in doc.diagnostics}, {"external_image", "unsupported_content"})
        doc = self.parse(package(body, relationships=relationship("img", "image", "media/missing.png")))
        self.assertTrue(any(d["code"] == "missing_asset" for d in doc.diagnostics))

    def test_untrusted_input_limits_and_invalid_merges(self):
        valid = package(p("안전"))
        with self.assertRaises(DocxParseError):
            self.parse(valid, limits=ParseLimits(max_file_bytes=8))
        with self.assertRaises(DocxParseError):
            self.parse(valid, limits=ParseLimits(max_depth=3))
        with self.assertRaises(DocxParseError):
            self.parse(valid, limits=ParseLimits(max_nodes=3))
        with self.assertRaises(DocxParseError):
            self.parse(package(p("A") + p("B")), limits=ParseLimits(max_blocks=1))
        for parts in ({"../escape": b"x"}, {"[Content_Types].xml": '<!DOCTYPE x [<!ENTITY a "x">]><Types>&a;</Types>'}):
            with self.assertRaises(DocxParseError):
                self.parse(package(p("본문"), parts=parts))
        with self.assertRaises(DocxParseError):
            self.parse(package(p("본문"), relationships=relationship("img", "image", "../../outside.png")))
        with self.assertRaises(DocxParseError):
            self.parse(package('<w:tbl><w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr>' + p("오류") + '</w:tc></w:tr></w:tbl>'))
        with self.assertRaises(DocxParseError):
            self.parse(b"not a ZIP / encrypted OLE")

    def test_style_cycles_and_unknown_namespace(self):
        styles = '<w:style w:styleId="A"><w:basedOn w:val="B"/></w:style><w:style w:styleId="B"><w:basedOn w:val="A"/></w:style>'
        with self.assertRaises(DocxParseError):
            self.parse(package(p("본문", '<w:pStyle w:val="A"/>'), styles=styles))
        doc = self.parse(package('<fake:p xmlns:fake="urn:fake"><fake:t>사칭</fake:t></fake:p>'))
        self.assertEqual(len(list(doc.iter_blocks(kind="text"))), 0)
        self.assertEqual(doc.structure_status, "partial")

    def test_wrapped_rows_cells_and_sections(self):
        body = p("구역 1", '<w:sectPr><w:type w:val="continuous"/></w:sectPr>')
        body += '<w:tbl><w:sdt><w:sdtContent><w:tr><w:sdt><w:sdtContent><w:tc>'
        body += p("컨트롤 셀") + '</w:tc></w:sdtContent></w:sdt></w:tr></w:sdtContent></w:sdt></w:tbl>'
        body += p("구역 2")
        doc = self.parse(package(body))
        self.assertEqual(doc.metadata["section_count"], 2)
        table = next(doc.iter_blocks(kind="table"))
        self.assertEqual((table.rows, table.columns), (1, 1))
        self.assertEqual(table.source_ref["section_index"], 1)
        self.assertEqual([b.text for b in doc.iter_blocks(kind="text")], ["구역 1", "컨트롤 셀", "구역 2"])

    def test_textbox_image_has_one_use_and_no_body_pagination(self):
        drawing = '<w:drawing><wp:inline><a:blip r:embed="img"/></wp:inline></w:drawing>'
        box = '<w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent><w:p><w:r>'
        box += '<w:t>글상자</w:t><w:lastRenderedPageBreak/>' + drawing
        box += '</w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>'
        doc = self.parse(package(box, relationships=relationship("img", "image", "media/a.png"), parts={"word/media/a.png": b"png"}))
        self.assertEqual(len(list(doc.iter_blocks(kind="image"))), 1)
        self.assertEqual([b.text for b in doc.iter_blocks(kind="text") if b.scope == "textbox"], ["글상자"])
        self.assertIsNone(doc.metadata["estimated_page_count"])

    def test_auxiliary_story_not_owned_by_last_body_heading(self):
        header = f'<w:hdr xmlns:w="{W}">' + p("머리말", '<w:outlineLvl w:val="0"/>') + '</w:hdr>'
        doc = self.parse(package(p("본문 제목", '<w:outlineLvl w:val="0"/>'),
                                relationships=relationship("hdr", "header", "header.xml"),
                                parts={"word/header.xml": header}))
        self.assertEqual(len(list(doc.iter_headings())), 1)
        self.assertTrue(all(b.heading_id == "root" for b in doc.blocks if b.scope == "header"))

    def test_duplicate_entries_and_compression_limit(self):
        stream = BytesIO(package(p("본문")))
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with ZipFile(stream, "a") as archive:
                archive.writestr("word/document.xml", "duplicate")
        with self.assertRaises(DocxParseError):
            self.parse(stream.getvalue())
        with self.assertRaises(DocxParseError):
            self.parse(package(p("a" * 10000)), limits=ParseLimits(max_compression_ratio=2))

    def test_cli_profile_and_page_requirements(self):
        self.source.write_bytes(package(p("제목", '<w:pStyle w:val="Custom"/>')))
        profile = self.root / "profile.json"
        profile.write_text(json.dumps({"style_levels": {"Custom": 2}}), encoding="utf-8")
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(cli([str(self.source), "--output", str(self.root / "out"), "--profile", str(profile)]), 0)
            self.assertEqual(cli([str(self.source), "--output", str(self.root / "out"), "--require-pages"]), 3)
            self.assertEqual(cli([str(self.source), "--output", str(self.root / "out"), "--require-verified-pages"]), 3)
            profile.write_text('{"unknown": 1}', encoding="utf-8")
            self.assertEqual(cli([str(self.source), "--profile", str(profile)]), 2)
        self.assertTrue((self.root / "out" / "document.json").exists())

    def test_batch_uses_project_root_and_continues(self):
        (self.root / "doc").mkdir()
        (self.root / "doc" / "good.DOCX").write_bytes(package(p("성공")))
        (self.root / "doc" / "bad.docx").write_bytes(b"invalid")
        (self.root / "doc" / "~$lock.docx").write_bytes(b"lock")
        with patch.object(batch, "__file__", str(self.root / "main.py")), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(batch.main(), 1)
        self.assertTrue((self.root / "output" / "good.DOCX" / "structure" / "texts.txt").is_file())
        self.assertFalse((self.root / "output" / "~$lock.docx").exists())


if __name__ == "__main__":
    unittest.main()
