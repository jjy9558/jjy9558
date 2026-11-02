import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from gachacalc.cli import main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_A = str(ROOT / "examples" / "banners" / "example_a.json")
SAMPLE_CSV = str(ROOT / "examples" / "playtime_sample.csv")


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class PityCommandTest(unittest.TestCase):
    def test_pity_prints_report_and_verdict(self):
        code, out, _ = run_cli([
            "pity", "--config", EXAMPLE_A, "--pity-count", "30", "--guaranteed",
            "--currency", "50,000", "--package", "6480원에 300 젬",
        ])
        self.assertEqual(code, 0)
        self.assertIn("EXAMPLE", out)
        self.assertIn("판정: 천장까지 약", out)

    def test_invalid_currency_returns_error_code(self):
        code, out, err = run_cli([
            "pity", "--config", EXAMPLE_A, "--currency", "-5", "--package", "6480원에 300 젬",
        ])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("입력 오류", err)

    def test_invalid_package_returns_error_code(self):
        code, _, err = run_cli([
            "pity", "--config", EXAMPLE_A, "--currency", "100", "--package", "6480 300",
        ])
        self.assertEqual(code, 2)
        self.assertIn("충전 패키지", err)


class RateCommandTest(unittest.TestCase):
    def test_rate_prints_payslip(self):
        code, out, _ = run_cli(["rate", SAMPLE_CSV])
        self.assertEqual(code, 0)
        self.assertIn("게임 시간 시급 명세서", out)
        self.assertIn("시급 6,000원짜리 알바", out)

    def test_rate_missing_file_returns_error_code(self):
        code, _, err = run_cli(["rate", str(ROOT / "examples" / "missing.csv")])
        self.assertEqual(code, 2)
        self.assertIn("입력 오류", err)


if __name__ == "__main__":
    unittest.main()
