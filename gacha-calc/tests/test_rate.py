import tempfile
import unittest
from pathlib import Path

from gachacalc.rate import build_payslip, load_playtime_csv, parse_playtime_csv, wage_comment
from gachacalc.validation import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "playtime_sample.csv"


class PayslipTest(unittest.TestCase):
    def test_sample_ranking_is_by_hourly_descending(self):
        slip = build_payslip(load_playtime_csv(SAMPLE))
        self.assertEqual(
            [r.name for r in slip.rows],
            ["예시 가챠 D", "예시 RPG A", "예시 MMO B", "예시 방치형 E", "예시 퍼즐 C"],
        )
        self.assertEqual([r.rank for r in slip.rows], [1, 2, 3, 4, 5])
        self.assertAlmostEqual(slip.rows[0].hourly, 6000.0)
        self.assertAlmostEqual(slip.rows[-1].hourly, 0.0)

    def test_totals(self):
        slip = build_payslip(load_playtime_csv(SAMPLE))
        self.assertAlmostEqual(slip.total_hours, 120.5 + 300 + 15 + 80 + 400)
        self.assertAlmostEqual(slip.total_spent, 250000 + 90000 + 0 + 480000 + 12000)

    def test_equal_hourly_values_share_a_rank(self):
        records = parse_playtime_csv("게임,시간,지출\nA,10,1000\nB,20,2000\nC,5,100\n")
        slip = build_payslip(records)
        self.assertEqual([r.rank for r in slip.rows], [1, 1, 3])

    def test_duplicate_games_are_summed(self):
        records = parse_playtime_csv("게임,시간,지출\nA,10,1000\nA,10,3000\n")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].hours, 20)
        self.assertEqual(records[0].spent_krw, 4000)

    def test_english_headers_and_unit_suffixes(self):
        records = parse_playtime_csv("game,hours,spent\nX,2h,\"1,200원\"\n")
        self.assertEqual((records[0].name, records[0].hours, records[0].spent_krw), ("X", 2, 1200))

    def test_cp949_file_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "play.csv"
            path.write_bytes("게임,시간,지출\n테스트,2,1000\n".encode("cp949"))
            records = load_playtime_csv(path)
        self.assertEqual(records[0].name, "테스트")


class ValidationTest(unittest.TestCase):
    def test_zero_hours_reports_line_number(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_playtime_csv("게임,시간,지출\nA,10,1000\nB,0,1000\n")
        self.assertIn("3행", str(ctx.exception))

    def test_missing_column(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_playtime_csv("게임,지출\nA,1000\n")
        self.assertIn("플레이 시간", str(ctx.exception))

    def test_negative_spend_rejected(self):
        with self.assertRaises(ValidationError):
            parse_playtime_csv("게임,시간,지출\nA,10,-5\n")

    def test_non_numeric_hours_rejected(self):
        with self.assertRaises(ValidationError):
            parse_playtime_csv("게임,시간,지출\nA,many,1000\n")

    def test_empty_inputs_rejected(self):
        with self.assertRaises(ValidationError):
            parse_playtime_csv("")
        with self.assertRaises(ValidationError):
            parse_playtime_csv("게임,시간,지출\n")
        with self.assertRaises(ValidationError):
            build_payslip([])

    def test_missing_file(self):
        with self.assertRaises(ValidationError):
            load_playtime_csv(ROOT / "examples" / "does_not_exist.csv")


class CommentTest(unittest.TestCase):
    def test_low_wage_comment(self):
        self.assertTrue(wage_comment(312).startswith("시급 312원짜리 알바"))

    def test_zero_wage_comment(self):
        self.assertIn("열정 페이", wage_comment(0))


if __name__ == "__main__":
    unittest.main()
