import datetime as dt
import os
import unittest

from fakes import FakeSystem, TempHome

from pc_doctor import updates
from pc_doctor.config import read_json

TODAY = dt.date(2026, 11, 1)
EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "known_issues.example.json")


def issues(*items):
    return [dict({"kb": "", "title_contains": "", "level": "yellow", "summary": "s", "workaround": "", "source": ""}, **i)
            for i in items]


class UpdatesTests(unittest.TestCase):
    def test_collect_parses_single_object_pending(self):
        d = updates.collect(FakeSystem())
        self.assertEqual([h["kb"] for h in d["hotfixes"]], ["KB0000000", "KB0000002"])
        self.assertEqual(d["pending"], [{"title": "2026-11 미리 보기 (KB0000001)", "kbs": ["KB0000001"]}])
        self.assertEqual(d["history"][1]["kbs"], ["KB0000009"])

    def test_pending_undetectable(self):
        d = updates.collect(FakeSystem(ps={"pending_updates": None}))
        self.assertIsNone(d["pending"])
        lines = updates.format_report(d, updates.evaluate(d, [], today=TODAY), [])
        self.assertTrue(any("감지 불가" in l for l in lines))

    def test_green_when_clean(self):
        d = updates.collect(FakeSystem(ps={"update_history": "[]"}))
        ev = updates.evaluate(d, [], today=TODAY)
        self.assertEqual(ev["signal"], "🟢")

    def test_failed_install_is_yellow(self):
        d = updates.collect(FakeSystem())
        ev = updates.evaluate(d, [], today=TODAY)
        self.assertEqual(ev["level"], "yellow")
        self.assertTrue(any("설치 실패" in m for _l, m in ev["reasons"]))

    def test_red_for_pending_known_issue(self):
        d = updates.collect(FakeSystem(ps={"update_history": "[]"}))
        ev = updates.evaluate(d, issues({"kb": "KB0000001", "level": "red", "summary": "BSOD"}), today=TODAY)
        self.assertEqual(ev["signal"], "🔴")
        self.assertTrue(any("보류" in m for _l, m in ev["reasons"]))

    def test_installed_known_issue_and_title_match(self):
        d = updates.collect(FakeSystem(ps={"update_history": "[]", "pending_updates": "[]"}))
        ev = updates.evaluate(d, issues({"kb": "KB0000000", "level": "yellow"}), today=TODAY)
        self.assertEqual(ev["level"], "yellow")
        d["pending"] = [{"title": "NVIDIA - Display - 32.0.15.9999", "kbs": []}]
        ev = updates.evaluate(d, issues({"title_contains": "nvidia - display", "level": "red"}), today=TODAY)
        self.assertEqual(ev["level"], "red")

    def test_stale_updates_yellow(self):
        d = updates.collect(FakeSystem(ps={"update_history": "[]", "pending_updates": "[]"}))
        ev = updates.evaluate(d, [], today=dt.date(2027, 3, 1))
        self.assertEqual(ev["level"], "yellow")
        self.assertTrue(any("일 전" in m for _l, m in ev["reasons"]))

    def test_example_file_uses_only_placeholder_kbs(self):
        data = read_json(EXAMPLE)
        self.assertIn("가짜", data["_comment"])
        for i in data["issues"]:
            if i.get("kb"):
                self.assertRegex(i["kb"], r"^KB000000\d$")
        with TempHome():
            loaded = updates.load_known_issues(EXAMPLE)
            self.assertEqual(len(loaded), 3)
            self.assertIsNone(updates.load_known_issues())  # 사용자 파일 없음


if __name__ == "__main__":
    unittest.main()
