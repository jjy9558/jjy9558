# -*- coding: utf-8 -*-
import datetime as dt
import os
import unittest

import samples as S
from helpers import NOW, TmpCase, read_bytes
from ics_check import parse_ics, prop, props, unescape
from paperbutler import ics as icsmod


class TestICSPrimitives(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(icsmod.escape_text("a,b;c\\d\ne"), "a\\,b\\;c\\\\d\\ne")
        self.assertEqual(unescape(icsmod.escape_text("a,b;c\\d\ne")), "a,b;c\\d\ne")

    def test_fold_ascii_and_korean(self):
        for line in ("DESCRIPTION:" + "x" * 200, "SUMMARY:" + "가나다라마바사" * 30, "A:" + "가" * 24):
            folded = icsmod.fold_line(line)
            parts = folded.split("\r\n")
            for p in parts:
                self.assertLessEqual(len(p.encode("utf-8")), 75)
                p.encode("utf-8").decode("utf-8")      # 문자 중간에서 잘리지 않음
            self.assertTrue(all(p.startswith(" ") for p in parts[1:]))
            self.assertEqual(parts[0] + "".join(p[1:] for p in parts[1:]), line)

    def test_short_line_not_folded(self):
        self.assertEqual(icsmod.fold_line("SUMMARY:짧음"), "SUMMARY:짧음")

    def test_utc_conversion(self):
        self.assertEqual(icsmod.fmt_utc(dt.datetime(2026, 11, 25, 9, 0)), "20261125T000000Z")   # KST 가정
        aware = dt.datetime(2026, 11, 25, 9, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(icsmod.fmt_utc(aware), "20261125T090000Z")

    def test_calendar_roundtrip_date_and_datetime(self):
        evs = [
            icsmod.VEvent("a@x", "납부, 마감; 확인\\끝\n둘째줄", dt.date(2026, 11, 25), "설명, 하나;둘\n셋"),
            icsmod.VEvent("b@x", "회의", dt.datetime(2026, 11, 25, 9, 30), alarm_days_before=None),
            icsmod.VEvent("c@x", "긴 제목 " * 30, dt.date(2026, 12, 1), "긴 설명 " * 60),
        ]
        data = icsmod.build_calendar(evs, now=NOW).encode("utf-8")
        cal = parse_ics(data)
        vevents = [c for c in cal["children"] if c["name"] == "VEVENT"]
        self.assertEqual(len(vevents), 3)
        a, b, c = vevents
        self.assertEqual(unescape(prop(a, "SUMMARY")[1]), "납부, 마감; 확인\\끝\n둘째줄")
        self.assertEqual(unescape(prop(a, "DESCRIPTION")[1]), "설명, 하나;둘\n셋")
        self.assertEqual(prop(a, "DTSTART"), (";VALUE=DATE", "20261125"))
        self.assertEqual(prop(a, "DTEND"), (";VALUE=DATE", "20261126"))
        self.assertEqual(prop(b, "DTSTART")[1], "20261125T003000Z")
        self.assertEqual(prop(b, "DTEND")[1], "20261125T013000Z")
        self.assertEqual(b["children"], [])
        self.assertEqual(unescape(prop(c, "SUMMARY")[1]), "긴 제목 " * 30)
        alarm = a["children"][0]
        self.assertEqual(prop(alarm, "TRIGGER")[1], "-P3D")
        self.assertEqual(prop(alarm, "ACTION")[1], "DISPLAY")
        self.assertEqual(prop(a, "DTSTAMP")[1], "20261102T070000Z")

    def test_uid_sanitized(self):
        data = icsmod.build_calendar([icsmod.VEvent("가 나/다@x", "t", dt.date(2026, 1, 1))], now=NOW)
        cal = parse_ics(data)
        uid = prop(cal["children"][0], "UID")[1]
        self.assertNotIn(" ", uid)
        self.assertNotIn("/", uid)


class TestICSFromButler(TmpCase):
    def fill(self, b):
        for t in (S.ELECTRIC, S.GAS, S.SMS_SAMSUNG, S.AD_FLYER):
            b.add_text(t, "t.txt")

    def test_due_dates_become_events(self):
        b = self.butler()
        self.fill(b)
        out = os.path.join(self.tmp, "o.ics")
        n = b.write_ics(out)
        self.assertEqual(n, 2)                           # 결제문자/광고는 일정이 아님
        cal = parse_ics(read_bytes(out))
        evs = [c for c in cal["children"] if c["name"] == "VEVENT"]
        titles = sorted(unescape(prop(e, "SUMMARY")[1]) for e in evs)
        self.assertEqual(titles, ["[납부] 서울도시가스 가스요금 38,900원", "[자동이체] 한국전력공사 전기요금 45,230원"])
        starts = sorted(prop(e, "DTSTART")[1] for e in evs)
        self.assertEqual(starts, ["20261120", "20261125"])
        for e in evs:
            self.assertEqual(prop(e["children"][0], "TRIGGER")[1], "-P3D")
        raw = read_bytes(out).decode("utf-8")
        self.assertNotIn("0123-4567-89", raw)            # 고객번호는 마스킹
        self.assertNotIn("7001234567", raw)

    def test_description_has_todo(self):
        b = self.butler()
        b.add_text(S.GAS, "t.txt")
        cal = parse_ics(b.build_ics()[0])
        desc = unescape(prop(cal["children"][0], "DESCRIPTION")[1])
        self.assertIn("할 일: 11/20까지 38,900원 납부", desc)
        self.assertIn("\n", desc)

    def test_stable_uid_and_idempotent_content(self):
        b = self.butler()
        b.add_text(S.GAS, "t.txt")
        a = parse_ics(b.build_ics()[0])
        c = parse_ics(b.build_ics()[0])
        self.assertEqual(prop(a["children"][0], "UID"), prop(c["children"][0], "UID"))

    def test_done_excluded_unless_requested(self):
        b = self.butler()
        r = b.add_text(S.GAS, "t.txt")[0]
        b.mark_done(r.doc_id)
        self.assertEqual(b.build_ics()[1], 0)
        self.assertEqual(b.build_ics(include_done=True)[1], 1)

    def test_days_window(self):
        b = self.butler()
        self.fill(b)
        self.assertEqual(b.build_ics(days=20)[1], 1)       # 11/20 만
        self.assertEqual(b.build_ics(days=30)[1], 2)
        self.assertEqual(b.build_ics(days=5)[1], 0)

    def test_empty_calendar_valid(self):
        b = self.butler()
        cal = parse_ics(b.build_ics()[0])
        self.assertEqual(cal["children"], [])

    def test_refund_events(self):
        b = self.butler()
        b.add_text(S.RECEIPT_ONLINE, "r.txt")
        cal = parse_ics(b.build_ics()[0])
        evs = [c for c in cal["children"] if c["name"] == "VEVENT"]
        self.assertEqual(len(evs), 1)
        self.assertTrue(unescape(prop(evs[0], "SUMMARY")[1]).startswith("[환불기한] 쿠팡"))
        self.assertEqual(prop(evs[0], "DTSTART")[1], "20261109")
        self.assertIn("법률 자문이 아닙니다", unescape(prop(evs[0], "DESCRIPTION")[1]))
        self.assertEqual(b.build_ics(include_refunds=False)[1], 0)


if __name__ == "__main__":
    unittest.main()
