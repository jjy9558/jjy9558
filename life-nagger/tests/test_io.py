import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime

from nagger import cli
from nagger.events_server import EventServer
from nagger.fakes import FakePlatform
from nagger.model import INFO, URGENT, WARN, Message
from nagger.outputs import Console, Ntfy, Notifier, SidekickSay, Toast
from nagger.platform import (WindowsPlatform, PlatformUnavailable, parse_tasklist, suspend_command,
                             toast_burnttoast_command, toast_msg_command)
from tests.helpers import Base, Sink

NOW = datetime(2026, 11, 2, 3, 0)


class PlatformTests(unittest.TestCase):
    def test_parse_tasklist(self):
        raw = '"System Idle Process","0","Services","0","8 K"\r\n"Code.exe","1234","Console","1","90,000 K"\r\n"League of Legends.exe","9","Console","1","1 K"\r\n'
        self.assertEqual(parse_tasklist(raw), {"system idle process", "code", "league of legends"})
        self.assertEqual(parse_tasklist(raw.encode("cp949")), parse_tasklist(raw))

    def test_commands_are_argument_lists(self):
        self.assertEqual(suspend_command("suspend"), ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        self.assertEqual(suspend_command("hibernate"), ["shutdown", "/h"])
        with self.assertRaises(ValueError):
            suspend_command("poweroff")
        cmd, env = toast_burnttoast_command("제목'; rm", "본문 $(x)")
        self.assertIsInstance(cmd, list)
        self.assertNotIn("rm", " ".join(cmd))           # 텍스트는 명령줄이 아니라 환경변수로
        self.assertEqual(env["NAGGER_BODY"], "본문 $(x)")
        self.assertEqual(toast_msg_command("a", "/b")[:2], ["msg", "*"])
        self.assertTrue(toast_msg_command("a", "/b")[-1].startswith("[a]"))

    def test_windows_platform_refuses_elsewhere(self):
        import sys
        if sys.platform != "win32":
            with self.assertRaises(PlatformUnavailable):
                WindowsPlatform()

    def test_windows_runner_calls_with_lists(self):
        import sys
        from unittest import mock
        calls = []

        class R:
            returncode, stdout = 0, b""

        def runner(cmd, **kw):
            calls.append(cmd)
            return R()
        with mock.patch.object(sys, "platform", "win32"):
            p = WindowsPlatform(runner)
            p.suspend("suspend")
            p.running_processes()
            self.assertTrue(p.toast("t", "b"))
        self.assertTrue(all(isinstance(c, list) for c in calls))
        self.assertEqual(calls[0][0], "rundll32.exe")

    def test_toast_fallback_chain(self):
        import sys
        from unittest import mock

        class R:
            def __init__(self, rc):
                self.returncode, self.stdout = rc, b""
        seen = []

        def runner(cmd, **kw):
            seen.append(cmd[0])
            if cmd[0] == "powershell.exe":
                raise FileNotFoundError
            return R(0)
        with mock.patch.object(sys, "platform", "win32"):
            self.assertTrue(WindowsPlatform(runner).toast("t", "b"))
        self.assertEqual(seen, ["powershell.exe", "msg"])


class OutputTests(unittest.TestCase):
    def test_level_filter_and_error_isolation(self):
        lo, hi = Sink(), Sink()
        hi.min_level = WARN

        class Bad:
            min_level = 0

            def send(self, m, n):
                raise RuntimeError("x")
        nt = Notifier([Bad(), lo, hi])
        nt.send(Message("a", "mombot", INFO), NOW)
        nt.send(Message("b", "mombot", URGENT), NOW)
        self.assertEqual(lo.texts(), ["a", "b"])
        self.assertEqual(hi.texts(), ["b"])
        self.assertEqual(len(nt.errors), 2)

    def test_console_and_toast_fallback(self):
        buf = []
        Console({}, buf.append).send(Message("안녕", "mombot"), NOW)
        self.assertIn("엄마봇", buf[0])
        p = FakePlatform()
        p.toast_ok = False
        Toast({}, p, buf.append).send(Message("경고", "sitting"), NOW)
        self.assertEqual(len(p.toasts), 1)
        self.assertIn("***", buf[-1])

    def test_ntfy(self):
        calls = []
        Ntfy({"topic": "t1", "url": "https://ntfy.example/"}, lambda *a: calls.append(a) or (200, b"")).send(
            Message("한글", "mombot"), NOW)
        self.assertEqual(calls[0][0], "https://ntfy.example/t1")
        self.assertIn("한글".encode(), calls[0][1])
        with self.assertRaises(ValueError):
            Ntfy({"topic": ""})

    def test_sidekick_say_ok_and_token(self):
        calls = []
        s = SidekickSay({"base_url": "http://h:1", "token": "cfg"}, lambda *a: calls.append(a) or (200, b""),
                        environ={"SIDEKICK_TOKEN": "env"})
        s.send(Message("야", "sitting"), NOW)
        url, data, headers = calls[0]
        self.assertEqual(url, "http://h:1/api/say")
        self.assertEqual(headers["X-Sidekick-Token"], "env")
        self.assertEqual(json.loads(data)["text"], "야")

    def test_sidekick_404_disables_itself(self):
        calls = []
        s = SidekickSay({}, lambda *a: calls.append(a) or (404, b""), environ={})
        s.send(Message("a", "x"), NOW)
        s.send(Message("b", "x"), NOW)
        self.assertTrue(s.unsupported)
        self.assertEqual(len(calls), 1)

    def test_sidekick_server_error_raises(self):
        s = SidekickSay({}, lambda *a: (500, b""), environ={})
        with self.assertRaises(RuntimeError):
            s.send(Message("a", "x"), NOW)


class EventServerTests(Base):
    def setUp(self):
        self.make()
        got = []
        self.got = got
        self.srv = EventServer("127.0.0.1", 0, "tok", self.cfg["counters"],
                               lambda k, n: (got.append((k, n)), self.n.counters.add(k, self.clock.now(), n))).start()
        self.addCleanup(self.srv.stop)

    def post(self, body, token="tok", path="/api/event"):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.srv.port, path), json.dumps(body).encode(),
                                     method="POST", headers={"X-Sidekick-Token": token} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_sigh_event(self):
        self.assertEqual(self.post({"type": "sigh"}), 200)
        self.assertEqual(self.post({"type": "pushup", "n": 15}), 200)
        self.assertEqual(self.got, [("sigh", 1), ("pushup", 15)])
        self.assertEqual(self.n.counters.today_total("pushup", self.clock.now()), 15)

    def test_rejections(self):
        self.assertEqual(self.post({"type": "sigh"}, token="bad"), 401)
        self.assertEqual(self.post({"type": "sigh"}, token=None), 401)
        self.assertEqual(self.post({"type": "burp"}), 400)
        self.assertEqual(self.post({"type": "sigh", "n": -3}), 400)
        self.assertEqual(self.post({"type": "sigh"}, path="/x"), 404)
        self.assertEqual(self.got, [])

    def test_requires_token(self):
        with self.assertRaises(ValueError):
            EventServer("127.0.0.1", 0, "", {}, lambda *a: None)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.cfgp = os.path.join(self.t.name, "c.json")
        self.write_cfg({"state_dir": self.t.name})
        self.out = []

    def write_cfg(self, obj):
        with open(self.cfgp, "w") as f:
            json.dump(obj, f)

    def rd(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def run_cli(self, *argv):
        rc = cli.main(["--config", self.cfgp] + list(argv), out=self.out.append)
        return rc, "\n".join(self.out)

    def test_excuse(self):
        rc, text = self.run_cli("excuse", "--late", "20", "--to", "팀장", "--seed", "1")
        self.assertEqual(rc, 0)
        self.assertIn("[3]", text)
        self.assertIn("[정직] 팀장님, 늦잠 잤습니다, 죄송합니다.", text)

    def test_count_and_weekly(self):
        for _ in range(3):
            self.run_cli("count", "sigh")
        self.run_cli("count", "pushup", "-n", "20")
        rc, text = self.run_cli("report", "--week")
        self.assertIn("한숨 3회", text)
        self.assertIn("푸시업 20개", text)
        self.assertEqual(self.run_cli("count", "burp")[0], 2)

    def test_dishes_reset(self):
        rc, text = self.run_cli("count", "dishes")
        self.assertIn("리셋", text)

    def test_month_report_files(self):
        h, m = os.path.join(self.t.name, "r.html"), os.path.join(self.t.name, "r.md")
        rc, _ = self.run_cli("report", "--month", "2026-10", "--html", h, "--md", m)
        self.assertEqual(rc, 0)
        self.assertIn("<html", self.rd(h))
        self.assertIn("순수 작업 시간", self.rd(m))
        self.assertEqual(self.run_cli("report", "--month", "bad")[0], 2)

    def test_status_without_daemon_and_cancel(self):
        self.assertIn("run", self.run_cli("status")[1])
        rc, _ = self.run_cli("cancel-sleep")
        self.assertTrue(os.path.exists(os.path.join(self.t.name, "cancel_sleep.flag")))

    def test_bad_config(self):
        self.write_cfg({"mombot": {"allow_sleep": "yes"}})
        rc, text = self.run_cli("excuse", "--late", "5")
        self.assertEqual(rc, 2)
        self.assertIn("allow_sleep", text)


class EngineIntegration(Base):
    def test_status_file_and_dishes_nag(self):
        self.make(datetime(2026, 11, 5, 19, 59))
        self.n.counters.add("dishes", datetime(2026, 11, 2, 21, 0))
        self.n.tick()
        self.assertEqual(self.sink.texts("dishes"), [])
        self.step(minutes=1)                                  # 20:00 -> 3일 방치
        self.assertIn("3일째", self.sink.texts("dishes")[0])
        self.step(minutes=30)
        self.assertEqual(len(self.sink.texts("dishes")), 1)   # 하루 한 번
        self.clock.set(datetime(2026, 11, 6, 20, 5))
        self.n.tick()
        self.assertIn("4일째", self.sink.texts("dishes")[1])
        with open(self.n.status_path, encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["dish_days"], 4)

    def test_dishes_done_today_silences(self):
        self.make(datetime(2026, 11, 5, 20, 5))
        self.n.counters.add("dishes", datetime(2026, 11, 5, 19, 0))
        self.n.tick()
        self.assertEqual(self.sink.texts("dishes"), [])

    def test_run_loop_uses_injected_sleep(self):
        self.make()
        slept = []
        self.n.run(max_ticks=3, sleep=slept.append)
        self.assertEqual(slept, [30, 30])


if __name__ == "__main__":
    unittest.main()
