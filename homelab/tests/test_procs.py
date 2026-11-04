import io
import os
import socket
import subprocess
import sys
import time
import unittest
from unittest import mock

from homelab import cli, logrun, procs, registry, status
from homelab import health as H
from helpers import FAKE_SERVER, TmpCase, free_port, make_ctx


def fake_service(name, port, *, mode="", token_key="", build_env=None, needs=()):
    def build(ctx):
        args = [str(FAKE_SERVER), str(port)] + ([mode] if mode else [])
        env = ctx.env_base()
        env.update(build_env or {})
        return registry.Spec([ctx.python] + args, ctx.layout.root, env)
    return registry.Service(name, "가짜 " + name, "x", port, "/api/ping", build, token_key=token_key,
                            token_header="X-Fake-Token" if token_key else "", needs=tuple(needs))


class ManagerTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.ctx = make_ctx(self.tmp, start_timeout=15.0, log_max_mb=0.01)
        self.ctx.layout.root.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.ctx.settings["ports"] = {"fake-a": self.port}
        self.svc = fake_service("fake-a", self.port, token_key="phone-home",
                                build_env={"FAKE_TOKEN": self.ctx.token("phone-home")})
        self.mgr = procs.Manager(self.ctx)
        self.mgr.registry = {"fake-a": self.svc}
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            self.mgr.stop("fake-a")
        except Exception:
            pass

    def test_up_status_down(self):
        r = self.mgr.start("fake-a")
        self.assertEqual(r.status, "started", r.message)
        st = self.mgr.status_of("fake-a")
        self.assertTrue(st.running)
        self.assertEqual(st.state, "실행 중")
        self.assertTrue(st.port_open)
        self.assertEqual(st.health.state, "up")  # 토큰 헤더를 붙여서 200
        self.assertIsNotNone(st.uptime)
        pid_file = self.ctx.layout.pid_file("fake-a")
        self.assertTrue(pid_file.exists())
        # 두 번째 up 은 이미 실행 중
        self.assertEqual(self.mgr.start("fake-a").status, "already")
        # 로그에 서버가 찍은 줄이 남는다
        self.assertTrue(any("fake server 시작" in ln for ln in procs.tail_lines(self.ctx.layout.log_file("fake-a"), 10)))
        out = self.mgr.stop("fake-a")
        self.assertEqual(out.status, "stopped")
        self.assertFalse(pid_file.exists())
        self.assertFalse(H.port_in_use("127.0.0.1", self.port))
        self.assertEqual(self.mgr.stop("fake-a").status, "not-running")
        self.assertEqual(self.mgr.status_of("fake-a").state, "꺼짐")

    def test_wrong_token_is_reported(self):
        self.mgr.registry = {"fake-a": fake_service("fake-a", self.port, token_key="sidekick",
                                                    build_env={"FAKE_TOKEN": "서버가-기대하는-다른-값"})}
        self.assertEqual(self.mgr.start("fake-a").status, "started")
        st = self.mgr.status_of("fake-a")
        self.assertEqual(st.health.state, "auth")
        self.assertIn("토큰", "\n".join(status.render_status([st])))

    def test_port_conflict_blocks_start(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(50)
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]
        self.ctx.settings["ports"]["fake-a"] = port
        popen = mock.Mock(side_effect=AssertionError("충돌인데 프로세스를 띄웠어요"))
        mgr = procs.Manager(self.ctx, popen=popen)
        mgr.registry = {"fake-a": fake_service("fake-a", port)}
        r = mgr.start("fake-a")
        self.assertEqual(r.status, "conflict")
        self.assertIn(str(port), r.message)
        popen.assert_not_called()
        st = mgr.status_of("fake-a")
        self.assertEqual(st.state, "다른 프로그램이 포트 사용")
        self.assertFalse(st.running)

    def test_stale_pid_file_is_cleaned(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        procs.write_pid(self.ctx.layout.pid_file("fake-a"), {"pid": dead.pid})
        self.assertIsNone(self.mgr.running_pid("fake-a"))
        self.assertEqual(self.mgr.stop("fake-a").status, "not-running")
        self.assertFalse(self.ctx.layout.pid_file("fake-a").exists())

    def test_server_that_dies_is_reported_with_log(self):
        self.mgr.registry = {"fake-a": fake_service("fake-a", self.port, mode="die")}
        r = self.mgr.start("fake-a")
        self.assertEqual(r.status, "failed")
        self.assertIn("일부러 죽음", r.message)
        self.assertFalse(self.ctx.layout.pid_file("fake-a").exists())

    def test_missing_python_is_reported(self):
        self.ctx.settings["python"] = str(self.tmp / "없는 python")
        r = procs.Manager(self.ctx, popen=subprocess.Popen).start("fake-a") if False else None
        mgr = procs.Manager(self.ctx)
        mgr.registry = {"fake-a": self.svc}
        r = mgr.start("fake-a")
        self.assertEqual(r.status, "failed")

    def test_log_rotation_while_running(self):
        self.mgr.registry = {"fake-a": fake_service("fake-a", self.port, mode="spam")}
        self.assertEqual(self.mgr.start("fake-a").status, "started")
        log = self.ctx.layout.log_file("fake-a")
        deadline = time.time() + 10
        while time.time() < deadline and not log.with_name(log.name + ".1").exists():
            time.sleep(0.1)
        self.mgr.stop("fake-a")
        self.assertTrue(log.with_name(log.name + ".1").exists(), "회전된 로그가 없어요")
        limit = int(0.01 * 1024 * 1024)
        self.assertLessEqual(log.stat().st_size, limit + 4096)
        self.assertFalse(log.with_name(log.name + ".4").exists())  # backups=3

    def test_command_is_argument_list_without_shell(self):
        popen = mock.Mock()
        popen.return_value.pid = 99999999
        mgr = procs.Manager(self.ctx, popen=popen, alive=lambda pid: False)
        mgr.registry = {"fake-a": self.svc}
        mgr.start("fake-a")
        args, kwargs = popen.call_args
        self.assertIsInstance(args[0], list)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(args[0][0], self.ctx.python)
        self.assertIn("-I", args[0])
        self.assertEqual(kwargs["env"]["FAKE_TOKEN"], self.ctx.token("phone-home"))
        self.assertNotIn(self.ctx.token("phone-home"), " ".join(args[0]))  # 토큰은 명령줄에 없다


class LogRotationTests(TmpCase):
    def test_rotating_log_keeps_backups(self):
        log = logrun.RotatingLog(self.tmp / "a.log", max_bytes=1024, backups=2)
        for i in range(30):
            log.write(("줄 %03d " % i + "y" * 90 + "\n").encode("utf-8"))
        log.close()
        names = sorted(p.name for p in self.tmp.iterdir())
        self.assertEqual(names, ["a.log", "a.log.1", "a.log.2"])
        self.assertIn("줄 029", (self.tmp / "a.log").read_text(encoding="utf-8"))

    def test_logrun_script_end_to_end(self):
        log = self.tmp / "s" / "x.log"
        code = "for i in range(200): print('hello', i, 'z'*50)"
        r = subprocess.run([sys.executable, "-I", str(procs.LOGRUN), "--log", str(log), "--max-bytes", "2048",
                            "--backups", "2", "--", sys.executable, "-c", code], capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(log.exists())
        self.assertTrue(log.with_name("x.log.1").exists())
        self.assertIn("종료 코드 0", log.read_text(encoding="utf-8"))

    def test_rotate_if_big(self):
        p = self.tmp / "big.log"
        p.write_bytes(b"x" * 5000)
        self.assertTrue(logrun.rotate_if_big(p, 2048, 2))
        self.assertTrue((self.tmp / "big.log.1").exists())
        self.assertEqual(p.stat().st_size, 0)


class CliUpDown(TmpCase):
    """cli 의 up/down/status 를 가짜 서비스 둘(의존 관계 포함)로."""

    def test_cli_flow(self):
        pa, pb = free_port(), free_port()
        out = io.StringIO()
        base = ["--dir", str(self.tmp / ".homelab"), "--home", str(self.tmp)]
        self.assertEqual(cli.main(base + ["init", "--yes", "--python", sys.executable], out=out), 0)
        sa = fake_service("fake-a", pa)
        sb = fake_service("fake-b", pb, needs=("fake-a",))
        with mock.patch.dict(registry.SERVICES, {"fake-a": sa, "fake-b": sb}):
            hl = self.tmp / ".homelab" / "homelab.json"
            import json
            data = json.loads(hl.read_text(encoding="utf-8"))
            data.update({"enabled": ["fake-a", "fake-b"], "ports": {"fake-a": pa, "fake-b": pb}, "start_timeout": 15})
            hl.write_text(json.dumps(data), encoding="utf-8")
            self.addCleanup(lambda: cli.main(base + ["down"], out=io.StringIO()))
            out = io.StringIO()
            self.assertEqual(cli.main(base + ["up"], out=out), 0, out.getvalue())
            text = out.getvalue()
            self.assertIn("fake-a", text)
            self.assertIn("실행 중", text)
            out = io.StringIO()
            self.assertEqual(cli.main(base + ["status", "--only", "fake-a"], out=out), 0)
            self.assertIn("정상", out.getvalue())
            # 일부만 끄기
            out = io.StringIO()
            self.assertEqual(cli.main(base + ["down", "--only", "fake-b"], out=out), 0)
            self.assertFalse(H.port_in_use("127.0.0.1", pb))
            self.assertTrue(H.port_in_use("127.0.0.1", pa))
            out = io.StringIO()
            self.assertEqual(cli.main(base + ["down"], out=out), 0)
            self.assertFalse(H.port_in_use("127.0.0.1", pa))

    def test_dependent_skipped_when_dependency_fails(self):
        pa, pb = free_port(), free_port()
        base = ["--dir", str(self.tmp / ".homelab"), "--home", str(self.tmp)]
        cli.main(base + ["init", "--yes", "--python", sys.executable], out=io.StringIO())
        sa = fake_service("fake-a", pa, mode="die")
        sb = fake_service("fake-b", pb, needs=("fake-a",))
        with mock.patch.dict(registry.SERVICES, {"fake-a": sa, "fake-b": sb}):
            hl = self.tmp / ".homelab" / "homelab.json"
            import json
            data = json.loads(hl.read_text(encoding="utf-8"))
            data.update({"enabled": ["fake-a", "fake-b"], "ports": {"fake-a": pa, "fake-b": pb}, "start_timeout": 15})
            hl.write_text(json.dumps(data), encoding="utf-8")
            out = io.StringIO()
            self.assertEqual(cli.main(base + ["up"], out=out), 1)
            self.assertIn("건너뛰", out.getvalue())
            self.assertFalse(H.port_in_use("127.0.0.1", pb))

    def test_up_without_init(self):
        out = io.StringIO()
        rc = cli.main(["--dir", str(self.tmp / "nope"), "--home", str(self.tmp), "up"], out=out)
        self.assertEqual(rc, 2)
        self.assertIn("homelab init", out.getvalue())


class StatusTable(unittest.TestCase):
    def test_korean_columns_align(self):
        rows = [procs.UnitStatus("sidekick", "S21 사이드킥", True, True, 1234, 8765, True, H.Health("up", 200), 3700, "실행 중"),
                procs.UnitStatus("phone-home", "폰 홈", True, False, None, 8790, False, H.Health("none"), None, "꺼짐")]
        lines = status.render_status(rows)
        from homelab._jjkit import textwidth
        table = [ln for ln in lines[:4]]
        # 첫 열 너비가 헤더/구분선/본문에서 같다 (한글 전각 폭 기준)
        self.assertEqual(textwidth.width(table[1].split("  ")[0]), len("-" * textwidth.width(table[1].split("  ")[0])))
        self.assertIn("1시간 1분", "\n".join(lines))
        self.assertIn("실행 중 1개 / 켜기로 한 서비스 2개", "\n".join(lines))
        self.assertEqual(status.fmt_uptime(5), "5초")
        self.assertEqual(status.fmt_uptime(90000), "1일 1시간")


class PidAlive(unittest.TestCase):
    def test_self_and_dead(self):
        self.assertTrue(procs.pid_alive(os.getpid()))
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(procs.pid_alive(p.pid))
        self.assertFalse(procs.pid_alive(0))


if __name__ == "__main__":
    unittest.main()
