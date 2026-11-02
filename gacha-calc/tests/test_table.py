import unittest

from gachacalc.table import display_width, pad, render_table


class DisplayWidthTest(unittest.TestCase):
    def test_hangul_and_hanja_are_two_columns(self):
        self.assertEqual(display_width("가"), 2)
        self.assertEqual(display_width("한글"), 4)
        self.assertEqual(display_width("가a"), 3)
        self.assertEqual(display_width("漢"), 2)

    def test_combining_marks_have_zero_width(self):
        self.assertEqual(display_width("é"), 1)

    def test_ascii_and_symbols_are_one_column(self):
        self.assertEqual(display_width("abc 123"), 7)
        self.assertEqual(display_width("—"), 1)  # em dash is ambiguous width -> narrow


class PadTest(unittest.TestCase):
    def test_right_and_left_alignment(self):
        self.assertEqual(pad("가", 4, "r"), "  가")
        self.assertEqual(pad("가", 4, "l"), "가  ")

    def test_center_alignment(self):
        self.assertEqual(pad("가", 5, "c"), " 가  ")

    def test_no_truncation_when_too_wide(self):
        self.assertEqual(pad("가나다", 2, "l"), "가나다")


class RenderTableTest(unittest.TestCase):
    def test_all_lines_have_same_display_width(self):
        table = render_table(
            ["이름", "값"],
            [["가챠 천장", "1,234"], ["abc", "12"], ["한글이 긴 항목", "9"]],
            ["l", "r"],
        )
        widths = {display_width(line) for line in table.splitlines()}
        self.assertEqual(len(widths), 1)

    def test_mismatched_row_is_rejected(self):
        with self.assertRaises(ValueError):
            render_table(["a", "b"], [["only one"]])


if __name__ == "__main__":
    unittest.main()
