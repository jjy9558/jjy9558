# -*- coding: utf-8 -*-
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime

from vjournal import contract as K
from vjournal.checkout import morning_payload
from vjournal.cli import main
from vjournal.goals import Declaration
from vjournal.redact import Redactor
from vjournal.store import Journal


def decl(title, kw, period, target=None, unit=None, deadline=None):
    return Declaration(title, kw, period, target, unit, deadline, None, title)


def seed(j, next_text="내일 눈 뜨자마자 PR 리뷰", extra_flags=(), extra_goals=()):
    """기준일 2026-11-03 (어제 = 11-02) 로 맞춘 샘플 데이터."""
    t1 = datetime(2026, 11, 1, 9, 0)
    t2 = datetime(2026, 11, 2, 21, 0)
    g = {}
    g["push"] = j.add_goal(decl("매일 푸시업 30개", ["푸시업"], "day", 30, "개"), None, t1)
    g["stretch"] = j.add_goal(decl("매일 스트레칭 10분", ["스트레칭"], "day", 10, "분"), None, t1)
    g["read"] = j.add_goal(decl("매일 독서 20쪽", ["독서"], "day", 20, "쪽"), None, t1)
    g["report"] = j.add_goal(decl("보고서 끝낸다", ["보고서"], "once", None, None, date(2026, 11, 2)), None, t1)
    g["blog"] = j.add_goal(decl("블로그 초안", ["블로그"], "once", 2, None, date(2026, 11, 10)), None, t1)
    g["drop"] = j.add_goal(decl("외국어 공부", ["외국어"], "once", None, None, date(2026, 11, 20)), None, t1)
    g["done"] = j.add_goal(decl("완료된 과제", ["과제"], "once", None, None, date(2026, 11, 5)), None, t1)
    g["old"] = j.add_goal(decl("옛날 목표", ["옛날"], "once"), None, t1)
    g["today"] = j.add_goal(decl("발표 준비", ["발표"], "once", None, None, date(2026, 11, 3)), None, t1)
    g["tomorrow"] = j.add_goal(decl("세금 신고", ["세금"], "once", None, None, date(2026, 11, 4)), None, t1)
    for title, kw, period, deadline in extra_goals:
        g[title] = j.add_goal(decl(title, kw, period, None, None, deadline), None, t1)

    j.add_progress(g["push"], 12, ts=t2)
    j.add_progress(g["stretch"], 10, ts=t2)
    j.add_progress(g["blog"], 1, ts=t2)
    j.complete_goal(g["done"], ts=t2)
    j.drop_goal(g["drop"], ts=t2)
    j.drop_goal(g["old"], ts=datetime(2026, 11, 1, 10, 0))

    j.add_entry(next_text, kind="checkout", qkey="next", ts=datetime(2026, 11, 2, 18, 0), auto_goals=False)
    j.add_entry("세금 서류 챙기기 #중요", flag=True, ts=datetime(2026, 11, 2, 18, 30), auto_goals=False)
    j.add_entry("오래된 표시", flag=True, ts=datetime(2026, 10, 10, 9, 0), auto_goals=False)
    j.add_entry("미래 표시", flag=True, ts=datetime(2026, 11, 4, 9, 0), auto_goals=False)
    for text in extra_flags:
        j.add_entry(text, flag=True, ts=datetime(2026, 11, 2, 19, 0), auto_goals=False)
    return g


class MorningJsonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)  # 나중에 등록 -> 나중에 실행 (sqlite 를 먼저 닫는다)
        self.db = os.path.join(self.tmp.name, "j.db")

    def seeded(self, **kw):
        j = Journal(self.db, now=lambda: datetime(2026, 11, 3, 9, 0))
        try:
            return seed(j, **kw)
        finally:
            j.close()

    def run_cli(self, now, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--db", self.db] + list(argv), now=lambda: now)
        return rc, buf.getvalue()

    def test_schema_and_values(self):
        g = self.seeded()
        rc, out = self.run_cli(datetime(2026, 11, 3, 9, 0), "morning", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(K.validate(data), data)
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["date"], "2026-11-03")
        self.assertEqual(data["first_task"], "내일 눈 뜨자마자 PR 리뷰")

        yesterday = {x["text"]: (x["result"], x["progress"]) for x in data["goals_yesterday"]}
        self.assertEqual(yesterday, {
            "매일 푸시업 30개": ("partial", "12/30개"),
            "매일 스트레칭 10분": ("done", "10/10분"),
            "매일 독서 20쪽": ("missed", "0/20쪽"),
            "보고서 끝낸다": ("missed", "0"),
            "블로그 초안": ("partial", "1/2"),
            "외국어 공부": ("dropped", "접음"),
            "완료된 과제": ("done", "완료"),
        })
        self.assertEqual([x["id"] for x in data["goals_yesterday"]],
                         sorted(x["id"] for x in data["goals_yesterday"]))

        self.assertEqual(data["goals_due_soon"], [
            {"id": g["today"], "text": "발표 준비", "deadline": "2026-11-03", "days_left": 0},
            {"id": g["tomorrow"], "text": "세금 신고", "deadline": "2026-11-04", "days_left": 1},
        ])
        self.assertEqual(data["flags"], [
            {"id": data["flags"][0]["id"], "text": "세금 서류 챙기기 #중요", "created": "2026-11-02T18:30:00"},
        ])

    def test_redaction_applied_to_every_text_field(self):
        self.seeded(next_text="문의 a.b@example.com 또는 010-1234-5678 확인",
                    extra_flags=("토큰 sk-abcdefghijklmnopqrstuv 를 노출했다",),
                    extra_goals=[("홈 /home/user/proj 정리", ["정리"], "once", date(2026, 11, 3))])
        rc, out = self.run_cli(datetime(2026, 11, 3, 9, 0), "morning", "--json")
        self.assertEqual(rc, 0)
        for raw in ("a.b@example.com", "010-1234-5678", "sk-abcdefghijklmnopqrstuv", "/home/user"):
            self.assertNotIn(raw, out)
        data = json.loads(out)
        self.assertEqual(data["first_task"], "문의 <이메일> 또는 <전화번호> 확인")
        self.assertTrue(any("~/proj" in x["text"] for x in data["goals_due_soon"]))
        self.assertTrue(any("<토큰>" in f["text"] for f in data["flags"]))

    def test_empty_journal(self):
        rc, out = self.run_cli(datetime(2026, 11, 3, 9, 0), "morning", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data, {"version": 1, "date": "2026-11-03", "first_task": None,
                                "goals_yesterday": [], "goals_due_soon": [], "flags": []})
        K.validate(data)

    def test_date_override(self):
        self.seeded()
        # 기본(오늘 = 11-05): 어제 11-04 기록이 없고 11-04 표시도 보인다.
        rc, out = self.run_cli(datetime(2026, 11, 5, 9, 0), "morning", "--json")
        data = json.loads(out)
        self.assertEqual(data["date"], "2026-11-05")
        self.assertIsNone(data["first_task"])
        # 11-04 에 진행 없음: 매일 목표 3개 + 11-04 가 기한인 '세금 신고'만 missed
        self.assertEqual({x["text"]: x["result"] for x in data["goals_yesterday"]}, {
            "매일 푸시업 30개": "missed", "매일 스트레칭 10분": "missed",
            "매일 독서 20쪽": "missed", "세금 신고": "missed"})
        self.assertEqual(sorted(f["text"] for f in data["flags"]), ["미래 표시", "세금 서류 챙기기 #중요"])

        # --date 11-03: 기준일 이후 표시는 빠진다.
        rc, out = self.run_cli(datetime(2026, 11, 5, 9, 0), "morning", "--json", "--date", "2026-11-03")
        data = json.loads(out)
        self.assertEqual(data["date"], "2026-11-03")
        self.assertEqual(data["first_task"], "내일 눈 뜨자마자 PR 리뷰")
        self.assertEqual([f["text"] for f in data["flags"]], ["세금 서류 챙기기 #중요"])

    def test_bad_date_is_error(self):
        self.seeded()
        rc, out = self.run_cli(datetime(2026, 11, 3, 9, 0), "morning", "--json", "--date", "2026-13-01")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_text_morning_still_works_with_date(self):
        self.seeded()
        rc, out = self.run_cli(datetime(2026, 11, 5, 9, 0), "morning", "--date", "2026-11-03")
        self.assertEqual(rc, 0)
        self.assertIn("좋은 아침 (2026-11-03)", out)
        self.assertIn("내일 눈 뜨자마자 PR 리뷰", out)

    def test_validate_rejects_bad_payloads(self):
        j = Journal(":memory:", now=lambda: datetime(2026, 11, 3, 9, 0))
        self.addCleanup(j.close)
        seed(j)
        good = morning_payload(j, date(2026, 11, 3), Redactor())
        K.validate(good)

        def broken(mutate):
            p = json.loads(json.dumps(good, ensure_ascii=False))
            mutate(p)
            with self.assertRaises(ValueError):
                K.validate(p)

        broken(lambda p: p.update(version=2))
        broken(lambda p: p.update(version=True))
        broken(lambda p: p.update(date="2026-02-30"))
        broken(lambda p: p.pop("flags"))
        broken(lambda p: p.update(extra=1))
        broken(lambda p: p["goals_yesterday"][0].update(result="bogus"))
        broken(lambda p: p["goals_yesterday"][0].update(id=True))
        broken(lambda p: p["goals_due_soon"][0].update(days_left="0"))
        broken(lambda p: p["flags"][0].update(created="어제"))
        broken(lambda p: p.update(first_task=5))


if __name__ == "__main__":
    unittest.main()
