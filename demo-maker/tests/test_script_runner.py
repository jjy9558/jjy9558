import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from demomaker import runner
from demomaker.runner import DemoError, Sandbox, build_env, expand, record, resolve_argv
from demomaker.script import Script, ScriptError

PY = sys.executable


def make_script(steps, **kw):
    d = dict(title="t", steps=steps)
    d.update(kw)
    return Script(d, tempfile.gettempdir())


class ScriptTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        s = make_script([{"type": "cmd", "run": "echo hi"}])
        self.assertEqual((s.width, s.height), (80, 24))
        with self.assertRaises(ScriptError):
            make_script([])
        with self.assertRaises(ScriptError):
            make_script([{"type": "nope"}])
        with self.assertRaises(ScriptError):
            make_script([{"type": "cmd"}])
        with self.assertRaises(ScriptError):
            make_script([{"type": "cmd", "run": "x", "typing_speed": -1}])
        with self.assertRaises(ScriptError):
            make_script([{"type": "cmd", "run": "x"}], width=5)

    def test_setup_path_must_stay_inside_sandbox(self):
        for bad in ("../x", "/etc/passwd", "a/../../x"):
            with self.assertRaises(ScriptError, msg=bad):
                make_script([{"type": "setup", "files": {bad: "x"}}])

    def test_load_json_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"title": "한글", "steps": [{"type": "pause", "seconds": 1}]}, f, ensure_ascii=False)
            self.assertEqual(Script.load(p).title, "한글")
            with self.assertRaises(ScriptError):
                Script.load(os.path.join(d, "missing.json"))


class SandboxTests(unittest.TestCase):
    def test_env_overrides_home_and_appdata(self):
        with Sandbox() as sb:
            env = build_env(sb, {"HOME": "/real/home", "FOO": "1"}, 100,
                            base={"HOME": "/real/home", "APPDATA": "/real/appdata", "PATH": "/bin",
                                  "GITHUB_TOKEN": "s3cret", "MY_API_KEY": "k"})
            for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "TMPDIR"):
                self.assertTrue(env[k].startswith(sb.root), (k, env[k]))
            self.assertEqual(env["FOO"], "1")
            self.assertEqual(env["PATH"], "/bin")
            self.assertEqual(env["COLUMNS"], "100")
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("MY_API_KEY", env)
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
            root = sb.root
        self.assertFalse(os.path.exists(root))

    def test_recorded_command_sees_sandbox_home(self):
        code = "import os;print(os.environ['HOME']);print(os.environ['APPDATA']);print(os.path.expanduser('~'))"
        real_home = os.path.expanduser("~")
        rec = record(make_script([{"type": "cmd", "run": 'python -c "%s"' % code}]))
        lines = "".join(c[1] for c in rec.steps[0]["output"]).split()
        self.assertEqual(len(lines), 3)
        for l in lines:
            self.assertIn("demomaker-", l)
            self.assertNotEqual(l, real_home)
        self.assertFalse(os.path.exists(lines[0]))   # 끝나면 샌드박스 삭제

    def test_script_env_is_merged_and_placeholders_expanded(self):
        code = "import os;print(os.environ['X_DIR'])"
        rec = record(make_script([{"type": "cmd", "run": 'python -c "%s"' % code}], env={"X_DIR": "{{home}}/data"}))
        out = "".join(c[1] for c in rec.steps[0]["output"]).strip()
        self.assertTrue(out.replace("\\", "/").endswith("/data") and "demomaker-" in out)

    def test_setup_writes_json_with_relative_dates(self):
        code = "import json,os;print(json.load(open(os.path.join(os.environ['HOME'],'a','b.json'),encoding='utf-8')))"
        steps = [
            {"type": "setup", "files": {"home/a/b.json": {"이름": "값", "when": "{{days_ago:1}}"}}},
            {"type": "cmd", "run": 'python -c "%s"' % code},
        ]
        rec = record(make_script(steps), now=__import__("datetime").datetime(2026, 11, 2, 12, 0, 0))
        out = "".join(c[1] for c in rec.steps[0]["output"])
        self.assertIn("'이름': '값'", out)
        self.assertIn("2026-11-01T12:00:00", out)
        self.assertEqual(len(rec.steps), 1)   # setup 은 화면에 안 나옴

    def test_hidden_command_runs_but_is_not_recorded(self):
        steps = [{"type": "cmd", "run": 'python -c "print(1)"', "hidden": True},
                 {"type": "cmd", "run": 'python -c "print(2)"'}]
        rec = record(make_script(steps))
        self.assertEqual(len(rec.steps), 1)


class ShellFalseTests(unittest.TestCase):
    def test_popen_called_with_list_and_shell_false(self):
        real = subprocess.Popen
        seen = {}

        def spy(*a, **kw):
            seen["args"], seen["kw"] = a, kw
            return real(*a, **kw)

        with mock.patch.object(runner.subprocess, "Popen", side_effect=spy):
            record(make_script([{"type": "cmd", "run": 'python -c "print(1)"'}]))
        self.assertIsInstance(seen["args"][0], list)
        self.assertIs(seen["kw"]["shell"], False)
        self.assertEqual(seen["args"][0][0], sys.executable)
        self.assertIn("demomaker-", seen["kw"]["env"]["HOME"])

    def test_shell_metacharacters_are_literal(self):
        code = "import sys;print(sys.argv[1:])"
        marker = os.path.join(tempfile.gettempdir(), "demomaker-should-not-exist-%d" % os.getpid())
        run = "python -c \"%s\" 'a; touch %s' '$HOME' '`id`' '*'" % (code, marker)
        rec = record(make_script([{"type": "cmd", "run": run}]))
        out = "".join(c[1] for c in rec.steps[0]["output"])
        self.assertIn("$HOME", out)
        self.assertIn("`id`", out)
        self.assertFalse(os.path.exists(marker))

    def test_resolve_argv_uses_shlex_and_current_python(self):
        argv = resolve_argv('python3 -m x --name "a b"', {"now": None})
        self.assertEqual(argv, [sys.executable, "-m", "x", "--name", "a b"])
        with self.assertRaises(DemoError):
            resolve_argv('echo "unterminated', {})


class RunnerBehaviour(unittest.TestCase):
    def test_timeout(self):
        s = make_script([{"type": "cmd", "run": 'python -c "import time;time.sleep(5)"', "timeout": 0.5}])
        with self.assertRaises(DemoError):
            record(s)

    def test_nonzero_exit_fails_unless_allowed(self):
        run = 'python -c "import sys;print(\'bad\');sys.exit(3)"'
        with self.assertRaises(DemoError):
            record(make_script([{"type": "cmd", "run": run}]))
        rec = record(make_script([{"type": "cmd", "run": run, "allow_fail": True}]))
        self.assertEqual(rec.steps[0]["exit_code"], 3)

    def test_missing_program(self):
        with self.assertRaises(DemoError):
            record(make_script([{"type": "cmd", "run": "definitely-not-a-program-xyz"}]))

    def test_stdout_stderr_and_timing_captured_in_order(self):
        code = ("import sys,time;print('out1',flush=True);time.sleep(0.3);"
                "print('err1',file=sys.stderr,flush=True);time.sleep(0.3);print('out2')")
        rec = record(make_script([{"type": "cmd", "run": 'python -c "%s"' % code}]))
        chunks = rec.steps[0]["output"]
        text = "".join(c[1] for c in chunks)
        self.assertEqual(text.split(), ["out1", "err1", "out2"])
        times = [c[0] for c in chunks]
        self.assertEqual(times, sorted(times))
        self.assertGreater(times[-1] - times[0], 0.3)

    def test_utf8_output_roundtrip(self):
        rec = record(make_script([{"type": "cmd", "run": 'python -c "print(\'한글 🎮\')"'}]))
        self.assertIn("한글 🎮", "".join(c[1] for c in rec.steps[0]["output"]))

    def test_unknown_placeholder(self):
        with self.assertRaises(DemoError):
            expand("{{nope}}", {"now": None})

    def test_cwd_defaults_to_project_and_can_be_work(self):
        with tempfile.TemporaryDirectory() as d:
            s = Script({"title": "t", "project": ".", "cwd": "{{work}}",
                        "steps": [{"type": "cmd", "run": 'python -c "import os;print(os.getcwd())"'}]}, d)
            out = "".join(c[1] for c in record(s).steps[0]["output"]).strip()
            self.assertTrue(out.endswith("work"))
            s = Script({"title": "t", "project": ".",
                        "steps": [{"type": "cmd", "run": 'python -c "import os;print(os.getcwd())"'}]}, d)
            out = "".join(c[1] for c in record(s).steps[0]["output"]).strip()
            self.assertEqual(os.path.realpath(out), os.path.realpath(d))


if __name__ == "__main__":
    unittest.main()
