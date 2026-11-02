import unittest

from jjkit.textwidth import char_width, md_table, pad, table, truncate, width


class WidthTests(unittest.TestCase):
    def test_width(self):
        self.assertEqual(width("abc"), 3)
        self.assertEqual(width("한글"), 4)
        self.assertEqual(width("한a글"), 5)
        self.assertEqual(width("ＡＢ"), 4)  # 전각 영문
        self.assertEqual(width(123), 3)

    def test_zero_width(self):
        self.assertEqual(width("é"), 1)  # 결합 악센트
        self.assertEqual(width("a​b"), 2)
        self.assertEqual(char_width("️"), 0)

    def test_ambiguous(self):
        self.assertEqual(width("①"), 1)
        self.assertEqual(width("①", ambiguous=2), 2)

    def test_pad(self):
        self.assertEqual(pad("한", 4), "한  ")
        self.assertEqual(pad("한", 4, "right"), "  한")
        self.assertEqual(pad("a", 4, "center"), " a  ")
        self.assertEqual(pad("길다란", 2), "길다란")  # 자르지 않음

    def test_truncate(self):
        self.assertEqual(truncate("가나다라", 5), "가나…")
        self.assertEqual(width(truncate("가나다라", 5)), 5)
        self.assertEqual(truncate("abc", 5), "abc")
        self.assertEqual(truncate("가나다", 1), "")
        self.assertEqual(truncate("abcdef", 4, ellipsis="..."), "a...")


class TableTests(unittest.TestCase):
    def test_alignment(self):
        lines = table([["사이버펑크", "39,600"], ["Hades", "20,000"]], ["게임", "가격"], right_cols=[1])
        self.assertEqual(lines[0], "게임          가격")
        self.assertEqual(lines[1], "----------  ------")
        self.assertEqual(lines[2], "사이버펑크  39,600")
        self.assertEqual(lines[3], "Hades       20,000")
        ws = {width(l) for l in lines}
        self.assertEqual(len(ws), 1)

    def test_no_header_ragged_none(self):
        lines = table([["a", None], ["bb"]])
        self.assertEqual(lines, ["a", "bb"])

    def test_max_width(self):
        lines = table([["아주아주긴이름", "1"]], ["이름", "n"], max_width={0: 6})
        self.assertEqual(lines[2], "아주…  1")

    def test_md_table(self):
        self.assertEqual(md_table([["a|b", 1]], ["x", "y"]), ["| x | y |", "|---|---|", "| a\\|b | 1 |"])


if __name__ == "__main__":
    unittest.main()
