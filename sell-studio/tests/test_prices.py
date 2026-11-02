import unittest

from sellstudio import prices


class ParseTests(unittest.TestCase):
    def test_units(self):
        cases = {"15만": 150000, "150,000": 150000, "850k": 850000, "12000원": 12000, "15만 5천": 155000,
                 "3천원": 3000, "1.5만": 15000, "28만원": 280000}
        for text, want in cases.items():
            self.assertEqual(prices.parse_won(text), want, text)
        self.assertIsNone(prices.parse_won("가격미정"))

    def test_lines(self):
        text = "RTX 3060 12GB 28만원\nRTX 3060 12GB 25만 5천\n270,000원\n# 주석\nRTX 3060 12GB\ni5-12400\n310000\n"
        values, skipped = prices.parse_paste(text)
        self.assertEqual(values, [280000, 255000, 270000, 310000])
        self.assertEqual(skipped, ["RTX 3060 12GB", "i5-12400"])  # 모델 번호를 가격으로 읽지 않는다

    def test_year_is_not_price(self):
        self.assertEqual(prices.parse_paste("2023")[0], [])


class SummaryTests(unittest.TestCase):
    def test_median_odd_even(self):
        s = prices.summarize("20만\n30만\n25만\n")
        self.assertEqual((s.low, s.median, s.high, s.count), (200000, 250000, 300000, 3))
        s = prices.summarize("20만\n30만\n25만\n27만\n")
        self.assertEqual(s.median, 260000)

    def test_few_samples_warn(self):
        s = prices.summarize("20만\n30만")
        self.assertTrue(any("2건" in w for w in s.warnings))

    def test_outlier_warn(self):
        s = prices.summarize("20만\n21만\n22만\n23만\n1만")
        self.assertTrue(any("크게 벗어난" in w for w in s.warnings))

    def test_empty(self):
        s = prices.summarize("가격 없음\n")
        self.assertEqual(s.count, 0)
        self.assertTrue(s.warnings)


if __name__ == "__main__":
    unittest.main()
