import datetime as dt
import os
import tempfile
import unittest

from helpers import FakeClock, FakeMono, config
from sidekick import butler

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261102T190000
SUMMARY:헬스장
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=Asia/Seoul:20261102T093000
SUMMARY:스탠드업\\, 팀
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261102
SUMMARY:엄마 생신
END:VEVENT
BEGIN:VEVENT
DTSTART:20261103T100000
SUMMARY:내일 일정
END:VEVENT
END:VCALENDAR
"""


class ButlerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ics = os.path.join(self.dir, "cal.ics")
        with open(self.ics, "w", encoding="utf-8") as f:
            f.write(ICS)
        self.now = dt.datetime(2026, 11, 2, 18, 30)

    def test_calendar_today_only_sorted(self):
        s = butler.calendar_ics({"path": self.ics}, self.now)
        self.assertEqual(s, "오늘 일정 3개: 종일 엄마 생신, 09시 30분 스탠드업, 팀, 19시 헬스장.")

    def test_unimplemented_providers_return_nothing(self):
        self.assertIsNone(butler.weather({}, self.now))
        self.assertIsNone(butler.delivery({}, self.now))
        self.assertIsNone(butler.claude_status({"path": ""}, self.now))
        self.assertIsNone(butler.calendar_ics({"path": "/nope.ics"}, self.now))

    def test_assemble_skips_empty_and_broken(self):
        status = os.path.join(self.dir, "status.txt")
        with open(status, "w", encoding="utf-8") as f:
            f.write("PR 2개 리뷰 대기\n두번째 줄")

        def boom(o, n):
            raise RuntimeError("x")
        reg = dict(butler.PROVIDERS, boom=boom)
        text = butler.assemble([{"name": "greeting"}, {"name": "boom"}, {"name": "weather"},
                                {"name": "unknown"}, {"name": "calendar_ics", "path": self.ics},
                                {"name": "claude_status", "path": status},
                                {"name": "delivery", "text": "택배 1개 도착 예정."}], self.now, reg)
        self.assertEqual(text, "수고했어요, 어서 와요. 오늘 일정 3개: 종일 엄마 생신, 09시 30분 스탠드업, 팀, 19시 헬스장. "
                               "Claude 상태: PR 2개 리뷰 대기 택배 1개 도착 예정.")

    def test_arrival_cooldown_motion_vs_button(self):
        mono = FakeMono()
        b = butler.Butler(config(butler={"providers": [{"name": "greeting", "text": "어서 와."}]})["butler"],
                          clock=FakeClock(self.now), monotonic=mono)
        self.assertEqual(b.arrive("motion")["say"], "어서 와.")
        self.assertTrue(b.arrive("motion")["cooldown"])
        self.assertTrue(b.arrive("button")["ok"])  # 버튼은 항상
        mono.t += 601
        self.assertTrue(b.arrive("motion")["ok"])


if __name__ == "__main__":
    unittest.main()
