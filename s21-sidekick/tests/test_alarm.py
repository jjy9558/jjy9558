import datetime as dt
import random
import unittest

from helpers import FakeClock, FakeLLM, config
from sidekick.alarm import Alarm, parse_verdict

IMG = "data:image/jpeg;base64,AAAA"


def make(vision=None, start="2026-11-02 06:59", **alarm):
    """알람은 start 시각에 '설정'된다. 07:00 정각 시작이면 그 창은 유효(>=)."""
    clock = FakeClock(dt.datetime.strptime(start, "%Y-%m-%d %H:%M"))
    a = Alarm(config(alarm=dict({"time": "07:00"}, **alarm))["alarm"], vision=vision, clock=clock,
              rng=random.Random(1))
    return a, clock


class VerdictParsingTests(unittest.TestCase):
    def test_json(self):
        self.assertEqual(parse_verdict('{"approved": true, "reason": "칫솔 확인"}'), (True, "칫솔 확인"))
        self.assertEqual(parse_verdict('결과: {"approved": false, "reason": "아직 누워 있음"} 끝'), (False, "아직 누워 있음"))
        self.assertEqual(parse_verdict('```json\n{"approved": "yes"}\n```')[0], True)

    def test_keywords(self):
        self.assertTrue(parse_verdict("APPROVED")[0])
        self.assertTrue(parse_verdict("승인")[0])
        self.assertFalse(parse_verdict("반려. 아직 누워 있음")[0])
        self.assertFalse(parse_verdict("not approved")[0])

    def test_ambiguous_rejects(self):
        for t in ["", "음... 잘 모르겠어요", "{broken json"]:
            self.assertFalse(parse_verdict(t)[0], t)


class AlarmTests(unittest.TestCase):
    def test_rings_only_in_window(self):
        a, c = make()
        self.assertFalse(a.status()["ringing"])
        c.advance(minutes=1)
        self.assertTrue(a.status()["ringing"])
        c.advance(minutes=61)
        self.assertFalse(a.status()["ringing"])  # 최대 울림 시간 이후 자동 종료

    def test_setting_past_time_does_not_ring_immediately(self):
        a, c = make(start="2026-11-02 23:00")
        self.assertFalse(a.status()["ringing"])

    def test_window_crossing_midnight(self):
        a, c = make(start="2026-11-02 23:40", time="23:50")
        c.advance(minutes=30)  # 다음날 00:10
        self.assertTrue(a.status()["ringing"])

    def test_changing_time_into_past_window_does_not_ring(self):
        a, c = make(start="2026-11-02 07:10")
        a.set_time("07:00")
        self.assertFalse(a.status()["ringing"])

    def test_vision_reject_then_approve(self):
        v = FakeLLM('{"approved": false, "reason": ""}')
        a, c = make(vision=v, start="2026-11-02 07:00")
        r = a.check_photo(IMG)
        self.assertEqual(r["verdict"], "rejected")
        self.assertEqual(r["say"], "반려. 아직 누워 있음.")
        self.assertEqual(v.calls[0][2], IMG)
        v.reply = '{"approved": true, "reason": "칫솔 확인"}'
        self.assertEqual(a.check_photo(IMG)["verdict"], "approved")
        self.assertFalse(a.status()["ringing"])
        self.assertEqual(a.status()["dismissed_by"], "photo")

    def test_vision_down_tells_user_emergency(self):
        a, _ = make(vision=FakeLLM(fail=True), start="2026-11-02 07:00")
        r = a.check_photo(IMG)
        self.assertEqual(r["verdict"], "error")
        self.assertTrue(a.status()["ringing"])

    def test_snooze_limit(self):
        a, c = make(start="2026-11-02 07:00", max_snoozes=2, snooze_minutes=5)
        self.assertTrue(a.snooze()["ok"])
        self.assertFalse(a.status()["ringing"])
        c.advance(minutes=5)
        self.assertTrue(a.status()["ringing"])
        self.assertTrue(a.snooze()["ok"])
        c.advance(minutes=5)
        r = a.snooze()
        self.assertFalse(r["ok"])
        self.assertIn("스누즈를 다 썼어요", r["say"])
        self.assertTrue(a.status()["ringing"])

    def test_emergency_code(self):
        a, c = make(start="2026-11-02 07:00")
        self.assertFalse(a.emergency_off("0000")["ok"])
        self.assertTrue(a.status()["ringing"])
        self.assertTrue(a.emergency_off(" 4242 ")["ok"])
        self.assertFalse(a.status()["ringing"])
        self.assertEqual(a.status()["dismissed_by"], "emergency")

    def test_emergency_bruteforce_lockout_is_temporary(self):
        a, c = make(start="2026-11-02 07:00")
        for _ in range(5):
            a.emergency_off("1")
        self.assertFalse(a.emergency_off("4242")["ok"])  # 잠시 잠김
        c.advance(seconds=61)
        self.assertTrue(a.emergency_off("4242")["ok"])

    def test_manual_mode_camera_then_math(self):
        a, c = make(start="2026-11-02 07:00", fallback_camera_seconds=30)
        self.assertEqual(a.check_photo(IMG)["verdict"], "wait")
        c.advance(seconds=10)
        self.assertEqual(a.check_photo(IMG)["remaining"], 21)
        c.advance(seconds=25)
        r = a.check_photo(IMG)
        self.assertEqual(r["verdict"], "challenge")
        q = r["question"].replace("= ?", "").replace("×", "*")
        self.assertFalse(a.answer("-1")["ok"])  # 틀리면 새 문제
        q = a.check_photo(IMG)["question"].replace("= ?", "").replace("×", "*")
        self.assertTrue(a.answer(str(eval(q)))["ok"])
        self.assertFalse(a.status()["ringing"])

    def test_ring_now_and_idle_check(self):
        a, c = make(start="2026-11-02 12:00")
        self.assertEqual(a.check_photo(IMG)["verdict"], "idle")
        a.ring_now()
        self.assertTrue(a.status()["ringing"])

    def test_next_day_rings_again(self):
        a, c = make(start="2026-11-02 07:00")
        a.emergency_off("4242")
        c.advance(days=1)
        self.assertTrue(a.status()["ringing"])


if __name__ == "__main__":
    unittest.main()
