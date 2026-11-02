import csv
import datetime as dt
import io
import os
import re
import tempfile
import unittest

import helpers  # noqa: F401
from soundwatch import config, report
from soundwatch.store import Store

KST = dt.timezone(dt.timedelta(hours=9))


def ts(y, mo, d, h=0, mi=0, s=0):
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=KST).timestamp()


def add(st, when, dur=2.0, peak=-30.0, kind="impact", clip=None):
    return st.add_event(when, when + dur, peak, peak - 6, kind, 1, clip)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config("/nonexistent")
        self.st = Store(":memory:")
        self.addCleanup(self.st.close)

    def data(self, a=dt.date(2026, 10, 26), b=dt.date(2026, 11, 2), cfg=None):
        return report.build_report_data(self.st, cfg or self.cfg, a, b, now=ts(2026, 11, 2, 12))

    def test_night_boundaries_22_to_06(self):
        add(self.st, ts(2026, 10, 31, 21, 59, 59))    # 주간
        add(self.st, ts(2026, 10, 31, 22, 0, 0))      # 야간
        add(self.st, ts(2026, 11, 1, 5, 59, 59))      # 야간
        add(self.st, ts(2026, 11, 1, 6, 0, 0))        # 주간
        add(self.st, ts(2026, 11, 1, 0, 0, 0))        # 야간 (자정)
        d = self.data()
        self.assertEqual(d["total_events"], 5)
        self.assertEqual(d["total_night"], 3)
        by_date = {r["date"]: r for r in d["days"]}
        self.assertEqual((by_date[dt.date(2026, 10, 31)]["events"], by_date[dt.date(2026, 10, 31)]["night"]), (2, 1))
        self.assertEqual((by_date[dt.date(2026, 11, 1)]["events"], by_date[dt.date(2026, 11, 1)]["night"]), (3, 2))
        self.assertEqual(d["hourly"][22], 1)
        self.assertEqual(d["hourly"][21], 1)
        self.assertEqual(d["hourly"][5], 1)

    def test_helper_is_night_minute(self):
        f = report.is_night_minute
        self.assertTrue(f(22 * 60, 1320, 360))
        self.assertTrue(f(0, 1320, 360))
        self.assertTrue(f(359, 1320, 360))
        self.assertFalse(f(360, 1320, 360))
        self.assertFalse(f(1319, 1320, 360))
        self.assertTrue(f(13 * 60, 12 * 60, 14 * 60))   # 자정을 안 넘는 구간

    def test_custom_night_window(self):
        c = config.load_config("/nonexistent")
        c["night"] = {"start": "23:00", "end": "07:00"}
        add(self.st, ts(2026, 11, 1, 22, 30))
        add(self.st, ts(2026, 11, 1, 6, 30))
        d = self.data(cfg=c)
        self.assertEqual(d["total_night"], 1)

    def test_local_day_uses_configured_timezone(self):
        # UTC 로는 10-31 이지만 KST 로는 11-01 00:30
        add(self.st, ts(2026, 11, 1, 0, 30))
        d = self.data()
        row = [r for r in d["days"] if r["events"]][0]
        self.assertEqual(row["date"], dt.date(2026, 11, 1))

    def test_daily_weekly_tables_and_aggregates(self):
        add(self.st, ts(2026, 10, 27, 23, 0), dur=3, peak=-25)
        add(self.st, ts(2026, 10, 27, 23, 30), dur=5, peak=-28)
        add(self.st, ts(2026, 11, 2, 10, 0), dur=1, peak=-35)
        for m in range(120):   # 10-27 23:00 부터 2시간 측정, 1분 Leq -40 dBFS
            self.st.add_minute(int(ts(2026, 10, 27, 23, 0)) // 60 + m, 10 ** (-4.0) * 600, 600, -35.0)
        d = self.data()
        self.assertEqual(len(d["days"]), 8)
        r = {x["date"]: x for x in d["days"]}[dt.date(2026, 10, 27)]
        self.assertEqual((r["events"], r["night"]), (2, 2))
        self.assertAlmostEqual(r["dur"], 8.0)
        cal = d["cal"]
        self.assertAlmostEqual(r["max_peak"], cal.to_db(-25.0), places=3)
        self.assertAlmostEqual(r["max_leq1"], cal.to_db(-40.0), places=2)
        self.assertAlmostEqual(r["mon_s"], 3600, delta=1)   # 23시대 1시간만 이 날짜
        nxt = {x["date"]: x for x in d["days"]}[dt.date(2026, 10, 28)]
        self.assertAlmostEqual(nxt["mon_s"], 3600, delta=1)   # 자정 이후는 다음 날
        self.assertEqual([w["label"] for w in d["weeks"]], ["2026-W44", "2026-W45"])
        self.assertEqual([w["events"] for w in d["weeks"]], [2, 1])
        self.assertEqual(d["top_peak"][0]["peak_db"], cal.to_db(-25.0))
        self.assertEqual(d["top_dur"][0]["duration"], 5.0)

    def test_calibration_offset_applied_and_labeled(self):
        add(self.st, ts(2026, 11, 1, 23, 0), peak=-30.0)
        base = self.data()["events"][0]["peak_db"]
        c = config.load_config("/nonexistent")
        config.set_calibration(c, 3.5, "소음계 X")
        d = self.data(cfg=c)
        self.assertAlmostEqual(d["events"][0]["peak_db"] - base, 3.5, places=6)
        html = report.render_html(d)
        self.assertIn("+3.5", html)
        self.assertIn("사용자 보정", html)
        self.assertIn("소음계 X", html)
        html0 = report.render_html(self.data())
        self.assertIn("미보정 상대값", html0)
        self.assertIn("보정 안 함", html0)

    def test_html_is_self_contained(self):
        for i in range(12):
            add(self.st, ts(2026, 10, 27 + i % 5, 22 + i % 2, i), peak=-30 + i)
        html = report.render_html(self.data())
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertEqual(html.count("<svg"), 4)
        for bad in ("http://", "https://", "<script", "<link", "@import", "<iframe", "src=", "url(http"):
            self.assertNotIn(bad, html, bad)
        self.assertIn("prefers-color-scheme:dark", html)
        self.assertIn("@media print", html)
        self.assertIn('name="viewport"', html)

    def test_limitations_and_official_measurement_statements(self):
        html = report.render_html(self.data())
        for must in ("교정된 소음계", "상대값", "층간소음 이웃사이센터", "공식", "참고", "최신", "확인",
                     "보정", "소음원 판별 불가", "기록 누락", "개인정보"):
            self.assertIn(must, html, must)

    def test_user_strings_are_escaped(self):
        payload = "<script>alert(1)</script>"
        add(self.st, ts(2026, 11, 1, 23, 0), kind=payload, clip='"><img src=x onerror=alert(1)>.wav')
        c = config.load_config("/nonexistent")
        config.set_calibration(c, 1.0, "<b>메모</b>&")
        html = report.render_html(self.data(cfg=c))
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<b>메모</b>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;b&gt;메모&lt;/b&gt;&amp;", html)

    def test_empty_period_renders_and_flags_missing_monitoring(self):
        html = report.render_html(self.data())
        self.assertIn("기록 없음", html)
        self.assertIn("해당 이벤트 없음", html)
        self.assertIn("0건", html)

    def test_csv_export(self):
        add(self.st, ts(2026, 11, 1, 23, 0), clip="a,b.wav")
        add(self.st, ts(2026, 11, 2, 9, 0))
        d = self.data()
        rows = list(csv.reader(io.StringIO(report.events_csv(d))))
        self.assertEqual(rows[0][:4], ["id", "start_local", "weekday", "duration_s"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][1], "2026-11-01 23:00:00")
        self.assertEqual(rows[1][2], "일")
        self.assertEqual(rows[1][7], "1")
        self.assertEqual(rows[1][9], "a,b.wav")
        self.assertEqual(rows[2][7], "0")
        self.assertIn("uncalibrated", rows[1][8])
        drows = list(csv.reader(io.StringIO(report.daily_csv(d))))
        self.assertEqual(len(drows), 1 + 8)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "e.csv")
            report.write_csv(p, report.events_csv(d))
            with open(p, "rb") as f:
                self.assertEqual(f.read(3), b"\xef\xbb\xbf")

    def test_fingerprint_changes_with_data(self):
        add(self.st, ts(2026, 11, 1, 23, 0))
        a = self.data()["fingerprint"]
        add(self.st, ts(2026, 11, 1, 23, 5))
        b = self.data()["fingerprint"]
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{16}$")

    def test_many_days_chart_is_capped(self):
        d = report.build_report_data(self.st, self.cfg, dt.date(2026, 8, 1), dt.date(2026, 11, 2), now=ts(2026, 11, 2))
        html = report.render_html(d)
        self.assertIn("<svg", html)
        self.assertGreater(len(d["weeks"]), 12)


if __name__ == "__main__":
    unittest.main()
