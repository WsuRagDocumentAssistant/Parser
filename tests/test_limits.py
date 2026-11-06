"""Resource limits added after the 2026-11 security review (codex-security findings)."""
import time
import unittest

from document_parser.adapter import MAX_TABLE_MARKDOWN_CHARS, ModelAdapter
from document_parser.models import Cell
from document_parser.semantics import toc_entry
from document_parser.table_hierarchy.header_col_detector import _UNIT_VALUE_PATTERN, is_value_like_cell
from document_parser.table_hierarchy.header_row_detector import _DATA_PATTERN, is_data_like_row


def cell(row, col, text, row_span=1, col_span=1):
    return Cell(row, col, row_span, col_span, text, [text], [], [])


class MergedCellMarkdownTest(unittest.TestCase):
    def test_small_merged_header_still_repeats(self):
        cells = [cell(0, 0, "제목", col_span=3), cell(1, 0, "a"), cell(1, 1, "b"), cell(1, 2, "c")]
        self.assertEqual(ModelAdapter.table_markdown(cells, 2, 3, [0]).splitlines()[0], "| 제목 | 제목 | 제목 |")

    def test_wide_merged_cell_does_not_amplify(self):
        big = "가" * 100_000
        cells = [cell(0, 0, big, col_span=1000)] + [cell(1, c, str(c)) for c in range(1000)]
        markdown = ModelAdapter.table_markdown(cells, 2, 1000)
        self.assertLess(len(markdown), MAX_TABLE_MARKDOWN_CHARS)
        self.assertEqual(markdown.count(big), 1)


class LinearPatternTest(unittest.TestCase):
    def test_toc_entries(self):
        self.assertEqual(toc_entry("Ⅰ. 서론 ........ 3"), "Ⅰ. 서론")
        self.assertEqual(toc_entry("가. 목적…… 5"), "가. 목적")
        self.assertIsNone(toc_entry("제목 . 7"))
        self.assertIsNone(toc_entry("A......X"))

    def test_long_inputs_finish_quickly(self):
        start = time.monotonic()
        toc_entry("A" + "." * 200_000 + "X")
        _DATA_PATTERN.search("1" * 200_000 + "x")
        _UNIT_VALUE_PATTERN.search("1" * 200_000 + "x")
        is_value_like_cell("1" * 200_000 + "x")
        is_data_like_row({"texts": ["1" * 200_000 + "x"]})
        self.assertLess(time.monotonic() - start, 2)

    def test_data_pattern_meaning_unchanged(self):
        for text in ("12.5%", "300원", "2024.03", "1,000명", "x99건y"):
            self.assertTrue(_DATA_PATTERN.search(text) or _UNIT_VALUE_PATTERN.search(text), text)
        self.assertIsNone(_DATA_PATTERN.search("abc"))


if __name__ == "__main__":
    unittest.main()
