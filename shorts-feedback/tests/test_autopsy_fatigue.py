import tempfile
import unittest
from datetime import date
from pathlib import Path

from shortsfb import autopsy, fatigue


class AutopsyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self):
        autopsy.add(self.data, "vid1", title="가", views=100, causes="hook_weak,too_long", when="2026-10-01")
        autopsy.add(self.data, "vid2", title="나", views=90, causes="hook_weak,title_mismatch", when="2026-10-02")
        autopsy.add(self.data, "vid3", title="다", views=80, causes="hook_weak,too_long,audio_issue", when="2026-10-03")
        autopsy.add(self.data, "vid4", title="라", views=70, causes="ending_weak", when="2026-10-04")

    # WP11 인수 (6)
    def test_accumulates_and_ranks_top_three(self):
        self.seed()
        p = autopsy.patterns(self.data)
        self.assertEqual(p["episodes"], 4)
        self.assertEqual([(t["cause"], t["count"]) for t in p["top"][:2]], [("hook_weak", 3), ("too_long", 2)])
        self.assertEqual(len(p["top"]), 3)
        self.assertEqual(p["top"][2]["count"], 1)
        text = autopsy.format_patterns(self.data)
        self.assertIn("첫 3초 훅이 약함 (3/4건)", text)

    def test_rerun_is_idempotent(self):
        self.seed()
        path = self.data / autopsy.FILE
        before = path.read_bytes()
        state, _ = autopsy.add(self.data, "vid1", title="가", views=100, causes="hook_weak,too_long", when="2026-10-01")
        self.assertEqual(state, "updated")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(autopsy.entries(self.data)), 4)
        # 날짜를 다시 주지 않아도 기존 날짜를 유지한다
        autopsy.add(self.data, "vid1", title="가", views=100, causes="hook_weak,too_long")
        self.assertEqual(path.read_bytes(), before)

    def test_update_replaces_causes_instead_of_accumulating(self):
        self.seed()
        autopsy.add(self.data, "vid1", causes="topic_stale")
        e = {x["video"]: x for x in autopsy.entries(self.data)}["vid1"]
        self.assertEqual(e["causes"], ["topic_stale"])

    def test_custom_causes_dedup_and_checklist(self):
        autopsy.add(self.data, "v", causes="hook-weak, hook_weak , 내가 정한 원인")
        e = autopsy.entries(self.data)[0]
        self.assertEqual(e["causes"], ["hook_weak", "내가 정한 원인"])
        self.seed()
        cl = autopsy.format_checklist(self.data)
        self.assertTrue(cl.startswith("## 다음 편 기획 전 확인"))
        self.assertIn("- [ ] 첫 3초 훅이 약함:", cl)
        self.assertEqual(cl.count("- [ ]"), 3)

    def test_note_and_title_are_redacted(self):
        autopsy.add(self.data, "v", title="연락 010-1234-5678", note="메일 a.b@example.com 로 문의", causes="timing")
        raw = (self.data / autopsy.FILE).read_text(encoding="utf-8")
        self.assertNotIn("010-1234-5678", raw)
        self.assertNotIn("a.b@example.com", raw)

    def test_validation_and_empty(self):
        with self.assertRaises(ValueError):
            autopsy.add(self.data, "  ")
        with self.assertRaises(ValueError):
            autopsy.add(self.data, "v", views=-1)
        with self.assertRaises(ValueError):
            autopsy.add(self.data, "v", when="2026-13-45")
        self.assertIn("없습니다", autopsy.format_patterns(self.data))
        self.assertIn("없습니다", autopsy.format_list(self.data))
        self.assertIn("쌓이면", autopsy.format_checklist(self.data))


class FatigueCase(unittest.TestCase):
    def test_window_rule(self):
        days = {"2026-11-01": 2, "2026-11-02": 3, "2026-10-20": 9}
        r = fatigue.evaluate(days, date(2026, 11, 2), window=3, limit=5)
        self.assertTrue(r["tired"])
        self.assertEqual(r["recent"], 5)
        self.assertIn("최근 3일 동안 5편", r["line"])
        self.assertEqual(r["line"].count("\n"), 0)

    def test_below_limit_is_fine(self):
        r = fatigue.evaluate({"2026-11-02": 4}, date(2026, 11, 2), window=3, limit=5)
        self.assertFalse(r["tired"])
        self.assertIn("괜찮아요", r["line"])

    def test_old_batches_do_not_count(self):
        r = fatigue.evaluate({"2026-10-25": 20}, date(2026, 11, 2), window=3, limit=5)
        self.assertFalse(r["tired"])

    def test_streak_rule_counts_from_yesterday_if_today_empty(self):
        days = {"2026-10-%02d" % d: 1 for d in range(27, 32)}
        days["2026-11-01"] = 1
        r = fatigue.evaluate(days, date(2026, 11, 2), window=3, limit=99, streak=6)
        self.assertEqual(r["streak"], 6)
        self.assertTrue(r["tired"])
        self.assertIn("6일 연속", r["line"])
        r = fatigue.evaluate(days, date(2026, 11, 2), window=3, limit=99, streak=7)
        self.assertFalse(r["tired"])

    def test_record_add_set_and_zero(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(fatigue.record(d, "2026-11-02", 2), 2)
            self.assertEqual(fatigue.record(d, "2026-11-02", 1), 3)
            self.assertEqual(fatigue.record(d, "2026-11-02", 5, absolute=True), 5)
            self.assertEqual(fatigue.load(d), {"2026-11-02": 5})
            self.assertEqual(fatigue.record(d, "2026-11-02", 0, absolute=True), 0)
            self.assertEqual(fatigue.load(d), {})
            with self.assertRaises(ValueError):
                fatigue.record(d, "2026-11-02", -1)
            with self.assertRaises(ValueError):
                fatigue.record(d, "nope", 1)


if __name__ == "__main__":
    unittest.main()
