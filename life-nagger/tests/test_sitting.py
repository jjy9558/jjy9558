import unittest
from datetime import datetime

from tests.helpers import Base


class SittingTests(Base):
    def setUp(self):
        self.make(datetime(2026, 11, 2, 10, 0))   # 낮 시간
        self.plat.idle = 1

    def run_for(self, minutes, idle=1, step=1):
        for _ in range(minutes // step):
            self.plat.idle = idle
            self.step(minutes=step)

    def test_alert_at_two_hours_not_before(self):
        self.n.tick()
        self.run_for(119)
        self.assertEqual(self.sink.texts("sitting"), [])
        self.run_for(1)
        t = self.sink.texts("sitting")
        self.assertEqual(len(t), 1)
        self.assertTrue(t[0].startswith("엉덩이 굳는다. 일어나서 물 한 잔."))

    def test_repeats_every_ten_minutes(self):
        self.n.tick()
        self.run_for(120 + 30)
        self.assertEqual(len(self.sink.texts("sitting")), 4)   # 120,130,140,150

    def test_short_break_does_not_reset(self):
        self.n.tick()
        self.run_for(100)
        self.plat.idle = 240            # 4분 쉼
        self.step(minutes=1)
        self.run_for(19)
        self.assertEqual(len(self.sink.texts("sitting")), 1)

    def test_five_minute_break_resets_and_praises(self):
        self.n.tick()
        self.run_for(125)
        self.assertEqual(len(self.sink.texts("sitting")), 1)
        self.plat.idle = 300
        self.step(minutes=1)
        self.assertIn("잘했다", self.sink.texts("sitting")[-1])
        self.run_for(60)
        self.assertEqual(len(self.sink.texts("sitting")), 2)   # 칭찬 1 + 경보 1, 새 경보 없음
        self.run_for(61)                                       # 새 스트릭 2시간 넘김
        self.assertTrue(self.sink.texts("sitting")[-1].startswith("엉덩이"))

    def test_daemon_gap_counts_as_break(self):
        self.n.tick()
        self.run_for(100)
        self.step(minutes=30)           # 절전/데몬 중단
        self.assertEqual(self.n.sitting.streak(self.clock.now()).total_seconds(), 0)
        self.run_for(30)
        self.assertEqual(self.sink.texts("sitting"), [])


if __name__ == "__main__":
    unittest.main()
