"""알람 -> 아침 브리핑 흐름. 파일 계약: morning-brief/mbrief/contract.py (brief-latest.json)."""
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import FakeClock, FakeLLM, LiveServer, config
from sidekick.morning_gate import STALE_DEFAULT, MorningGate, read_brief
from sidekick.server import Sidekick

TODAY = dt.datetime(2026, 11, 2, 7, 0)
SPEAK = "오늘 할 일은 PR 리뷰예요. 밤사이 클로드가 테스트 3개를 고쳤어요. 통신비는 내일 마감이에요."
SECRET_SPEAK = "비밀브리핑내용"
APPROVE = '{"approved": true, "reason": "칫솔 확인"}'
REJECT = '{"approved": false, "reason": "이불 속"}'


def brief(date="2026-11-02", speak=SPEAK, reject="일어나면 해 드립니다"):
    """contract.validate_brief를 통과하는 형태의 최소 객체 (모르는 키는 읽는 쪽이 무시해야 함)."""
    return {"version": 1, "date": date, "speak_text": speak, "reject_text": reject,
            "sections": [], "sources": {}, "quiet_day": False, "speak_budget_chars": 300, "dropped": 0}


class GateBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "brief-latest.json"
        self.clock = FakeClock(TODAY)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, obj):
        self.path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def app(self, vision_reply=APPROVE, **gate):
        g = dict({"enabled": True, "brief_path": str(self.path)}, **gate)
        cfg = config(alarm={"morning_gate": g})
        return Sidekick(cfg, vision=FakeLLM(vision_reply), clock=self.clock, spawn=lambda fn, *a: fn(*a))


class FlowTests(GateBase):
    IMG = "data:image/jpeg;base64,AAAA"

    def test_approve_speaks_brief(self):
        self.write(brief())
        app = self.app()
        r = app.alarm_check(self.IMG)
        self.assertEqual(r["verdict"], "approved")
        self.assertTrue(r["say"].startswith("승인. 좋은 아침이에요!"))
        self.assertTrue(r["say"].endswith(SPEAK))
        self.assertEqual(r["brief"], "ok")
        self.assertFalse(app.alarm.status()["ringing"])

    def test_reject_never_reads_brief(self):
        self.write(brief(speak=SECRET_SPEAK))
        app = self.app(REJECT)
        r = app.alarm_check(self.IMG)
        self.assertEqual(r["verdict"], "rejected")
        self.assertNotIn(SECRET_SPEAK, json.dumps(r, ensure_ascii=False))
        self.assertNotIn("brief", r)
        self.assertEqual(r["say"], "반려. 이불 속")  # 기본: reject_text도 말하지 않음
        self.assertTrue(app.alarm.status()["ringing"])

    def test_reject_text_optional(self):
        self.write(brief(speak=SECRET_SPEAK, reject="일어나면 해 드립니다"))
        r = self.app(REJECT, speak_reject=True).alarm_check(self.IMG)
        self.assertEqual(r["say"], "반려. 이불 속 일어나면 해 드립니다")
        self.assertNotIn(SECRET_SPEAK, r["say"])
        # 파일을 못 읽으면 설정의 대체 문장
        self.path.unlink()
        r = self.app(REJECT, speak_reject=True).alarm_check(self.IMG)
        self.assertEqual(r["say"], "반려. 이불 속 브리핑은 일어나면 해 드립니다.")

    def test_stale_date_falls_back(self):
        self.write(brief(date="2026-11-01", speak=SECRET_SPEAK))
        r = self.app().alarm_check(self.IMG)
        self.assertEqual(r["verdict"], "approved")
        self.assertNotIn(SECRET_SPEAK, r["say"])
        self.assertTrue(r["say"].endswith(STALE_DEFAULT))
        self.assertEqual(r["brief"], "stale")
        r = self.app(stale_text="브리핑 없음").alarm_check(self.IMG)
        self.assertTrue(r["say"].endswith("브리핑 없음"))

    def test_missing_invalid_and_empty_fall_back(self):
        for content in (None, "{broken", "[]", json.dumps(brief(speak="  ")), "\xff"):
            if content is None:
                self.path.unlink(missing_ok=True)
            else:
                self.path.write_text(content, encoding="utf-8")
            r = self.app().alarm_check(self.IMG)
            self.assertEqual(r["verdict"], "approved", content)
            self.assertTrue(r["say"].endswith(STALE_DEFAULT), content)

    def test_disabled_by_default(self):
        self.write(brief())
        cfg = config()
        self.assertFalse(cfg["alarm"]["morning_gate"]["enabled"])
        app = Sidekick(cfg, vision=FakeLLM(APPROVE), clock=self.clock)
        r = app.alarm_check(self.IMG)
        self.assertEqual(r["say"], "승인. 좋은 아침이에요! 칫솔 확인")
        self.assertNotIn("brief", r)
        # 켜져 있어도 경로가 비면 아무것도 안 함
        self.assertEqual(MorningGate({"enabled": True, "brief_path": ""}, self.clock).on_approved(), ("", "disabled"))

    def test_emergency_off_does_not_read_brief(self):
        self.write(brief())
        app = self.app()
        r = app.alarm.emergency_off("4242")
        self.assertTrue(r["ok"])
        self.assertNotIn(SPEAK, r["say"])

    def test_manual_mode_answer_speaks_brief(self):
        self.write(brief())
        cfg = config(alarm={"morning_gate": {"enabled": True, "brief_path": str(self.path)}})
        app = Sidekick(cfg, clock=self.clock)  # VLM 없음 -> 수동 모드
        app.alarm_check(self.IMG)
        self.clock.advance(seconds=46)
        ch = app.alarm_check(self.IMG)
        self.assertEqual(ch["verdict"], "challenge")
        wrong = app.alarm_answer("0")
        self.assertFalse(wrong["ok"])
        self.assertNotIn(SPEAK, wrong["say"])
        ch = app.alarm_check(self.IMG)
        a, b, c = [int(x) for x in ch["question"].replace("×", " ").replace("+", " ").replace("= ?", " ").split()]
        ok = app.alarm_answer(str(a * b + c))
        self.assertTrue(ok["ok"])
        self.assertTrue(ok["say"].endswith(SPEAK))

    def test_long_speak_text_is_capped(self):
        self.write(brief(speak="가" * 500))
        r = self.app().alarm_check(self.IMG)
        self.assertEqual(len(r["say"].rsplit(" ", 1)[-1]), 300)

    def test_never_modifies_source(self):
        self.write(brief())
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns, sorted(os.listdir(self.dir)))
        app = self.app()
        app.alarm_check(self.IMG)
        self.app(REJECT, speak_reject=True).alarm_check(self.IMG)
        self.assertEqual(read_brief(str(self.path), TODAY.date())[0], "ok")
        after = (self.path.read_bytes(), self.path.stat().st_mtime_ns, sorted(os.listdir(self.dir)))
        self.assertEqual(before, after)

    def test_over_http(self):
        self.write(brief())
        live = LiveServer(self.app())
        try:
            code, r = live.call("/api/alarm/check", {"image": self.IMG})
            self.assertEqual(code, 200)
            self.assertTrue(r["say"].endswith(SPEAK))
        finally:
            live.close()


if __name__ == "__main__":
    unittest.main()
