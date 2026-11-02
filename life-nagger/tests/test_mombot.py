import os
import unittest
from datetime import datetime

from nagger.config import ConfigError, load_config
from nagger.model import Action
from nagger.platform import Platform
from nagger.timeutil import in_window
from tests.helpers import Base


class MomBotTests(Base):
    def test_no_warning_before_window(self):
        self.make(datetime(2026, 11, 2, 1, 40))
        self.plat.processes = {"Game.exe"}
        for _ in range(3):
            self.step(minutes=5)
        self.assertEqual(self.sink.texts("mombot"), [])

    def test_no_game_no_warning(self):
        self.make(datetime(2026, 11, 2, 2, 5))
        self.n.tick()
        self.assertEqual(self.sink.texts("mombot"), [])

    def test_escalation_three_warnings_then_stops_without_sleep(self):
        self.make(datetime(2026, 11, 2, 1, 55))
        self.plat.processes = {"game.exe"}
        self.n.tick()                      # 01:55 창 밖
        self.step(minutes=5)               # 02:00 -> 1단계
        self.assertEqual(len(self.sink.texts("mombot")), 1)
        self.assertIn("몇 시니", self.sink.texts("mombot")[0])
        self.step(minutes=9)
        self.assertEqual(len(self.sink.texts("mombot")), 1)   # 아직 간격 전
        self.step(minutes=1)               # +10분
        self.assertIn("엄마 화났다", self.sink.texts("mombot")[1])
        self.step(minutes=10)
        self.assertIn("마지막 경고", self.sink.texts("mombot")[2])
        for _ in range(30):                # 몇 시간이 지나도 더 이상 없음, 재우지 않음
            self.step(minutes=10)
            if self.clock.now().hour >= 6:
                break
        self.assertEqual(len(self.sink.texts("mombot")), 3)
        self.assertEqual(self.plat.suspends, [])
        self.assertEqual(self.n.mombot.state, "idle" if self.clock.now().hour >= 6 else "exhausted")

    def test_reset_when_game_closes(self):
        self.make(datetime(2026, 11, 2, 2, 0))
        self.plat.processes = {"game.exe"}
        self.n.tick()
        self.step(minutes=10)
        self.assertEqual(self.n.mombot.stage, 2)
        self.plat.processes = set()
        self.step(minutes=1)
        self.assertEqual(self.n.mombot.state, "idle")
        self.plat.processes = {"game.exe"}
        self.step(minutes=1)
        self.assertEqual(self.n.mombot.stage, 1)
        self.assertIn("몇 시니", self.sink.texts("mombot")[-1])

    def test_window_wraps_midnight(self):
        self.assertTrue(in_window(datetime(2026, 1, 1, 23, 30), "23:00", "05:00"))
        self.assertTrue(in_window(datetime(2026, 1, 1, 4, 59), "23:00", "05:00"))
        self.assertFalse(in_window(datetime(2026, 1, 1, 5, 0), "23:00", "05:00"))

    def test_never_kills_processes(self):
        for name in dir(Platform):
            self.assertNotIn("kill", name.lower())
            self.assertNotIn("terminate", name.lower())


class SleepSafetyTests(Base):
    def _to_countdown(self, **mom):
        self.make(datetime(2026, 11, 2, 2, 0), {"mombot": dict({"allow_sleep": True}, **mom)})
        self.plat.processes = {"game.exe"}
        self.n.tick()
        self.step(minutes=10)
        self.step(minutes=10)
        self.assertEqual(self.n.mombot.stage, 3)
        self.step(minutes=5)       # 저장 유예(5분) 끝 -> 카운트다운 시작
        self.assertEqual(self.n.mombot.state, "countdown")

    def test_disabled_by_default(self):
        cfg = load_config(None)
        self.assertIs(cfg["mombot"]["allow_sleep"], False)

    def test_string_true_rejected(self):
        with self.assertRaises(ConfigError):
            load_config(None, {"mombot": {"allow_sleep": "true"}})

    def test_engine_ignores_suspend_action_when_disabled(self):
        self.make()
        self.n._execute(Action("suspend"))
        self.assertEqual(self.plat.suspends, [])

    def test_countdown_completes_and_suspends_once(self):
        self._to_countdown()
        self.assertIn("60초", self.sink.texts("mombot")[-1])
        self.assertEqual(self.plat.suspends, [])
        self.step(seconds=30)
        self.assertEqual(self.plat.suspends, [])
        self.step(seconds=29)
        self.assertEqual(self.plat.suspends, [])           # 59초: 아직
        self.step(seconds=1)
        self.assertEqual(self.plat.suspends, ["suspend"])
        self.step(minutes=1)                                # 깨어난 뒤 게임이 그대로여도 또 재우지 않음
        self.step(minutes=10)
        self.assertEqual(self.plat.suspends, ["suspend"])

    def test_cancel_flag_stops_countdown(self):
        self._to_countdown()
        self.step(seconds=20)
        open(self.n.cancel_path, "w").close()
        self.step(seconds=5)
        self.assertEqual(self.n.mombot.state, "cancelled")
        self.step(seconds=120)
        self.assertEqual(self.plat.suspends, [])
        self.assertFalse(os.path.exists(self.n.cancel_path))

    def test_stale_cancel_flag_cleared_at_countdown_start(self):
        self.make(datetime(2026, 11, 2, 2, 0), {"mombot": {"allow_sleep": True}})
        open(self.n.cancel_path, "w").close()
        self.plat.processes = {"game.exe"}
        self.n.tick(); self.step(minutes=10); self.step(minutes=10); self.step(minutes=5)
        self.assertEqual(self.n.mombot.state, "countdown")   # 오래된 깃발로 즉시 취소되지 않음

    def test_game_closing_cancels_countdown(self):
        self._to_countdown()
        self.plat.processes = set()
        self.step(seconds=30)
        self.step(minutes=5)
        self.assertEqual(self.plat.suspends, [])
        self.assertEqual(self.n.mombot.state, "idle")

    def test_hibernate_method_and_dry_run(self):
        self._to_countdown(sleep_method="hibernate")
        self.step(seconds=60)
        self.assertEqual(self.plat.suspends, ["hibernate"])
        self.make(datetime(2026, 11, 2, 2, 0), {"mombot": {"allow_sleep": True}})
        self.n.dry_run = True
        self.plat.processes = {"game.exe"}
        self.n.tick(); self.step(minutes=10); self.step(minutes=10); self.step(minutes=5); self.step(seconds=60)
        self.assertEqual(self.plat.suspends, [])
        self.assertTrue(any("dry-run" in t for t in self.sink.texts("mombot")))

    def test_fast_poll_during_countdown(self):
        self._to_countdown()
        self.assertLessEqual(self.n.poll_interval(), 5)


if __name__ == "__main__":
    unittest.main()
