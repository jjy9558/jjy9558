import unittest
from datetime import date, datetime

from nagger.report import month_data, render_html, render_markdown, headline
from nagger.timeutil import fmt_duration
from tests.helpers import Base


class WorkTimeTests(Base):
    def go(self, minutes, fg, idle=1, step=1):
        for _ in range(minutes // step):
            self.plat.foreground, self.plat.idle = fg, idle
            self.step(minutes=step)

    def day(self, key):
        return self.n.work.data["days"][key]

    def test_classification_and_accounting(self):
        self.make(datetime(2026, 11, 2, 9, 0))
        self.n.tick()
        self.go(60, "Code.exe")
        self.go(30, "game.exe")
        self.go(10, "chrome.exe")
        self.go(20, "Code.exe", idle=600)          # 자리 비움
        d = self.day("2026-11-02")
        self.assertEqual(d["work"], 3600)
        self.assertEqual(d["game"], 1800)
        self.assertEqual(d["other"], 600)

    def test_clock_in_and_out_messages(self):
        self.make(datetime(2026, 11, 2, 9, 0))
        self.n.tick()
        self.go(5, "Code.exe")
        self.assertEqual(sum("출근 도장" in t for t in self.sink.texts("work")), 1)
        self.go(5, "Code.exe")
        self.assertEqual(sum("출근 도장" in t for t in self.sink.texts("work")), 1)
        self.go(29, None, idle=900)
        self.assertFalse(any("퇴근" in t for t in self.sink.texts("work")))
        self.go(2, None, idle=900)
        out = [t for t in self.sink.texts("work") if "퇴근" in t]
        self.assertEqual(len(out), 1)
        self.assertIn("순수 작업 10분", out[0])
        self.go(60, None, idle=900)
        self.assertEqual(len([t for t in self.sink.texts("work") if "퇴근" in t]), 1)
        self.assertEqual(self.day("2026-11-02")["first_in"], "09:00:00")

    def test_across_midnight_split(self):
        self.make(datetime(2026, 11, 2, 23, 40))
        self.n.tick()
        self.go(40, "Code.exe")
        a, b = self.day("2026-11-02"), self.day("2026-11-03")
        self.assertEqual(a["work"], 20 * 60)
        self.assertEqual(b["work"], 20 * 60)
        self.assertEqual(a["last_work"], "23:59:59")
        self.assertEqual(b["first_in"], "00:00:00")
        # 자정 넘어 이어진 근무는 새 날 '출근' 알림을 또 울리지 않음
        self.assertEqual(sum("출근 도장" in t for t in self.sink.texts("work")), 1)

    def test_single_tick_spanning_midnight(self):
        self.make(datetime(2026, 11, 2, 23, 59, 0))
        self.n.tick()
        self.plat.foreground, self.plat.idle = "code.exe", 1
        self.step(seconds=90)
        self.assertEqual(self.day("2026-11-02")["work"], 60)
        self.assertEqual(self.day("2026-11-03")["work"], 30)

    def test_gap_not_counted(self):
        self.make(datetime(2026, 11, 2, 9, 0))
        self.n.tick()
        self.plat.foreground, self.plat.idle = "code.exe", 1
        self.step(hours=3)       # 절전 후 복귀
        self.assertEqual(self.n.work.data["days"].get("2026-11-02", {}).get("work", 0), 0)

    def test_persists_between_runs(self):
        self.make(datetime(2026, 11, 2, 9, 0))
        self.n.tick()
        self.go(10, "code.exe")
        from nagger.worktime import WorkTracker
        w2 = WorkTracker(self.cfg, self.n.work.path)
        self.assertEqual(w2.data["days"]["2026-11-02"]["work"], 600)

    def test_platform_failures_dont_crash(self):
        self.make(datetime(2026, 11, 2, 9, 0))
        self.plat.fail = {"idle", "fg", "procs"}
        self.step(minutes=1)
        self.assertTrue(self.n.errors)


class ReportTests(unittest.TestCase):
    DAYS = {"2026-10-01": {"work": 7200, "game": 600, "first_in": "09:00:00", "last_work": "12:00:00"},
            "2026-10-03": {"work": 3600 * 110, "game": 0}}

    def test_headline_format(self):
        d = month_data({"2026-11-01": {"work": 112 * 3600}}, "2026-11", date(2026, 11, 20))
        self.assertEqual(headline(d), "이번 달 순수 작업 시간 112시간")
        self.assertEqual(fmt_duration(112 * 3600 + 30 * 60), "112시간 30분")
        d2 = month_data(self.DAYS, "2026-10", date(2026, 11, 20))
        self.assertIn("2026년 10월", headline(d2))

    def test_markdown_bars_and_empty_days(self):
        d = month_data(self.DAYS, "2026-10", date(2026, 11, 20))
        self.assertEqual(len(d["rows"]), 31)
        md = render_markdown(d)
        self.assertIn("█" * 20, md)             # 최고 일자는 꽉 찬 막대
        self.assertIn("01(목)", md)
        self.assertIn("09:00~12:00", md)
        self.assertIn("일한 날: 2일", md)

    def test_current_month_stops_today(self):
        d = month_data({}, "2026-11", date(2026, 11, 5))
        self.assertEqual(len(d["rows"]), 5)
        self.assertIn("기록이 텅 비었어요", render_markdown(d))

    def test_html_self_contained_and_escaped(self):
        d = month_data(self.DAYS, "2026-10", date(2026, 11, 20))
        h = render_html(d)
        self.assertTrue(h.startswith("<!doctype html>"))
        for bad in ("http://", "https://", "<script", "src="):
            self.assertNotIn(bad, h)
        self.assertIn("width:100.0%", h)


if __name__ == "__main__":
    unittest.main()
