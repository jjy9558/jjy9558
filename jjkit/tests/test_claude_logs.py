import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jjkit import claude_logs as C


def rec(typ, content, ts="2026-11-01T10:00:00.000Z", **kw):
    d = {"type": typ, "timestamp": ts, "sessionId": "s1", "cwd": "/w/p", "uuid": "u-%s" % ts,
         "message": {"role": typ, "content": content}}
    d.update(kw)
    return d


class CleanTests(unittest.TestCase):
    def test_reminders_removed(self):
        self.assertEqual(C.clean_text("앞 <system-reminder>숨김</system-reminder> 뒤"), "앞  뒤")
        self.assertIsNone(C.clean_text("<system-reminder>열린 채로"))
        self.assertIsNone(C.clean_text("<system-reminder>x</system-reminder>"))

    def test_noise(self):
        for t in ("<command-name>/clear</command-name>", "Caveat: x", "[Request interrupted by user]", "  ", None, 3):
            self.assertIsNone(C.clean_text(t), t)
        self.assertEqual(C.clean_text("  진짜 질문  "), "진짜 질문")

    def test_timestamp(self):
        self.assertEqual(C.parse_timestamp("2026-11-01T10:00:00Z"),
                         datetime(2026, 11, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(C.parse_timestamp("2026-11-01T19:00:00+09:00").utcoffset(), timedelta(hours=9))
        self.assertIsNone(C.parse_timestamp("어제"))
        self.assertIsNone(C.parse_timestamp(None))


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        proj = self.root / "-w-p"
        (proj / "subagents").mkdir(parents=True)
        lines = [
            rec("user", "첫 질문", "2026-11-01T09:00:00Z"),
            rec("assistant", [{"type": "text", "text": "답"}, {"type": "thinking", "thinking": "속생각"},
                              {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}],
                "2026-11-01T09:00:05Z"),
            rec("user", [{"type": "tool_result", "content": "결과"}], "2026-11-01T09:00:06Z"),
            rec("user", "서브에이전트", "2026-11-01T09:00:07Z", isSidechain=True),
            rec("user", "메타", "2026-11-01T09:00:08Z", isMeta=True),
            rec("assistant", "합성", "2026-11-01T09:00:09Z") | {"message": {"model": "<synthetic>", "content": "x"}},
            rec("user", "<system-reminder>r</system-reminder>둘째 질문", "2026-11-02T01:00:00Z"),
            {"type": "summary", "summary": "요약"},
            rec("user", "시각 없음", None),
        ]
        body = "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n{깨진 줄\n[1]\n\n"
        (proj / "s1.jsonl").write_text(body, encoding="utf-8")
        (proj / "subagents" / "a.jsonl").write_text(json.dumps(rec("user", "숨은 파일")) + "\n", encoding="utf-8")
        (proj / "notes.txt").write_text("x", encoding="utf-8")

    def test_load_messages(self):
        stats = {}
        msgs = C.load_messages(self.root, stats=stats)
        self.assertEqual([(m.role, m.texts) for m in msgs],
                         [("user", ["첫 질문"]), ("assistant", ["답"]), ("user", ["둘째 질문"])])
        self.assertEqual(msgs[1].tools, [("Bash", {"command": "ls"})])
        self.assertEqual(msgs[0].session_id, "s1")
        self.assertEqual(msgs[0].cwd, "/w/p")
        self.assertEqual(stats["files"], 1)
        self.assertEqual(stats["malformed"], 2)
        self.assertEqual(stats["messages"], 3)

    def test_include_sidechain(self):
        msgs = C.load_messages(self.root, include_sidechain=True)
        self.assertIn(["서브에이전트"], [m.texts for m in msgs])
        self.assertTrue(any(m.sidechain for m in msgs))

    def test_since_until(self):
        since = datetime(2026, 11, 2, tzinfo=timezone.utc)
        self.assertEqual([m.text for m in C.load_messages(self.root, since=since)], ["둘째 질문"])
        until = datetime(2026, 11, 1, 9, 0, 1, tzinfo=timezone.utc)
        self.assertEqual([m.text for m in C.load_messages(self.root, until=until)], ["첫 질문"])

    def test_old_files_skipped_by_mtime(self):
        f = self.root / "-w-p" / "s1.jsonl"
        old = datetime(2020, 1, 1).timestamp()
        os.utime(f, (old, old))
        stats = {}
        self.assertEqual(C.load_messages(self.root, since=datetime(2026, 11, 1), stats=stats), [])
        self.assertEqual(stats["files"], 0)

    def test_single_file_and_missing(self):
        self.assertEqual(len(C.load_messages(self.root / "-w-p" / "s1.jsonl")), 3)
        self.assertEqual(C.load_messages(self.root / "없음"), [])

    def test_default_dir_env(self):
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.root)
        try:
            self.assertEqual(C.default_projects_dir(), self.root / "projects")
        finally:
            if old is None:
                del os.environ["CLAUDE_CONFIG_DIR"]
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old


if __name__ == "__main__":
    unittest.main()
