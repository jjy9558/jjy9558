import os
import shutil
import tempfile
import unittest

from subvocab.output import fmt_time, highlight_entry, render_anki_tsv, render_markdown, write_outputs
from subvocab.vocab import Analysis, Entry


def go_entry(**overrides):
    values = dict(
        key="go", word="go", count=2, first_start=65.0, first_source="a.srt",
        example="I went & left <now>.", meaning="가다",
    )
    values.update(overrides)
    return Entry(**values)


class FormatTests(unittest.TestCase):
    def test_fmt_time(self):
        self.assertEqual(fmt_time(0), "00:00:00")
        self.assertEqual(fmt_time(65.9), "00:01:05")
        self.assertEqual(fmt_time(3723), "01:02:03")


class HighlightTests(unittest.TestCase):
    def test_bolds_inflected_forms_in_markdown_style(self):
        entry = go_entry(example="We went home and ate apples.")
        self.assertEqual(highlight_entry(entry, entry.example, "**", "**"), "We **went** home and ate apples.")

    def test_bolds_names_exactly(self):
        entry = Entry(key="sarah", word="Sarah", is_name=True, example="I went with Sarah.")
        self.assertEqual(highlight_entry(entry, entry.example, "**", "**"), "I went with **Sarah**.")


class AnkiTsvTests(unittest.TestCase):
    def test_header_and_row_layout(self):
        text = render_anki_tsv([go_entry()])
        lines = text.splitlines()
        self.assertEqual(lines[0], "#separator:tab")
        self.assertEqual(lines[1], "#html:true")
        fields = lines[2].split("\t")
        self.assertEqual(len(fields), 4)
        self.assertEqual(fields[0], "go")
        self.assertEqual(fields[1], "가다")
        self.assertEqual(fields[3], "00:01:05")

    def test_example_is_bold_and_html_escaped(self):
        row = render_anki_tsv([go_entry()]).splitlines()[2].split("\t")
        self.assertEqual(row[2], "I <b>went</b> &amp; left &lt;now&gt;.")

    def test_tabs_and_newlines_in_fields_are_flattened(self):
        row = render_anki_tsv([go_entry(meaning="가다\t나다\n")]).splitlines()[2].split("\t")
        self.assertEqual(len(row), 4)
        self.assertEqual(row[1], "가다 나다")

    def test_empty_meaning_keeps_column(self):
        row = render_anki_tsv([go_entry(meaning="")]).splitlines()[2].split("\t")
        self.assertEqual(len(row), 4)
        self.assertEqual(row[1], "")


class MarkdownTests(unittest.TestCase):
    def test_table_rows_in_given_order_with_pipes_escaped(self):
        first = go_entry(example="a | b went", meaning="가다")
        second = Entry(key="appl", word="apples", count=1, first_start=3.0, example="apples")
        analysis = Analysis(words=[first, second], names=[], caption_count=4)
        text = render_markdown(analysis, [first, second], ["a.srt"], "2026-01-01 00:00")
        self.assertIn("| 1 | go | 가다 | 2 | 00:01:05 | a \\| b **went** |", text)
        self.assertIn("| 2 | apples |  | 1 | 00:00:03 | **apples** |", text)
        self.assertLess(text.index("| 1 | go"), text.index("| 2 | apple"))
        self.assertIn("## 고유명사 후보", text)
        self.assertIn("자막 줄 4개", text)

    def test_names_section_lists_names(self):
        name = Entry(key="sarah", word="Sarah", is_name=True, count=2, first_start=1.0,
                     example="I went with Sarah.")
        analysis = Analysis(words=[], names=[name], caption_count=1)
        text = render_markdown(analysis, [], ["a.srt"], "now")
        self.assertIn("| Sarah | 2 | 00:00:01 | I went with **Sarah**. |", text)


class WriteOutputsTests(unittest.TestCase):
    def test_writes_both_files(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        out = os.path.join(tmp, "nested", "out")
        analysis = Analysis(words=[go_entry()], names=[], caption_count=1)
        md_path, tsv_path = write_outputs(out, analysis, [go_entry()], ["a.srt"], "now")
        self.assertTrue(os.path.isfile(md_path))
        self.assertTrue(os.path.isfile(tsv_path))
        with open(tsv_path, encoding="utf-8") as fh:
            self.assertTrue(fh.readline().startswith("#separator:tab"))


if __name__ == "__main__":
    unittest.main()
