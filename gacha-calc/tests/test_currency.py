import unittest

from gachacalc.currency import Package, fmt_krw, fmt_won, parse_package
from gachacalc.validation import ValidationError


class ParsePackageTest(unittest.TestCase):
    def test_parses_korean_package_text(self):
        pkg = parse_package("6480원에 300 젬")
        self.assertEqual(pkg.price_krw, 6480)
        self.assertEqual(pkg.units, 300)
        self.assertEqual(pkg.unit_name, "젬")

    def test_accepts_commas_and_other_particles(self):
        pkg = parse_package("1,000원으로 100크리스탈")
        self.assertEqual((pkg.price_krw, pkg.units, pkg.unit_name), (1000, 100, "크리스탈"))

    def test_rejects_malformed_text(self):
        for text in ("6480 300", "6480원에 300 젬 추가", "", "원에 300 젬"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_package(text)

    def test_rejects_zero_price_or_units(self):
        with self.assertRaises(ValidationError):
            parse_package("0원에 300 젬")
        with self.assertRaises(ValidationError):
            parse_package("6480원에 0 젬")


class ConversionTest(unittest.TestCase):
    def test_krw_per_unit_and_conversion(self):
        pkg = Package(price_krw=6480, units=300, unit_name="젬")
        self.assertAlmostEqual(pkg.krw_per_unit, 21.6)
        self.assertAlmostEqual(pkg.krw_for_units(160), 3456.0)

    def test_purchase_cost_rounds_up_to_whole_packages(self):
        pkg = Package(price_krw=6480, units=300, unit_name="젬")
        self.assertEqual(pkg.purchase_cost(0), 0)
        self.assertEqual(pkg.purchase_cost(160), 6480)
        self.assertEqual(pkg.purchase_cost(300), 6480)
        self.assertEqual(pkg.purchase_cost(301), 12960)

    def test_invalid_package_fields(self):
        with self.assertRaises(ValidationError):
            Package(price_krw=-1, units=300)
        with self.assertRaises(ValidationError):
            Package(price_krw=6480, units=1.5)


class FormatTest(unittest.TestCase):
    def test_fmt_krw(self):
        cases = {
            0: "0원",
            3456: "3,456원",
            9999: "9,999원",
            10_000: "1만원",
            34_567: "3.5만원",
            234_567: "23만원",
            100_000_000: "1억원",
            123_456_789: "1억 2,346만원",
        }
        for amount, expected in cases.items():
            with self.subTest(amount=amount):
                self.assertEqual(fmt_krw(amount), expected)

    def test_fmt_won(self):
        self.assertEqual(fmt_won(312.4), "312원")


if __name__ == "__main__":
    unittest.main()
