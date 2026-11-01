# -*- coding: utf-8 -*-
import contextlib
import io
import json
import os
import tempfile
import unittest

import helpers as H
from til_drafter.cli import main


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "projects")
        self.out = os.path.join(self._tmp.name, "out")
        self.cfg = os.path.join(self._tmp.name, "none.json")
        self.tok = "sk-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3"
        H.write_jsonl(os.path.join(self.root, "p", "s.jsonl"), [
            H.user("2026-03-10T01:00:00Z", "토큰 %s 로 CompanyX 배포" % self.tok, cwd="/w/proj"),
            H.assistant("2026-03-10T01:01:00Z", "배포 완료, 원인은 설정 누락이었다.", cwd="/w/proj"),
        ])

    def args(self, *extra):
        return ["--projects-dir", self.root, "--out", self.out, "--tz-offset", "0",
                "--config", self.cfg] + list(extra)

    def test_writes_file_and_prints_path(self):
        with open(self.cfg, "w") as fh:
            fh.write("{}")
        code, out, _ = run(self.args("--date", "2026-03-10"))
        self.assertEqual(code, 0)
        path = os.path.join(self.out, "2026-03-10.md")
        self.assertEqual(out.strip(), path)
        text = read(path)
        self.assertIn("## proj", text)
        self.assertNotIn(self.tok, text)

    def test_check_mode_prints_counts_not_secrets_and_writes_nothing(self):
        with open(self.cfg, "w") as fh:
            fh.write("{}")
        code, out, _ = run(self.args("--date", "2026-03-10", "--check", "--redact-word", "CompanyX"))
        self.assertEqual(code, 0)
        self.assertIn("api_key", out)
        self.assertIn("custom_word", out)
        self.assertNotIn(self.tok, out)
        self.assertNotIn("CompanyX", out)
        self.assertFalse(os.path.exists(self.out))

    def test_config_file_words_and_cli_words_combined(self):
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"redact_words": ["CompanyX"]}, fh)
        code, out, _ = run(self.args("--date", "2026-03-10", "--redact-word", "설정,누락"))
        self.assertEqual(code, 0)
        text = read(os.path.join(self.out, "2026-03-10.md"))
        for w in ("CompanyX", "설정", "누락"):
            self.assertNotIn(w, text)

    def test_broken_config_aborts(self):
        with open(self.cfg, "w") as fh:
            fh.write("{bad json")
        code, _, err = run(self.args("--date", "2026-03-10"))
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.out))

    def test_missing_explicit_config_aborts(self):
        code, _, _ = run(self.args("--date", "2026-03-10"))  # cfg 파일 없음 + 명시 지정
        self.assertEqual(code, 2)

    def test_invalid_date_and_tz(self):
        with open(self.cfg, "w") as fh:
            fh.write("{}")
        self.assertEqual(run(self.args("--date", "10-03-2026"))[0], 2)
        self.assertEqual(run(["--projects-dir", self.root, "--config", self.cfg, "--tz-offset", "xx"])[0], 2)

    def test_no_entries_writes_nothing(self):
        with open(self.cfg, "w") as fh:
            fh.write("{}")
        code, out, _ = run(self.args("--date", "2020-01-01"))
        self.assertEqual(code, 0)
        self.assertIn("기록이 없어", out)
        self.assertFalse(os.path.exists(self.out))

    def test_missing_projects_dir(self):
        with open(self.cfg, "w") as fh:
            fh.write("{}")
        code, _, err = run(["--projects-dir", os.path.join(self._tmp.name, "nope"), "--config", self.cfg])
        self.assertEqual(code, 1)

    def test_rerun_keeps_previous_draft_as_backup_when_changed(self):
        with open(self.cfg, "w") as fh:
            fh.write("{}")
        run(self.args("--date", "2026-03-10"))
        path = os.path.join(self.out, "2026-03-10.md")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n내가 손본 내용\n")
        run(self.args("--date", "2026-03-10"))
        self.assertIn("내가 손본 내용", read(os.path.join(self.out, "2026-03-10.bak.md")))
        self.assertNotIn("내가 손본 내용", read(path))

    def test_date_keywords(self):
        from datetime import timedelta, timezone
        from til_drafter.transcripts import local_today
        with open(self.cfg, "w") as fh:
            fh.write("{}")
        code, out, _ = run(self.args("--date", "yesterday", "--check"))
        self.assertEqual(code, 0)
        self.assertIn(str(local_today(timezone.utc) - timedelta(days=1)), out)
        code, out, _ = run(self.args("--date", "today", "--check"))
        self.assertIn(local_today(timezone.utc).isoformat(), out)

    def test_runs_as_module(self):
        import subprocess, sys
        with open(self.cfg, "w") as fh:
            fh.write("{}")
        res = subprocess.run([sys.executable, "-m", "til_drafter"] + self.args("--date", "2026-03-10", "--check"),
                             cwd=H.ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("[check]", res.stdout)


if __name__ == "__main__":
    unittest.main()
