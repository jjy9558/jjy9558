import os
import sys
import tempfile
import unittest
from unittest import mock

from helpers import FakeLLM, config
from sidekick import debugger as dbg


class CommandRunnerTests(unittest.TestCase):
    def test_utterance_stays_single_argument(self):
        evil = '왜 안 돼"; rm -rf / && echo {0.__class__} $(whoami)'
        argv = dbg.build_argv(["python3", "-c", "x", "{prompt}", "--u={utterance}"], evil, "말: {utterance}")
        self.assertEqual(len(argv), 5)
        self.assertIn("rm -rf /", argv[3])  # 데이터로만 들어감
        self.assertIn("{0.__class__}", argv[3])  # format 해석 안 됨
        self.assertTrue(argv[4].startswith("--u="))

    def test_batch_files_strip_metacharacters(self):
        with mock.patch.object(dbg.shutil, "which", return_value=r"C:\npm\claude.cmd"):
            argv = dbg.build_argv(["claude", "-p", "{prompt}"], '왜 "안" 돼 & del * | %PATH%', "{utterance}")
        for ch in '"&|%':
            self.assertNotIn(ch, argv[2])

    def test_default_runner_never_uses_shell(self):
        with mock.patch.object(dbg.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            dbg.default_runner(["echo", "x"])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertIsInstance(run.call_args.args[0], list)

    def test_real_subprocess_receives_utterance_verbatim(self):
        evil = "왜 안 돼; echo PWNED `id`"
        argv = dbg.build_argv([sys.executable, "-c", "import sys;print(sys.argv[1])", "{utterance}"], evil, "")
        code, out = dbg.default_runner(argv, timeout=30)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), evil)

    def test_command_mode_uses_runner_and_cwd(self):
        seen = {}

        def runner(argv, cwd=None, timeout=None):
            seen.update(argv=argv, cwd=cwd)
            return 0, "  NullPointer 때문에 테스트 3개 실패.  \n"
        cfg = config(debugger={"mode": "command", "command": ["claude", "-p", "{prompt}"], "cwd": "/proj"})["debugger"]
        d = dbg.Debugger(cfg, runner=runner)
        self.assertEqual(d.investigate("왜 안 돼"), "NullPointer 때문에 테스트 3개 실패.")
        self.assertEqual(seen["cwd"], "/proj")
        self.assertIn("왜 안 돼", seen["argv"][2])

    def test_string_command_in_config_is_split(self):
        cfg = config(debugger={"command": 'claude -p "{prompt}"'})
        self.assertEqual(cfg["debugger"]["command"], ["claude", "-p", "{prompt}"])


class LogTailTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".log")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("ok start\nINFO fine\nERROR db connection refused\nTraceback (most recent call last)\nINFO done\n")

    def tearDown(self):
        os.unlink(self.path)

    def test_tail_errors(self):
        hits = dbg.tail_errors(self.path, 100, config()["debugger"]["error_pattern"])
        self.assertEqual(hits, ["ERROR db connection refused", "Traceback (most recent call last)"])

    def test_summary_without_llm(self):
        d = dbg.Debugger(config(debugger={"log_file": self.path})["debugger"])
        self.assertIn("Traceback", d.investigate("왜 안 돼"))

    def test_summary_with_llm_and_fallback(self):
        cfg = config(debugger={"log_file": self.path})["debugger"]
        self.assertEqual(dbg.Debugger(cfg, llm=FakeLLM("DB가 꺼져 있어요.")).investigate("x"), "DB가 꺼져 있어요.")
        self.assertIn("최근 에러 2줄", dbg.Debugger(cfg, llm=FakeLLM(fail=True)).investigate("x"))

    def test_missing_log(self):
        d = dbg.Debugger(config()["debugger"])
        self.assertIn("log_file", d.investigate("x"))


if __name__ == "__main__":
    unittest.main()
