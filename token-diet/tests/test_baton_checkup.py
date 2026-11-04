import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tokendiet import baton, checkup
from tokendiet.cli import main

from helpers import B


def fake_secret() -> str:
    return "sk-" + "ab12" * 10


def make_session(path: Path, sid="s1", cwd="/work/proj"):
    b = B(sid)
    b.user("로그인 기능을 만들어줘")
    b.call("Edit", {"file_path": "/work/proj/auth.py"})
    b.call("Write", {"file_path": "/work/proj/new.py"})
    b.call("Bash", {"command": "python -m unittest -v"}, "FAIL: test_login (tests.T)\n", error=True)
    b.user("아니 로그인 말고 회원가입으로 바꿔줘")
    b.recs.append({"type": "assistant", "isSidechain": False, "sessionId": sid, "uuid": "z", "timestamp": "2026-01-01T01:00:00.000Z",
                   "message": {"role": "assistant", "model": "m", "content": [
                       {"type": "text", "text": f"회원가입 구현을 마쳤습니다.\n남은 일: 이메일 검증 추가\nTODO 비밀번호 규칙 {fake_secret()}"}]}})
    for r in b.recs:
        r["cwd"] = cwd
    b.save(path)


class BatonTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        make_session(self.root / "-work-proj" / "s1.jsonl")

    def tearDown(self):
        self.td.cleanup()

    def test_content(self):
        info = baton.analyze(self.root / "-work-proj" / "s1.jsonl")
        text = baton.build_baton(info)
        self.assertIn("로그인 기능을 만들어줘", text)
        self.assertIn("auth.py", text)
        self.assertIn("남은 일: 이메일 검증", text)
        self.assertIn("test_login", text)
        self.assertIn("python -m unittest", text)
        self.assertIn("다음 세션 첫 메시지", text)
        self.assertIn("회원가입", text)          # 변경된 요구
        self.assertNotIn(fake_secret(), text)

    def test_cap(self):
        info = baton.analyze(self.root / "-work-proj" / "s1.jsonl")
        info["summaries"] = ["가" * 160] * 3
        info["opens"] = ["나" * 120] * 6
        text = baton.build_baton(info, cap=600)
        self.assertLessEqual(len(text), 600)
        self.assertIn("다음 세션 첫 메시지", text)

    def test_find(self):
        make_session(self.root / "-other" / "s2.jsonl", "s2", "/other")
        self.assertEqual(baton.find_session(self.root, session="s2").stem, "s2")
        self.assertIsNone(baton.find_session(self.root, session="nope"))
        self.assertEqual(baton.find_session(self.root, project="/other").stem, "s2")
        self.assertEqual(baton.find_session(self.root, project="/work/proj").stem, "s1")
        self.assertIsNotNone(baton.find_session(self.root, latest=True))

    def test_cli_out(self):
        out = io.StringIO()
        dest = self.root / "baton.md"
        self.assertEqual(main(["baton", "--logs", str(self.root), "--session", "s1", "--out", str(dest)], out), 0)
        self.assertIn("세션 바톤", dest.read_text(encoding="utf-8"))
        out = io.StringIO()
        self.assertEqual(main(["baton", "--logs", str(self.root), "--latest"], out), 0)
        self.assertIn("목표", out.getvalue())
        self.assertEqual(main(["baton", "--logs", str(self.root), "--session", "x"], io.StringIO()), 2)


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=t", *a],
                   check=True, capture_output=True)


class CheckupTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "m.py").write_text("# TODO: fix\nx = 1\n", encoding="utf-8")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_a.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n", encoding="utf-8")
        (self.repo / "big.bin").write_bytes(b"0" * 3000)

    def tearDown(self):
        self.td.cleanup()

    def test_report_without_run(self):
        md = checkup.build_report(self.repo, large_kb=1)
        text = __import__("tokendiet.report", fromlist=["x"]).to_markdown(md)
        self.assertIn("unittest discovery", text)
        self.assertIn("big.bin", text)
        self.assertIn("pkg", text)               # README 없는 폴더
        self.assertIn("⚠️", text)
        self.assertIn("1개", text)               # TODO

    def test_git_and_run(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        g = checkup.git_summary(self.repo)
        self.assertEqual(g["modified"], 0)
        self.assertGreaterEqual(g["untracked"], 1)
        ok, msg = checkup.run_tests(*checkup.detect_tests(self.repo)[:1], self.repo, 60)
        self.assertTrue(ok, msg)
        self.assertFalse(checkup.run_tests([sys.executable, "-c", "import time;time.sleep(5)"], self.repo, 1)[0])

    def test_not_git_and_cli_html(self):
        out = io.StringIO()
        md, html = self.repo / "o.md", self.repo / "o.html"
        self.assertEqual(main(["checkup", str(self.repo), "--md", str(md), "--html", str(html)], out), 0)
        h = html.read_text(encoding="utf-8")
        self.assertIn("<style>", h)
        self.assertNotIn("http", h)
        self.assertIn("건강검진표", md.read_text(encoding="utf-8"))
        self.assertEqual(main(["checkup", str(self.repo / "nope")], io.StringIO()), 2)

    def test_sync_absent(self):
        self.assertIsNone(checkup.sync_check(self.repo)[0])


if __name__ == "__main__":
    unittest.main()
