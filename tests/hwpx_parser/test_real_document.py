"""Opt-in validation against the user's document. The document is not a test fixture."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import unittest
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring

from hwpx_parser import HwpxParser, TableBlock, TextBlock


@unittest.skipUnless(os.environ.get("HWPX_SAMPLE"), "Set HWPX_SAMPLE to run real-document validation")
class RealDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(os.environ["HWPX_SAMPLE"])
        profile_path = os.environ.get("HWPX_PROFILE")
        profile = json.loads(Path(profile_path).read_text(encoding="utf-8")) if profile_path else {}
        cls.doc = HwpxParser(**profile).parse(cls.path)

    def test_structure_counts_and_text_against_source_xml(self):
        ns = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'
        expected = Counter()
        expected_texts = []
        expected_cells = []
        with ZipFile(self.path) as archive:
            for section in self.doc.sections:
                root = fromstring(archive.read(section['path']))
                for element in root.iter():
                    if element.tag in {ns+'p', ns+'tbl', ns+'pic'}:
                        expected[element.tag] += 1
                    if element.tag == ns+'p':
                        chunks = [''.join(t.itertext()) for run in element.findall(ns+'run') for t in run.findall(ns+'t')]
                        expected_texts.append(''.join(''.join(chunks).split()))
                    if element.tag == ns+'tc':
                        address = element.find(ns+'cellAddr').attrib
                        span = element.find(ns+'cellSpan').attrib
                        expected_cells.append((int(address['rowAddr']), int(address['colAddr']), int(span['rowSpan']), int(span['colSpan'])))
        counts = Counter(b.kind for b in self.doc.blocks)
        self.assertEqual(counts['text'], expected[ns+'p'])
        self.assertEqual(counts['table'], expected[ns+'tbl'])
        self.assertEqual(counts['image'], expected[ns+'pic'])
        actual_texts = [''.join(b.text.split()) for b in self.doc.blocks if isinstance(b, TextBlock)]
        self.assertEqual(actual_texts, expected_texts)
        actual_cells = [(c.row, c.column, c.row_span, c.col_span) for b in self.doc.blocks if isinstance(b, TableBlock) for c in b.cells]
        self.assertCountEqual(actual_cells, expected_cells)

    def test_references_assets_heading_tree_and_no_false_verified_pages(self):
        ids = {b.id for b in self.doc.blocks}
        self.assertEqual(len(ids), len(self.doc.blocks))
        cells = {c.id for b in self.doc.blocks if isinstance(b, TableBlock) for c in b.cells}
        headings = {h.id for h in self.doc.headings}
        for b in self.doc.blocks:
            self.assertTrue(b.parent_id is None or b.parent_id in ids | cells)
            self.assertIn(b.heading_id, headings)
            self.assertNotEqual(b.location.status, 'verified')
        self.assertEqual(len(list(self.doc.iter_headings())), len(headings) - 1)
        with ZipFile(self.path) as archive:
            for asset in self.doc.assets:
                if asset['path'] is not None and asset['size_bytes'] is not None:
                    self.assertEqual(hashlib.sha256(archive.read(asset['path'])).hexdigest(), asset['sha256'])
                self.assertTrue(set(asset['used_by']) <= ids)


if __name__ == '__main__':
    unittest.main()
