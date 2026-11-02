import unittest

from cool_off.text import display_width, pad, table, won


class TextTests(unittest.TestCase):
    def test_wide_characters_count_double(self):
        self.assertEqual(display_width("에어팟"), 6)
        self.assertEqual(display_width("AirPods"), 7)
        self.assertEqual(display_width("₩"), 1)

    def test_pad_by_display_width(self):
        self.assertEqual(pad("에어", 6), "에어  ")
        self.assertEqual(pad("12", 5, "r"), "   12")

    def test_table_columns_line_up(self):
        out = table(["이름", "가격"], [["에어팟 프로", won(359000)], ["Kbd", won(1000)]], ["l", "r"])
        lines = out.splitlines()
        self.assertEqual(len(lines), 4)
        price_ends = {display_width(line.rstrip()) for line in lines[2:]}
        self.assertEqual(len(price_ends), 1, out)

    def test_won_format(self):
        self.assertEqual(won(47800), "47,800원")


if __name__ == "__main__":
    unittest.main()
