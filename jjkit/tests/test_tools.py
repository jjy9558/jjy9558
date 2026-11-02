"""tools/jjkit_sync.py 와 tools/new_project.py 테스트 (임시 폴더에서만, 저장소는 건드리지 않음)."""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

JJKIT = Path(__file__).resolve().parents[1]
TOOLS = JJKIT.parent / "tools"
SRC = JJKIT / "jjkit"


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless((TOOLS / "jjkit_sync.py").exists(), "tools/ 폴더가 없음 (jjkit만 따로 복사됨)")
class SyncTests(unittest.TestCase):
    def setUp(self):
        self.S = load("jjkit_sync")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "jjkit" / "jjkit"
        shutil.copytree(SRC, self.src, ignore=shutil.ignore_patterns("__pycache__"))
        self.proj = self.root / "demo-proj"
        (self.proj / "dpkg").mkdir(parents=True)
        (self.proj / "dpkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.proj / "tests").mkdir()
        (self.proj / "tests" / "__init__.py").write_text("", encoding="utf-8")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.S.main(list(args) + ["--root", str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def test_sync_with_deps_and_header(self):
        changed = self.S.sync(self.proj, "dpkg", ["llm"], self.src)
        cd = self.proj / "dpkg" / "_jjkit"
        self.assertEqual(sorted(p.name for p in cd.glob("*.py")), ["__init__.py", "llm.py", "netutil.py"])
        self.assertEqual(len(changed), 3)
        text = (cd / "llm.py").read_text(encoding="utf-8")
        sha = self.S.sha8((self.src / "llm.py").read_bytes())
        self.assertTrue(text.startswith("# generated from jjkit@%s; 직접 고치지 말 것\n" % sha))
        self.assertIn('MODULES = ("llm", "netutil",)', (cd / "__init__.py").read_text(encoding="utf-8"))
        self.assertEqual(self.S.check_project(self.proj, self.src), [])
        self.assertEqual(self.S.sync(self.proj, "dpkg", ["llm"], self.src), [])  # 두 번째는 변화 없음

    def test_copy_is_importable(self):
        self.S.sync(self.proj, "dpkg", ["textwidth", "notify"], self.src)
        code = ("from dpkg._jjkit import textwidth, notify; "
                "print(textwidth.width('한글'), notify.encode_header('a'))")
        r = subprocess.run([sys.executable, "-c", code], cwd=self.proj, capture_output=True, text=True,
                           encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "4 a")

    def test_detects_hand_edit_and_stale(self):
        self.S.sync(self.proj, "dpkg", ["textwidth"], self.src)
        f = self.proj / "dpkg" / "_jjkit" / "textwidth.py"
        original = f.read_text(encoding="utf-8")
        f.write_text(original + "\n# 손으로 고침\n", encoding="utf-8")
        probs = self.S.check_project(self.proj, self.src)
        self.assertTrue(any("직접 고쳤" in p for p in probs), probs)
        f.write_text(original, encoding="utf-8")
        with open(self.src / "textwidth.py", "a", encoding="utf-8") as fh:
            fh.write("\n# 원본 변경\n")
        probs = self.S.check_project(self.proj, self.src)
        self.assertTrue(any("낡았" in p for p in probs), probs)
        self.S.sync(self.proj, None, None, self.src)  # 패키지 자동 탐지, 있던 모듈만 갱신
        self.assertEqual(self.S.check_project(self.proj, self.src), [])

    def test_crlf_checkout_still_passes(self):
        self.S.sync(self.proj, "dpkg", ["ics"], self.src)
        for p in (self.proj / "dpkg" / "_jjkit").glob("*.py"):
            p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.S.check_project(self.proj, self.src), [])

    def test_union_and_unknown(self):
        self.S.sync(self.proj, "dpkg", ["ics"], self.src)
        self.S.sync(self.proj, "dpkg", ["redact"], self.src)
        self.assertEqual(self.S.copied_modules(self.proj / "dpkg" / "_jjkit"), ["ics", "redact"])
        with self.assertRaises(self.S.SyncError):
            self.S.sync(self.proj, "dpkg", ["nope"], self.src)

    def test_missing_dep_and_removed_module(self):
        self.S.sync(self.proj, "dpkg", ["notify"], self.src)
        cd = self.proj / "dpkg" / "_jjkit"
        (cd / "netutil.py").unlink()
        probs = self.S.check_project(self.proj, self.src)
        self.assertTrue(any("netutil.py 가 없습니다" in p for p in probs), probs)
        self.S.sync(self.proj, "dpkg", None, self.src)
        (self.src / "ics.py").write_text("", encoding="utf-8")
        self.S.sync(self.proj, "dpkg", ["ics"], self.src)
        (self.src / "ics.py").unlink()
        probs = self.S.check_project(self.proj, self.src)
        self.assertTrue(any("더 이상 없는" in p for p in probs), probs)

    def test_find_package(self):
        self.assertEqual(self.S.find_package(self.proj), "dpkg")
        (self.proj / "other").mkdir()
        (self.proj / "other" / "__init__.py").write_text("", encoding="utf-8")
        with self.assertRaises(self.S.SyncError):
            self.S.find_package(self.proj)

    def test_check_all_vacuous_and_failing(self):
        self.assertEqual(self.S.check_all(self.root, self.src), (0, []))
        code, out, _ = self.cli("--check-all")
        self.assertEqual(code, 0)
        self.assertIn("통과 (0곳)", out)
        self.S.sync(self.proj, "dpkg", ["textwidth"], self.src)
        self.assertEqual(self.cli("--check-all")[0], 0)
        (self.proj / "dpkg" / "_jjkit" / "textwidth.py").write_text("# 헤더 없음\n", encoding="utf-8")
        code, _, err = self.cli("--check-all")
        self.assertEqual(code, 1)
        self.assertIn("헤더가 없습니다", err)

    def test_cli_sync_check_list(self):
        code, out, _ = self.cli("demo-proj", "--modules", "textwidth")
        self.assertEqual(code, 0, out)
        self.assertIn("갱신:", out)
        self.assertEqual(self.cli("--check", "demo-proj")[0], 0)
        code, out, _ = self.cli("--list")
        self.assertIn("llm", out)
        self.assertIn("의존: netutil", out)
        self.assertIn("복사본: demo-proj", out)
        self.assertEqual(self.cli("없는-프로젝트")[0], 2)
        self.assertEqual(self.cli("demo-proj", "--modules", "nope")[0], 2)

    def test_real_script_runs(self):
        r = subprocess.run([sys.executable, str(TOOLS / "jjkit_sync.py"), "--check-all",
                            "--root", str(self.root)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless((TOOLS / "new_project.py").exists(), "tools/ 폴더가 없음")
class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.N = load("new_project")
        self.S = load("jjkit_sync")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_create_runs_and_passes(self):
        proj = self.N.create("hello-box", "hbox", "인사 상자", ["textwidth", "llm"], self.root, SRC)
        for rel in ("pyproject.toml", "README.md", "docs/demo.json", "hbox/__init__.py", "hbox/__main__.py",
                    "hbox/cli.py", "tests/__init__.py", "tests/test_cli.py", "hbox/_jjkit/llm.py",
                    "hbox/_jjkit/netutil.py", "hbox/_jjkit/textwidth.py"):
            self.assertTrue((proj / rel).exists(), rel)
        py = (proj / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.12"', py)
        self.assertIn('hello-box = "hbox.cli:main"', py)
        readme = (proj / "README.md").read_text(encoding="utf-8")
        self.assertIn("python -m hbox hello", readme)
        self.assertIn("from ._jjkit import", readme)
        self.assertEqual(self.S.check_project(proj, SRC), [])
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=proj,
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, "-m", "hbox", "hello", "진영"], cwd=proj, capture_output=True,
                           text=True, encoding="utf-8", env=env)
        self.assertEqual(r.stdout.strip(), "안녕하세요, 진영!")
        r = subprocess.run([sys.executable, "-m", "hbox", "--version"], cwd=proj, capture_output=True,
                           text=True, encoding="utf-8", env=env)
        self.assertIn("0.1.0", r.stdout)

    def test_without_modules_and_default_pkg(self):
        proj = self.N.create("plain-tool", root=self.root, src=SRC)
        self.assertTrue((proj / "plain_tool" / "cli.py").exists())
        self.assertFalse((proj / "plain_tool" / "_jjkit").exists())
        self.assertIn("jjkit_sync.py plain-tool", (proj / "README.md").read_text(encoding="utf-8"))

    def test_refuses_bad_or_existing(self):
        self.N.create("dup", root=self.root, src=SRC)
        with self.assertRaises(FileExistsError):
            self.N.create("dup", root=self.root, src=SRC)
        for bad in ("Bad", "-x", "a_b", ""):
            with self.assertRaises(ValueError):
                self.N.create(bad, root=self.root, src=SRC)
        with self.assertRaises(ValueError):
            self.N.create("ok-name", "class", root=self.root, src=SRC)
        self.assertFalse((self.root / "ok-name").exists())

    def test_unknown_module_creates_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.N.main(["ghost", "--modules", "nope", "--root", str(self.root)])
        self.assertEqual(code, 2)
        self.assertFalse((self.root / "ghost").exists())

    def test_cli_main(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.N.main(["cli-made", "--pkg", "cmade", "--desc", "설명", "--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("python -m cmade hello", out.getvalue())


if __name__ == "__main__":
    unittest.main()
