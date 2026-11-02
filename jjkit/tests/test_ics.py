import datetime as dt
import unittest

from jjkit import ics
from jjkit.ics import KST, Event, build_calendar, fold_line, parse_calendar, unfold

NOW = dt.datetime(2026, 11, 2, 0, 0, tzinfo=dt.timezone.utc)


class FoldTests(unittest.TestCase):
    def test_fold_utf8_boundaries(self):
        line = "SUMMARY:" + "한글" * 40
        folded = fold_line(line)
        parts = folded.split("\r\n")
        self.assertGreater(len(parts), 1)
        for i, p in enumerate(parts):
            self.assertLessEqual(len(p.encode("utf-8")), 75)
            if i:
                self.assertTrue(p.startswith(" "))
        self.assertEqual(unfold(folded), [line])

    def test_short_line_untouched(self):
        self.assertEqual(fold_line("A:b"), "A:b")

    def test_escape_roundtrip(self):
        s = "a, b; c\\d\n둘째 줄"
        self.assertEqual(ics.unescape_text(ics.escape_text(s)), s)


class BuildTests(unittest.TestCase):
    def test_build(self):
        evs = [Event("game-1", "[패치] 게임: 새 시즌", dt.datetime(2026, 11, 3, 10, 0),
                     description="- 줄1\n- 줄2", url="https://example.com/x", categories=["패치"],
                     alarm_before=dt.timedelta(days=1)),
               Event("bill 2", "전기요금 납부", dt.date(2026, 11, 25), transparent=True)]
        text = build_calendar(evs, name="내 캘린더", now=NOW, refresh="PT6H")
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(text.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""))
        lines = unfold(text)
        self.assertIn("DTSTART:20261103T010000Z", lines)  # naive는 KST
        self.assertIn("DTEND:20261103T020000Z", lines)
        self.assertIn("DTSTART;VALUE=DATE:20261125", lines)
        self.assertIn("DTEND;VALUE=DATE:20261126", lines)
        self.assertIn("UID:bill-2", lines)
        self.assertIn("TRIGGER:-P1D", lines)
        self.assertIn("DESCRIPTION:- 줄1\\n- 줄2", lines)
        self.assertIn("X-WR-CALNAME:내 캘린더", lines)
        self.assertIn("REFRESH-INTERVAL;VALUE=DURATION:PT6H", lines)
        self.assertEqual(text.count("DTSTAMP:20261102T000000Z"), 2)

    def test_alarm_minutes(self):
        text = build_calendar([Event("a", "x", dt.datetime(2026, 1, 1, 9, tzinfo=KST),
                                     alarm_before=dt.timedelta(minutes=90))], now=NOW)
        self.assertIn("TRIGGER:-PT1H30M", text)


class ParseTests(unittest.TestCase):
    def test_roundtrip(self):
        src = [Event("u1", "회의, 중요; 진짜", dt.datetime(2026, 11, 3, 14, 30, tzinfo=KST),
                     end=dt.datetime(2026, 11, 3, 15, 0, tzinfo=KST), description="설명\n둘째",
                     location="서울", categories=["일", "회의"], alarm_before=dt.timedelta(days=3)),
               Event("u2", "종일" * 30, dt.date(2026, 12, 1))]
        got = parse_calendar(build_calendar(src, now=NOW))
        self.assertEqual(len(got), 2)
        a, b = got
        self.assertEqual(a.uid, "u1")
        self.assertEqual(a.summary, "회의, 중요; 진짜")
        self.assertEqual(a.start, src[0].start)
        self.assertEqual(a.end, src[0].end)
        self.assertEqual(a.description, "설명\n둘째")
        self.assertEqual(a.location, "서울")
        self.assertEqual(a.categories, ["일", "회의"])
        self.assertEqual(a.alarm_before, dt.timedelta(days=3))
        self.assertTrue(b.all_day)
        self.assertEqual(b.start, dt.date(2026, 12, 1))
        self.assertEqual(b.summary, "종일" * 30)

    def test_external_calendar(self):
        text = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART;TZID=Asia/Seoul:20261105T090000\n"
                "SUMMARY:아침\n  운동\nRRULE:FREQ=WEEKLY\nBEGIN:VALARM\nDESCRIPTION:알람 설명\n"
                "TRIGGER:-PT15M\nEND:VALARM\nEND:VEVENT\n"
                "BEGIN:VEVENT\nDTSTART:20261105T1000\nSUMMARY:떠 있는 시각\nEND:VEVENT\n"
                "BEGIN:VEVENT\nDTSTART:깨짐\nSUMMARY:버림\nEND:VEVENT\n"
                "BEGIN:VEVENT\nSUMMARY:시작 없음\nEND:VEVENT\nEND:VCALENDAR\n")
        got = parse_calendar(text)
        self.assertEqual([e.summary for e in got], ["아침 운동", "떠 있는 시각"])
        a = got[0]
        self.assertEqual(a.start.utcoffset(), dt.timedelta(hours=9))
        self.assertEqual(a.start.hour, 9)
        self.assertEqual(a.description, "")  # VALARM 안의 DESCRIPTION은 무시
        self.assertEqual(a.alarm_before, dt.timedelta(minutes=15))
        self.assertEqual(a.rrule, "FREQ=WEEKLY")
        self.assertEqual(got[1].start, dt.datetime(2026, 11, 5, 10, 0, tzinfo=KST))

    def test_naive_mode_and_utc(self):
        text = "BEGIN:VEVENT\r\nDTSTART:20261105T100000\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART:20261105T010000Z\r\nEND:VEVENT\r\n"
        a, b = parse_calendar(text, default_tz=None)
        self.assertIsNone(a.start.tzinfo)
        self.assertEqual(b.start.tzinfo, dt.timezone.utc)

    def test_unknown_tzid_falls_back(self):
        text = "BEGIN:VEVENT\nDTSTART;TZID=Nowhere/City:20261105T100000\nEND:VEVENT\n"
        self.assertEqual(parse_calendar(text)[0].start.tzinfo, KST)


if __name__ == "__main__":
    unittest.main()
