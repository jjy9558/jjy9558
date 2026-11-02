import io
import json
import os
import tempfile
import unittest
import urllib.error

import helpers  # noqa: F401
from jarvis.config import build_worker_config
from jarvis.handlers import HandlerError, PermanentError, build_handlers
from jarvis.handlers.base import default_runner
from jarvis.handlers.claude import ClaudeTaskHandler, needs_bat_sanitize, render_command
from jarvis.handlers.llm import LlmChatHandler
from jarvis.handlers.notify import NotifySayHandler
from jarvis.handlers.shell import ShellAllowedHandler


class FakeRunner:
    def __init__(self, code=0, out="ok", err=""):
        self.calls = []
        self.ret = (code, out, err)

    def __call__(self, argv, cwd=None, timeout=60, env=None):
        self.calls.append({"argv": list(argv), "cwd": cwd, "timeout": timeout})
        return self.ret


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def wcfg(**handlers):
    return build_worker_config({"device": "main", "token": "x" * 20, "handlers": handlers})


class RegistryTest(unittest.TestCase):
    def test_defaults_register_nothing(self):
        self.assertEqual(build_handlers(wcfg()), {})

    def test_claude_disabled_by_default(self):
        cfg = wcfg(**{"claude.task": {"repos": {"default": "/tmp"}}})
        self.assertFalse(cfg["handlers"]["claude.task"]["enabled"])
        self.assertNotIn("claude.task", build_handlers(cfg))
        cfg = wcfg(**{"claude.task": {"enabled": True, "repos": {"default": "/tmp"}}})
        self.assertIn("claude.task", build_handlers(cfg))

    def test_enabled_handlers(self):
        h = build_handlers(wcfg(**{"llm.chat": {"base_url": "http://x/v1"},
                                   "shell.allowed": {"commands": {"a": {"argv": ["echo"]}}},
                                   "notify.say": {"enabled": True}, "wake": {"enabled": True}}))
        self.assertEqual(sorted(h), ["llm.chat", "notify.say", "shell.allowed", "wake"])


class ShellTest(unittest.TestCase):
    CMDS = {
        "git_status": {"argv": ["git", "status", "--short"], "cwd": "/repo", "timeout": 30},
        "backup": {"argv": ["robocopy", "C:/work", "{target}", "/MIR"],
                   "params": {"target": {"choices": ["D:/backup", "E:/backup"]}}},
        "tail": {"argv": ["tail", "-n", "{n}", "/var/log/app.log"], "params": {"n": {"pattern": r"[0-9]{1,4}", "default": "50"}}},
        "bad": {"argv": ["echo", "{x}"]},
    }

    def setUp(self):
        self.r = FakeRunner()
        self.h = ShellAllowedHandler({"commands": self.CMDS}, runner=self.r)

    def run_job(self, payload):
        return self.h.run({"payload": payload})

    def test_rejects_unknown_names(self):
        for name in ("rm", "git status", "git_status; rm -rf /", "", None, 5, "../git_status"):
            with self.assertRaises(PermanentError, msg=name):
                self.run_job({"name": name})
        self.assertEqual(self.r.calls, [])

    def test_runs_named_command_as_argv(self):
        res = self.run_job({"name": "git_status"})
        self.assertEqual(self.r.calls[0], {"argv": ["git", "status", "--short"], "cwd": "/repo", "timeout": 30})
        self.assertEqual(res["returncode"], 0)

    def test_choices_param(self):
        self.run_job({"name": "backup", "args": {"target": "E:/backup"}})
        self.assertEqual(self.r.calls[0]["argv"], ["robocopy", "C:/work", "E:/backup", "/MIR"])
        for args in ({"target": "C:/Windows"}, {"target": "D:/backup & del C:"}, {}, {"target": "D:/backup", "x": 1},
                     {"target": ["D:/backup"]}):
            with self.assertRaises(PermanentError, msg=args):
                self.run_job({"name": "backup", "args": args})

    def test_pattern_param_and_default(self):
        self.run_job({"name": "tail", "args": {"n": "200"}})
        self.run_job({"name": "tail"})
        self.assertEqual([c["argv"][2] for c in self.r.calls], ["200", "50"])
        for bad in ("200; rm -rf /", "-1", "12345", "$(id)"):
            with self.assertRaises(PermanentError, msg=bad):
                self.run_job({"name": "tail", "args": {"n": bad}})

    def test_param_without_rule_rejected(self):
        with self.assertRaises(PermanentError):
            self.run_job({"name": "bad", "args": {"x": "hi"}})

    def test_inline_placeholder_rejected(self):
        h = ShellAllowedHandler({"commands": {"x": {"argv": ["echo", "--to={t}"],
                                                    "params": {"t": {"choices": ["a"]}}}}}, runner=self.r)
        with self.assertRaises(PermanentError):
            h.run({"payload": {"name": "x", "args": {"t": "a"}}})

    def test_nonzero_exit_is_failure(self):
        h = ShellAllowedHandler({"commands": self.CMDS}, runner=FakeRunner(code=2, err="fatal"))
        with self.assertRaises(PermanentError):
            h.run({"payload": {"name": "git_status"}})

    def test_default_runner_real_process_no_shell(self):
        import sys
        code, out, err = default_runner([sys.executable, "-c", "import sys; print(sys.argv[1])", "a; echo b && $(id)"])
        self.assertEqual((code, out.strip()), (0, "a; echo b && $(id)"))
        with self.assertRaises(PermanentError):
            default_runner("echo hi")
        with self.assertRaises(PermanentError):
            default_runner(["/definitely/not/here-xyz"])


class ClaudeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = FakeRunner(out="고쳤어요: README.md 3줄")
        self.cfg = {"enabled": True, "command": ["claude", "-p", "{prompt}", "--output-format", "text"],
                    "repos": {"default": self.tmp.name}, "timeout": 99}

    def tearDown(self):
        self.tmp.cleanup()

    def test_disabled_handler_refuses(self):
        h = ClaudeTaskHandler(dict(self.cfg, enabled=False), runner=self.r)
        with self.assertRaises(PermanentError):
            h.run({"payload": {"prompt": "hi"}})
        self.assertEqual(self.r.calls, [])

    def test_prompt_passed_as_single_argument(self):
        prompt = 'README "오타" 고쳐줘; rm -rf / && echo $(whoami) `id` | tee x'
        h = ClaudeTaskHandler(self.cfg, runner=self.r)
        with unittest.mock.patch("os.name", "posix"):
            res = h.run({"payload": {"prompt": prompt}})
        call = self.r.calls[0]
        self.assertEqual(call["argv"], ["claude", "-p", prompt, "--output-format", "text"])
        self.assertEqual(call["cwd"], self.tmp.name)
        self.assertEqual(call["timeout"], 99)
        self.assertEqual(res["text"], "고쳤어요: README.md 3줄")

    def test_repo_must_be_configured_name(self):
        h = ClaudeTaskHandler(self.cfg, runner=self.r)
        for repo in ("/etc", "../x", "other"):
            with self.assertRaises(PermanentError):
                h.run({"payload": {"prompt": "hi", "repo": repo}})
        for p in ("", "   ", None, "x" * 9000):
            with self.assertRaises(PermanentError):
                h.run({"payload": {"prompt": p}})

    def test_bat_sanitize(self):
        self.assertTrue(needs_bat_sanitize("claude.cmd", is_windows=False))
        self.assertTrue(needs_bat_sanitize("claude", is_windows=True))
        self.assertFalse(needs_bat_sanitize("C:/node/node.exe", is_windows=True))
        self.assertFalse(needs_bat_sanitize("claude", is_windows=False))
        self.assertEqual(render_command(["claude.cmd", "-p", "{prompt}"], 'a "b" & c|d %X%', is_windows=True),
                         ["claude.cmd", "-p", "a b  cd X"])
        with self.assertRaises(PermanentError):
            render_command(["claude", "-p"], "x")

    def test_worker_config_requires_placeholder_when_enabled(self):
        from jarvis.config import ConfigError
        with self.assertRaises(ConfigError):
            wcfg(**{"claude.task": {"enabled": True, "command": ["claude", "-p", "prompt: {prompt}"]}})


class LlmTest(unittest.TestCase):
    def setUp(self):
        self.reqs = []
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def opener(self, reply="안녕하세요", status=None):
        def _open(req, timeout=None):
            self.reqs.append(req)
            if status:
                raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(b""))
            return Resp(json.dumps({"model": "qwen", "choices": [{"message": {"content": reply}}]}).encode())
        return _open

    def h(self, **kw):
        cfg = dict({"base_url": "http://127.0.0.1:8080/v1/", "model": "local", "files_dir": self.tmp.name,
                    "max_file_chars": 1000}, **kw)
        return LlmChatHandler(cfg, runner=FakeRunner(out="PDF 본문 텍스트"), opener=self.opener())

    def test_chat_request(self):
        r = self.h().run({"payload": {"prompt": "안녕?"}})
        req = self.reqs[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:8080/v1/chat/completions")
        body = json.loads(req.data)
        self.assertEqual(body["model"], "local")
        self.assertEqual(body["messages"][-1], {"role": "user", "content": "안녕?"})
        self.assertEqual((r["text"], r["model"]), ("안녕하세요", "qwen"))

    def test_file_latest_and_paths(self):
        h = self.h()
        for name, mtime in (("a.txt", 100), ("b.md", 200), ("c.exe", 300)):
            p = os.path.join(self.tmp.name, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write("내용 " + name)
            os.utime(p, (mtime, mtime))
        r = h.run({"payload": {"prompt": "요약", "file": "@latest"}})
        self.assertEqual(r["file"], "b.md")
        self.assertIn("내용 b.md", json.loads(self.reqs[0].data)["messages"][-1]["content"])
        for bad in ("../etc/passwd", "/etc/passwd", "sub/a.txt", "c.exe", "없음.txt", "..\\x.txt"):
            with self.assertRaises(PermanentError, msg=bad):
                h.run({"payload": {"prompt": "요약", "file": bad}})

    def test_pdf_uses_pdftotext_argv(self):
        open(os.path.join(self.tmp.name, "r.pdf"), "wb").close()
        runner = FakeRunner(out="PDF 본문")
        h = LlmChatHandler({"base_url": "http://x/v1", "files_dir": self.tmp.name, "pdftotext": "pdftotext"},
                           runner=runner, opener=self.opener())
        h.run({"payload": {"prompt": "요약", "file": "r.pdf"}})
        self.assertEqual(runner.calls[0]["argv"][:4], ["pdftotext", "-layout", "-enc", "UTF-8"])
        self.assertEqual(runner.calls[0]["argv"][-1], "-")

    def test_errors(self):
        h = LlmChatHandler({"base_url": "http://x/v1"}, opener=self.opener(status=503))
        with self.assertRaises(HandlerError):
            h.run({"payload": {"prompt": "x"}})
        h = LlmChatHandler({"base_url": "http://x/v1"}, opener=self.opener(status=400))
        with self.assertRaises(PermanentError):
            h.run({"payload": {"prompt": "x"}})
        with self.assertRaises(PermanentError):
            LlmChatHandler({"base_url": ""}).run({"payload": {"prompt": "x"}})
        with self.assertRaises(PermanentError):
            self.h().run({"payload": {"messages": [{"role": "root", "content": "x"}]}})


class NotifySayTest(unittest.TestCase):
    def test_logs_without_url(self):
        r = NotifySayHandler({}).run({"payload": {"text": "작업 끝"}})
        self.assertEqual((r["delivered"], r["logged"]), (False, True))

    def test_posts_to_sidekick(self):
        reqs = []
        h = NotifySayHandler({"sidekick_url": "http://127.0.0.1:8765/", "token": "sk", "path": "/api/say"},
                             opener=lambda req, timeout: reqs.append(req) or Resp(b"{}"))
        r = h.run({"payload": {"text": "작업 끝"}})
        self.assertTrue(r["delivered"])
        self.assertEqual(reqs[0].full_url, "http://127.0.0.1:8765/api/say")
        self.assertEqual(reqs[0].get_header("X-sidekick-token"), "sk")
        self.assertEqual(json.loads(reqs[0].data)["text"], "작업 끝")

    def test_missing_endpoint_404_is_logged(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b""))
        r = NotifySayHandler({"sidekick_url": "http://x"}, opener=opener).run({"payload": {"text": "t"}})
        self.assertFalse(r["delivered"])
        self.assertIn("엔드포인트", r["reason"])
        with self.assertRaises(PermanentError):
            NotifySayHandler({}).run({"payload": {"text": ""}})


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
