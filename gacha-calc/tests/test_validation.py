import unittest
from pathlib import Path

from gachacalc.banner import RULE_FIFTY_FIFTY, Banner
from gachacalc.pity import pity_distribution
from gachacalc.validation import ValidationError, parse_number, parse_text

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples" / "banners").glob("*.json"))


def valid_kwargs(**overrides):
    data = dict(name="배너", base_rate_pct=0.6, soft_pity_start=74,
                soft_pity_increase_pct=6.0, hard_pity=90, cost_per_pull=160)
    data.update(overrides)
    return data


class ParseNumberTest(unittest.TestCase):
    def test_accepts_numbers_and_comma_strings(self):
        self.assertEqual(parse_number("1,234", "x"), 1234.0)
        self.assertEqual(parse_number(" 2.5 ", "x"), 2.5)
        self.assertEqual(parse_number(7, "x", integer=True), 7)

    def test_rejects_bad_inputs(self):
        bad = [True, "abc", "", "nan", "inf", None, [1]]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_number(value, "x")

    def test_integer_flag(self):
        with self.assertRaises(ValidationError):
            parse_number("1.5", "x", integer=True)

    def test_bounds(self):
        with self.assertRaises(ValidationError):
            parse_number("-1", "x", minimum=0)
        with self.assertRaises(ValidationError):
            parse_number("0", "x", minimum=0, exclusive_minimum=True)
        with self.assertRaises(ValidationError):
            parse_number("101", "x", maximum=100)

    def test_parse_text(self):
        self.assertEqual(parse_text("  이름 ", "x"), "이름")
        with self.assertRaises(ValidationError):
            parse_text("   ", "x")


class BannerValidationTest(unittest.TestCase):
    def test_valid_banner(self):
        banner = Banner(**valid_kwargs())
        self.assertEqual(banner.rule, RULE_FIFTY_FIFTY)
        self.assertAlmostEqual(banner.base_rate, 0.006)
        self.assertEqual(banner.featured_share, 0.5)

    def test_rejects_unknown_rule(self):
        with self.assertRaises(ValidationError):
            Banner(**valid_kwargs(rule="coin"))

    def test_rejects_soft_pity_after_hard_pity(self):
        with self.assertRaises(ValidationError):
            Banner(**valid_kwargs(soft_pity_start=91))

    def test_rejects_zero_base_rate_and_zero_cost(self):
        with self.assertRaises(ValidationError):
            Banner(**valid_kwargs(base_rate_pct=0))
        with self.assertRaises(ValidationError):
            Banner(**valid_kwargs(cost_per_pull=0))

    def test_rejects_base_rate_above_100(self):
        with self.assertRaises(ValidationError):
            Banner(**valid_kwargs(base_rate_pct=100.5))

    def test_from_dict_missing_and_unknown_keys(self):
        data = valid_kwargs()
        del data["hard_pity"]
        with self.assertRaises(ValidationError) as ctx:
            Banner.from_dict(data)
        self.assertIn("hard_pity", str(ctx.exception))
        with self.assertRaises(ValidationError):
            Banner.from_dict(dict(valid_kwargs(), hardpity=3))

    def test_from_dict_rejects_non_object(self):
        with self.assertRaises(ValidationError):
            Banner.from_dict([1, 2, 3])

    def test_load_missing_file(self):
        with self.assertRaises(ValidationError):
            Banner.load(ROOT / "examples" / "nope.json")

    def test_carried_pity_count_is_validated_by_distribution(self):
        banner = Banner(**valid_kwargs())
        with self.assertRaises(ValidationError):
            pity_distribution(banner, pity_count=90)


class ExampleFilesTest(unittest.TestCase):
    def test_three_examples_are_labeled_and_load(self):
        self.assertEqual(len(EXAMPLES), 3)
        for path in EXAMPLES:
            with self.subTest(path=path.name):
                banner = Banner.load(path)
                self.assertTrue(banner.label.startswith("EXAMPLE"))
                self.assertTrue(banner.name.startswith("[예시"))


if __name__ == "__main__":
    unittest.main()
