import contextlib
import io
import json
import os
import tempfile
import unittest

from subpipe import cli, ledger, power, quote, srt
from subpipe.config import DEFAULT_DISCLOSURE
from subpipe.qa import Limits, lint
from subpipe.qareport import render_md

BROKEN = """1
00:00:00,000 --> 00:00:02,000
정상 자막입니다

3
00:00:01,500 --> 00:00:03,000
번호가 건너뛰고 앞과 겹침

4
00:00:04,000 --> 00:00:05,000
이 줄은 한 줄 길이가 스물두 글자를 훨씬 넘기도록 길게 쓴 자막입니다

5
00:00:06,000 --> 00:00:06,500
빠른 자막입니다 정말 매우 빠르게 지나갑니다

6
00:00:08,000 --> 00:00:09,000
 
"""

GPU_LOG = """timestamp, power.draw [W]
2025/01/01 10:00:00.000, 100.00 W
2025/01/01 10:00:10.000, 200.00 W
2025/01/01 10:00:20.000, [N/A]
2025/01/01 10:00:30.000, 200.00 W
2025/01/01 11:00:00.000, 200.00 W
"""


def read(path, encoding="utf-8"):
    with open(path, encoding=encoding) as f:
        return f.read()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class TmpCase(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name

    def path(self, name, text=None):
        p = os.path.join(self.d, name)
        if text is not None:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        return p


class QAReportTests(TmpCase):
    def test_broken_srt_caught_per_item(self):
        cues = srt.parse_cues(BROKEN)
        md = render_md("x.srt", cues, Limits(cps=14, max_chars=22))
        codes = {i.code for i in lint(cues, Limits(cps=14, max_chars=22))}
        for c in ("numbering", "overlap", "line_length", "cps", "empty"):
            self.assertIn(c, codes)
        for label in ("번호 연속성", "겹침", "줄 길이", "초당 글자 수(CPS)", "빈 큐"):
            row = [l for l in md.splitlines() if l.startswith("| %s |" % label)][0]
            self.assertNotEqual(row, "| %s | 0 |" % label)

    def test_qa_default_output_unchanged_and_md_option(self):
        p = self.path("b.srt", BROKEN)
        rc, out, _ = run(["qa", p])
        self.assertEqual(rc, 1)
        self.assertNotIn("QA 리포트", out)
        md = self.path("r.md")
        rc, out2, _ = run(["qa", p, "--md", md])
        self.assertEqual(out2.replace("QA 리포트: %s\n" % md, ""), out)
        self.assertIn("# QA 리포트", read(md))


class LedgerTests(TmpCase):
    def test_hourly(self):
        lp = self.path("l.json")
        ledger.start(lp, "A", media_minutes=10, quote=50000, est_hours=2, at="2025-01-01T10:00:00")
        j = ledger.stop(lp, at="2025-01-01T12:30:00")
        s = ledger.stats(j)
        self.assertAlmostEqual(s["hours"], 2.5)
        self.assertAlmostEqual(s["hourly"], 20000)
        self.assertAlmostEqual(s["vs_est"], 1.25)
        self.assertAlmostEqual(s["work_per_media_min"], 15.0)

    def test_cli_report_and_errors(self):
        lp = self.path("l.json")
        self.assertEqual(run(["ledger", "stop", "--ledger", lp])[0], 1)
        run(["ledger", "start", "A", "--ledger", lp, "--minutes", "10", "--quote", "30000", "--at", "2025-01-01T09:00:00"])
        self.assertEqual(run(["ledger", "start", "B", "--ledger", lp])[0], 1)  # one at a time
        run(["ledger", "stop", "--ledger", lp, "--at", "2025-01-01T11:00:00"])
        rc, out, _ = run(["ledger", "report", "--ledger", lp, "--target-hourly", "20000"])
        self.assertEqual(rc, 0)
        self.assertIn("15,000", out)
        self.assertIn("75%", out)


class QuoteTests(TmpCase):
    def test_rounding_rules(self):
        # half-up, not banker's: 2.5 -> 3, 0.5 -> 1
        self.assertEqual(quote.round_amount(2.5), 3)
        self.assertEqual(quote.round_amount(12250, 100), 12300)
        self.assertEqual(quote.round_amount(12249, 100), 12200)
        self.assertEqual(quote.round_amount(12201, 100, "up"), 12300)
        self.assertEqual(quote.round_amount(12299, 100, "down"), 12200)
        # 601s -> 11 billable min (ceil); 5000 * 1.25 * 11 = 68750 -> unit 100 half-up 68800
        q = quote.compute(601, 5000, 1.25, "ceil", 100)
        self.assertEqual(q["billable_minutes"], 11)
        self.assertEqual(q["amount"], 68800)
        self.assertEqual(quote.compute(601, 5000, 1.0)["amount"], 55000)

    def test_cli_md(self):
        md = self.path("q.md")
        rc, _, _ = run(["quote", "x", "--minutes", "10.01", "--rate", "5000", "--review-multiplier", "1.5", "--md", md])
        self.assertEqual(rc, 0)
        self.assertIn("82,500원", read(md))


class DeliverTests(TmpCase):
    CLEAN = "1\n00:00:00,000 --> 00:00:02,000\n안녕하세요\n"

    def test_does_not_overwrite_source(self):
        src = self.path("a.srt", self.CLEAN)
        rc, _, err = run(["deliver", src, "-o", self.d, "--force"])  # same folder, same name
        self.assertEqual(rc, 1)
        self.assertEqual(read(src), self.CLEAN)

    def test_deliver_files_and_editable_notice(self):
        src = self.path("a.srt", self.CLEAN)
        out = os.path.join(self.d, "out")
        cfg = self.path("c.json", json.dumps({"disclosure": "커스텀 고지\n한 줄"}, ensure_ascii=False))
        rc, _, _ = run(["deliver", src, "-o", out, "--config", cfg])
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(os.listdir(out)), ["a.notice.txt", "a.qa.md", "a.srt"])
        self.assertEqual(read(os.path.join(out, "a.notice.txt")), "커스텀 고지 한 줄\n")
        self.assertIn("커스텀 고지 한 줄", read(os.path.join(out, "a.qa.md")))
        self.assertEqual(run(["deliver", src, "-o", out])[0], 1)  # existing delivery protected
        self.assertEqual(run(["deliver", src, "-o", out, "--force"])[0], 0)

    def test_default_disclosure_and_qa_errors_block(self):
        src = self.path("a.srt", self.CLEAN)
        out = os.path.join(self.d, "o")
        run(["deliver", src, "-o", out])
        self.assertEqual(read(os.path.join(out, "a.notice.txt")), DEFAULT_DISCLOSURE + "\n")
        bad = self.path("b.srt", BROKEN)
        out2 = os.path.join(self.d, "o2")
        self.assertEqual(run(["deliver", bad, "-o", out2])[0], 1)
        self.assertFalse(os.path.exists(os.path.join(out2, "b.srt")))


class PowerTests(TmpCase):
    def test_parse_and_energy(self):
        s, skipped = power.parse_log(GPU_LOG)
        self.assertEqual((len(s), skipped), (4, 1))  # [N/A] row skipped
        kwh, secs = power.energy_kwh(s, max_gap_s=60)
        # 0->10s avg 150W = 1500 J; 10->30s (N/A row skipped) 200W = 4000 J; the 1h gap is excluded
        self.assertAlmostEqual(kwh, 5500 / 3.6e6)
        self.assertEqual(secs, 30)

    def test_empty_and_header_cases(self):
        self.assertEqual(power.parse_log(""), ([], 0))
        self.assertEqual(power.parse_log("timestamp, power.draw [W]\n"), ([], 0))
        s, _ = power.parse_log("2025/01/01 10:00:00.000, 50.0 W\n2025/01/01 10:00:01.000, 50.0 W\n")  # no header
        self.assertEqual(len(s), 2)
        s, _ = power.parse_log("power.draw [W], timestamp\n80 W, 2025/01/01 10:00:00\n")  # reordered
        self.assertEqual(len(s), 1)
        s, skipped = power.parse_log("timestamp, power.draw [W]\ngarbage\n")
        self.assertEqual((s, skipped), ([], 1))

    def test_tiers(self):
        t = power.parse_tiers("100=10,200=20,inf=30")
        self.assertAlmostEqual(power.tiered_cost(150, t), 100 * 10 + 50 * 20)
        self.assertAlmostEqual(power.tiered_cost(250, t), 1000 + 2000 + 50 * 30)
        self.assertAlmostEqual(power.job_cost(20, t, base_kwh=90), 10 * 10 + 10 * 20)  # crosses tier
        with self.assertRaises(power.PowerError):
            power.parse_tiers("100=10")  # no unbounded last tier
        with self.assertRaises(power.PowerError):
            power.parse_tiers("200=10,100=20,inf=30")

    def test_cli(self):
        empty = self.path("e.csv", "")
        rc, _, err = run(["power", empty])
        self.assertEqual(rc, 1)
        self.assertIn("2개 미만", err)
        log = self.path("g.csv", "timestamp, power.draw [W]\n2025/01/01 10:00:00.000, 360 W\n2025/01/01 10:00:10.000, 360 W\n")
        rc, out, _ = run(["power", log, "--tiers", "inf=1000"])
        self.assertEqual(rc, 0)
        self.assertIn("0.0010 kWh", out)
        self.assertIn("1.0원", out)
        self.assertNotIn("예시용", out)
        rc, out, _ = run(["power", log])  # placeholder tiers are labeled
        self.assertIn("예시용", out)
        cfg = self.path("c.json", json.dumps({"power": {"tiers": [{"upto_kwh": None, "rate": 500}]}}))
        rc, out, _ = run(["power", log, "--config", cfg])
        self.assertNotIn("예시용", out)


if __name__ == "__main__":
    unittest.main()
