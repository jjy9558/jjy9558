import json
import os
import tempfile
import unittest
from datetime import datetime

from nagger.counters import Counters, dish_line
from nagger.excuse import fmt_late, generate, honorific
from nagger.timeutil import ko_hour

KINDS = {"sigh": {"label": "한숨", "unit": "회"}, "pushup": {"label": "푸시업", "unit": "개"},
         "dishes": {"label": "설거지", "unit": "회"}}


class ExcuseTests(unittest.TestCase):
    def test_three_plus_honest(self):
        drafts, honest, src = generate(20, "팀장", seed=1)
        self.assertEqual(len(drafts), 3)
        self.assertEqual(len(set(drafts)), 3)
        self.assertEqual(src, "template")
        self.assertIn("늦잠 잤습니다, 죄송합니다", honest)
        for d in drafts + [honest]:
            self.assertIn("팀장님", d)
            self.assertIn("20분", d)

    def test_deterministic_with_seed_and_formatting(self):
        self.assertEqual(generate(5, "팀장", seed=3), generate(5, "팀장", seed=3))
        self.assertEqual(fmt_late(75), "1시간 15분")
        self.assertEqual(fmt_late(120), "2시간")
        self.assertEqual(honorific("김 팀장님"), "김 팀장님")
        self.assertEqual(honorific(""), "안녕하세요")
        _, honest, _ = generate(10, "팀장", where="remote", seed=1)
        self.assertIn("접속", honest)

    def test_llm_failure_falls_back(self):
        calls = []

        def boom(*a, **k):
            calls.append(1)
            raise OSError("no net")
        drafts, honest, src = generate(20, "팀장", seed=1, llm_cfg={"base_url": "http://x/v1"}, use_llm=True, post=boom)
        self.assertEqual((src, len(drafts), bool(calls)), ("template", 3, True))
        self.assertIn("늦잠", honest)

    def test_llm_bad_status_and_garbage_fall_back(self):
        for resp in ((500, b"x"), (200, b"not json"), (200, json.dumps({"choices": [{"message": {"content": ""}}]}).encode())):
            _, _, src = generate(20, "팀장", seed=1, llm_cfg={"base_url": "http://x/v1"}, use_llm=True,
                                 post=lambda *a, r=resp, **k: r)
            self.assertEqual(src, "template")

    def test_llm_success_keeps_honest(self):
        body = json.dumps({"choices": [{"message": {"content": json.dumps(
            ["팀장님, 지하철 지연으로 15분 늦습니다. 죄송합니다.", "팀장님, 차가 막혀 15분 늦습니다. 죄송합니다."])}}]}).encode()
        drafts, honest, src = generate(15, "팀장", seed=1, llm_cfg={"base_url": "http://x/v1"}, use_llm=True,
                                       post=lambda *a, **k: (200, body))
        self.assertEqual(src, "llm")
        self.assertEqual(len(drafts), 3)         # 부족분은 템플릿으로 보충
        self.assertIn("늦잠", honest)

    def test_llm_not_used_unless_requested(self):
        def boom(*a, **k):
            raise AssertionError("호출되면 안 됨")
        generate(20, "팀장", llm_cfg={"base_url": "http://x"}, use_llm=False, post=boom)


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.c = Counters(os.path.join(self.t.name, "c.jsonl"), KINDS)

    def test_weekly_report_and_peak(self):
        # 2026-11-02 는 월요일. 화요일 15시에 최다.
        for _ in range(100):
            self.c.add("sigh", datetime(2026, 11, 3, 15, 10))
        for _ in range(40):
            self.c.add("sigh", datetime(2026, 11, 4, 9, 0))
        for _ in range(7):
            self.c.add("sigh", datetime(2026, 11, 6, 22, 0))
        self.c.add("sigh", datetime(2026, 10, 30, 12, 0))     # 지난 주: 제외
        r = self.c.weekly_report(datetime(2026, 11, 6, 23, 0))
        self.assertIn("이번 주 한숨 147회, 화요일 오후 3시 최고점", r)
        self.assertIn("푸시업 기록 없음", r)
        last = self.c.weekly_report(datetime(2026, 11, 6, 23, 0), -1)
        self.assertIn("지난 주 한숨 1회, 금요일 오후 12시 최고점", last)

    def test_peak_tie_prefers_earlier_and_n_weight(self):
        self.c.add("pushup", datetime(2026, 11, 4, 8, 0), 20)
        self.c.add("pushup", datetime(2026, 11, 3, 8, 0), 20)
        s, e = Counters.week_range(datetime(2026, 11, 5))
        self.assertEqual(self.c.peak("pushup", s, e), (1, 8, 20))
        self.assertEqual(self.c.total("pushup", s, e), 40)

    def test_week_boundary_monday(self):
        self.c.add("sigh", datetime(2026, 11, 2, 0, 0))
        self.c.add("sigh", datetime(2026, 11, 1, 23, 59, 59))
        s, e = Counters.week_range(datetime(2026, 11, 8, 12))
        self.assertEqual(self.c.total("sigh", s, e), 1)

    def test_validation_and_corrupt_lines(self):
        with self.assertRaises(ValueError):
            self.c.add("burp", datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            self.c.add("sigh", datetime(2026, 1, 1), 0)
        self.c.add("sigh", datetime(2026, 11, 3, 1, 0))
        with open(self.c.path, "a") as f:
            f.write("{garbage\n")
        s, e = Counters.week_range(datetime(2026, 11, 3))
        self.assertEqual(self.c.total("sigh", s, e), 1)

    def test_dishes_days_and_escalation(self):
        self.assertIsNone(self.c.dish_days(datetime(2026, 11, 2)))
        self.c.add("dishes", datetime(2026, 11, 1, 22, 0))
        self.assertEqual(self.c.dish_days(datetime(2026, 11, 1, 23)), 0)
        self.assertEqual(self.c.dish_days(datetime(2026, 11, 4, 8)), 3)
        lines = [dish_line(d) for d in range(0, 10)]
        self.assertEqual(len(set(lines[:7])), 7)
        self.assertIn("9일째", lines[9])
        self.c.add("dishes", datetime(2026, 11, 4, 9))        # 설거지하면 리셋
        self.assertEqual(self.c.dish_days(datetime(2026, 11, 4, 10)), 0)

    def test_ko_hour(self):
        self.assertEqual([ko_hour(h) for h in (0, 9, 12, 15, 23)],
                         ["오전 12시", "오전 9시", "오후 12시", "오후 3시", "오후 11시"])


if __name__ == "__main__":
    unittest.main()
