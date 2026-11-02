import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeOpener, NOW, World, jarvis_status
from mbrief.cli import main
from mbrief.sources import paper_butler


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.w = World(Path(self._tmp.name))
        self.w.config()
        self.cfg = str(self.w.root / "mbrief.json")

    def call(self, *argv, opener=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(list(argv), out, opener=opener or FakeOpener({"/api/status": jarvis_status()}),
                      clock=lambda: NOW, env={} if env is None else env)
        return rc, out.getvalue(), err.getvalue()

    def test_requires_command(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            main([], out=io.StringIO())

    def test_run_writes_files_and_prints_summary(self):
        rc, out, _ = self.call("run", "--config", self.cfg)
        self.assertEqual(rc, 0)
        self.assertIn("좋은 아침입니다. 11월 2일 월요일.", out)
        self.assertIn("paper_butler", out)
        names = {p.name for p in self.w.out.iterdir()}
        self.assertEqual(names, {"brief-2026-11-02.md", "brief-2026-11-02.json", "brief-latest.json",
                                 "cards-latest.json"})
        md = (self.w.out / "brief-2026-11-02.md").read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# 아침 브리핑 2026-11-02 (월)"))
        self.assertIn("## 어댑터 상태", md)

    def test_run_json_flag(self):
        rc, out, _ = self.call("run", "--config", self.cfg, "--json", "--dry-run")
        self.assertEqual(rc, 0)
        obj = json.loads(out.split("\n\n[dry-run]")[0])
        self.assertEqual(obj["date"], "2026-11-02")

    def test_date_option_uses_that_morning(self):
        rc, out, _ = self.call("run", "--config", self.cfg, "--date", "2026-11-04")
        self.assertEqual(rc, 0)
        self.assertTrue((self.w.out / "brief-2026-11-04.json").is_file())
        latest = json.loads((self.w.out / "brief-latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["date"], "2026-11-04")

    def test_bad_date_and_missing_config(self):
        rc, _, err = self.call("run", "--config", self.cfg, "--date", "내일")
        self.assertEqual(rc, 2)
        self.assertIn("YYYY-MM-DD", err)
        rc, _, err = self.call("run", "--config", str(self.w.root / "nope.json"))
        self.assertEqual(rc, 2)
        self.assertIn("설정 파일이 없어요", err)

    def test_speak_without_token_is_skipped_and_failure_exit_code(self):
        self.w.config(notify={"sidekick": {"token": ""}})
        rc, out, _ = self.call("run", "--config", self.cfg, "--speak")
        self.assertEqual(rc, 0)
        self.assertIn("말하기: 건너뜀", out)
        self.w.config()
        rc, out, _ = self.call("run", "--config", self.cfg, "--speak",
                               opener=FakeOpener({"/api/status": jarvis_status(), "/api/say": OSError("down")}))
        self.assertEqual(rc, 3)
        self.assertIn("말하기: 오류", out)

    def test_push_without_topic_is_skipped(self):
        self.w.config(notify={"ntfy": {"topic": ""}})
        rc, out, _ = self.call("run", "--config", self.cfg, "--push")
        self.assertEqual(rc, 0)
        self.assertIn("푸시: 건너뜀", out)

    def test_sources_table_and_exit_code(self):
        rc, out, _ = self.call("sources", "--config", self.cfg)
        self.assertEqual(rc, 0)
        self.assertIn("✅ 정상", out)
        with mock.patch.object(paper_butler, "collect", side_effect=ValueError("bad")):
            rc, out, _ = self.call("sources", "--config", self.cfg)
        self.assertEqual(rc, 1)
        self.assertIn("❌ 오류", out)

    def test_preview_seconds(self):
        rc, out, _ = self.call("preview", "--config", self.cfg, "--seconds", "120")
        self.assertEqual(rc, 0)
        self.assertIn("예산 120초 = 약 600자", out)
        self.assertIn("낭독", out)
        self.assertFalse(self.w.out.exists())             # 미리보기는 파일을 쓰지 않는다
        rc, out, _ = self.call("preview", "--config", self.cfg, "--seconds", "20")
        self.assertIn("초과", out)

    def test_check_command(self):
        rc, out, _ = self.call("check", "--config", self.cfg)
        self.assertEqual(rc, 1)
        self.assertIn("파일이 없어요", out)
        self.call("run", "--config", self.cfg)
        rc, out, _ = self.call("check", "--config", self.cfg)
        self.assertEqual(rc, 0)
        self.assertIn("brief-latest.json: 정상", out)
        self.assertIn("cards-latest.json: 정상", out)

    def test_config_from_env_and_defaults(self):
        rc, out, _ = self.call("sources", env={"MBRIEF_CONFIG": self.cfg})
        self.assertEqual(rc, 0)
        self.assertIn("paper_butler", out)


if __name__ == "__main__":
    unittest.main()
