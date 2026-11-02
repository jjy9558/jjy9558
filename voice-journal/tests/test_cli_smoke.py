# -*- coding: utf-8 -*-
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta

from vjournal.cli import main
from tests.helpers import msg, write_jsonl


class CliSmoke(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "j.db")
        self.now = lambda: datetime(2026, 11, 6, 22, 0)

    def run_cli(self, *argv, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--db", self.db] + list(argv), now=self.now, **kw)
        return rc, buf.getvalue()

    def test_flow(self):
        self.run_cli("add", "이번 주에 블로그 2개 쓴다 #다짐")
        self.run_cli("add", "블로그는 시간이 없어서 접었다")
        rc, out = self.run_cli("why", "블로그")
        self.assertIn("접었", out)
        rc, out = self.run_cli("search", "블로그")
        self.assertIn("#1", out)
        html = os.path.join(self.dir, "r.html")
        md = os.path.join(self.dir, "r.md")
        rc, out = self.run_cli("report", "--html", html, "--md", md)
        self.assertEqual(rc, 0)
        self.assertTrue(open(html, encoding="utf-8").read().startswith("<!doctype html>"))
        self.assertIn("주간 성적표", open(md, encoding="utf-8").read())
        rc, out = self.run_cli("goals")
        self.assertIn("dropped", out)
        rc, out = self.run_cli("export", "2026-11", "--out", os.path.join(self.dir, "m.md"))
        self.assertEqual(rc, 0)
        rc, out = self.run_cli("goals", "done", "99")
        self.assertEqual(rc, 1)

    def test_claude_command(self):
        pdir = os.path.join(self.dir, "projects")
        ts = (datetime.now().astimezone() - timedelta(hours=2)).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        ts = ts[:-2] + ":" + ts[-2:]
        write_jsonl(os.path.join(pdir, "p", "s.jsonl"), [
            msg("user", ts, "야간 작업 시켰다", cwd="/x/nightproj"),
            msg("assistant", ts, [{"type": "text", "text": "다 했습니다."}], cwd="/x/nightproj")])
        rc, out = self.run_cli("claude", "--projects-dir", pdir, "--save")
        self.assertEqual(rc, 0)
        self.assertIn("nightproj", out)
        rc, out = self.run_cli("list")
        self.assertIn("claude", out)


if __name__ == "__main__":
    unittest.main()
