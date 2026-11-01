import unittest

from helpers import FakeMono, config
from sidekick.debugger import Debugger
from sidekick.triggers import Cooldown, TriggerMatcher, normalize


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.m = TriggerMatcher(config()["debugger"]["triggers"])

    def test_korean_variants_match(self):
        for u in ["아 이거 왜 안 돼", "왜안돼", "아 왜 안되지?", "이거 왜 안 되냐", "왜 안되는데 진짜",
                  "에러 뭐야", "아 에러가 뭐야 이거", "오류 뭐야..", "또 터졌네", "왜이래"]:
            self.assertIsNotNone(self.m.match(u), u)

    def test_unrelated_speech_ignored(self):
        for u in ["점심 뭐 먹지", "이거 잘 된다", "안녕", "", "왜냐하면 그게 맞아"]:
            self.assertIsNone(self.m.match(u), u)

    def test_normalize(self):
        self.assertEqual(normalize("왜 안 돼?!"), normalize("왜안되"))
        self.assertEqual(normalize("오류 뭐야"), normalize("에러뭐야"))

    def test_cooldown(self):
        mono = FakeMono()
        cd = Cooldown(60, clock=mono)
        self.assertTrue(cd.try_acquire())
        self.assertFalse(cd.try_acquire())
        mono.t += 59
        self.assertFalse(cd.try_acquire())
        mono.t += 2
        self.assertTrue(cd.try_acquire())

    def test_debugger_check_respects_cooldown(self):
        mono = FakeMono()
        d = Debugger(config()["debugger"], clock=mono)
        self.assertEqual(d.check("왜 안 돼")[0], "trigger")
        self.assertEqual(d.check("에러 뭐야")[0], "cooldown")
        self.assertEqual(d.check("점심 뭐 먹지")[0], None)
        mono.t += 61
        self.assertEqual(d.check("에러 뭐야")[0], "trigger")


if __name__ == "__main__":
    unittest.main()
