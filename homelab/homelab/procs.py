"""서버를 백그라운드로 켜고 끄고 상태를 보는 관리자.

- 인자 목록 + shell=False. 파이썬은 설정된 것(기본 init 한 파이썬).
- 서버마다 작은 감독자(logrun.py)를 띄워 출력을 크기 기준으로 회전하는 로그에 받아 적는다.
- PID 파일은 ``run/<이름>.pid``(JSON). 감독자의 PID 이고, 끄면 감독자와 서버를 같이 끈다.
- 켜기 전에 포트 충돌을 본다. 다른 프로그램이 그 포트를 쓰고 있으면 켜지 않는다.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import health as H
from . import logrun
from .registry import SERVICES, Ctx, Service

LOGRUN = Path(logrun.__file__).resolve()


# ---------------------------------------------------------------------------------------
# PID 파일 / 프로세스
# ---------------------------------------------------------------------------------------
def read_pid(path: Path) -> dict | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, int):
        return {"pid": data}
    if isinstance(data, dict) and isinstance(data.get("pid"), int):
        return data
    return None


def write_pid(path: Path, info: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, ensure_ascii=False) + "\n", encoding="utf-8")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _win_alive(pid)
    try:  # 내가 띄운 자식이 끝나 좀비로 남았으면 거둔다
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _win_alive(pid: int) -> bool:  # pragma: no cover - 윈도우 전용
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False


def terminate(pid: int, *, timeout: float = 8.0, alive: Callable[[int], bool] = pid_alive,
              sleep: Callable[[float], None] = time.sleep) -> bool:
    """감독자와 자식을 끈다. 끝났으면 True."""
    if not alive(pid):
        return True
    if sys.platform == "win32":  # pragma: no cover - 윈도우 전용
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, shell=False)
        # 창 없는 감독자는 /F 없이는 안 꺼지는 게 보통이라, 정중한 요청은 잠깐만 기다린다
        if _wait_dead(pid, min(timeout, 1.0), alive, sleep):
            return True
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, shell=False)
        return _wait_dead(pid, 3.0, alive, sleep)
    group = False
    try:
        group = os.getpgid(pid) == pid  # start_new_session 으로 띄운 우리 감독자만 그룹째 끈다
    except OSError:
        pass

    def send(sig: int) -> None:
        try:
            if group:
                os.killpg(pid, sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass

    send(signal.SIGTERM)
    if _wait_dead(pid, timeout, alive, sleep):
        return True
    send(signal.SIGKILL)
    return _wait_dead(pid, 3.0, alive, sleep)


def _wait_dead(pid: int, timeout: float, alive, sleep) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive(pid):
            return True
        sleep(0.1)
    return not alive(pid)


def _detach(proc) -> None:
    """감독자는 이 명령이 끝나도 계속 돌아야 한다. Popen 이 GC 될 때 '아직 실행 중' 경고를 내지 않게 표시만 한다.
    (거두는 일은 pid_alive 의 waitpid 와 OS 가 한다.)"""
    try:
        if getattr(proc, "returncode", 0) is None:
            proc.returncode = 0
    except AttributeError:
        pass


def tail_lines(path: Path, n: int = 20) -> list[str]:
    try:
        data = Path(path).read_bytes()[-64 * 1024:]
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


# ---------------------------------------------------------------------------------------
# 관리자
# ---------------------------------------------------------------------------------------
@dataclass
class Outcome:
    name: str
    status: str   # started | slow | already | conflict | failed | skipped | stopped | not-running
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("started", "already", "stopped", "not-running")


@dataclass
class UnitStatus:
    name: str
    title: str
    enabled: bool
    running: bool
    pid: int | None
    port: int
    port_open: bool
    health: H.Health = field(default_factory=lambda: H.Health("none"))
    uptime: float | None = None
    state: str = "꺼짐"


def service_host(ctx: Ctx, name: str) -> str:
    if name == "jarvis-hub":
        h = ctx.hub_host
        return "127.0.0.1" if h in ("0.0.0.0", "::", "") else h
    return "127.0.0.1"


class Manager:
    def __init__(self, ctx: Ctx, *, popen=subprocess.Popen, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, port_in_use=H.port_in_use,
                 alive: Callable[[int], bool] = pid_alive, opener=None,
                 terminate_fn=terminate, now: Callable[[], float] = time.time):
        self.ctx = ctx
        self.popen = popen
        self.sleep = sleep
        self.clock = clock
        self.port_in_use = port_in_use
        self.alive = alive
        self.opener = opener
        self.terminate_fn = terminate_fn
        self.now = now
        self.registry: dict[str, Service] = SERVICES
        self._children: dict[str, object] = {}   # 띄운 Popen 을 쥐고 있다가 끌 때 거둔다 (ResourceWarning 방지)

    # --- 조회 ------------------------------------------------------------------------
    def pid_path(self, name: str) -> Path:
        return self.ctx.layout.pid_file(name)

    def running_pid(self, name: str) -> int | None:
        info = read_pid(self.pid_path(name))
        if info and self.alive(info["pid"]):
            return info["pid"]
        return None

    def health_of(self, svc: Service) -> H.Health:
        port = self.ctx.port(svc.name)
        if not svc.health or not port:
            return H.Health("none")
        headers = {}
        if svc.token_key and svc.token_header:
            headers[svc.token_header] = self.ctx.token(svc.token_key)
        host = service_host(self.ctx, svc.name)
        return H.check_http("http://%s:%d%s" % (host, port, svc.health), headers,
                            any_http_ok=svc.any_http_ok, opener=self.opener)

    def status_of(self, name: str, *, with_health: bool = True) -> UnitStatus:
        svc = self.registry[name]
        port = self.ctx.port(name)
        pid = self.running_pid(name)
        host = service_host(self.ctx, name)
        port_open = bool(port) and self.port_in_use(host, port)
        st = UnitStatus(name, svc.title, self.ctx.is_enabled(name), pid is not None, pid, port, port_open)
        if pid is not None:
            info = read_pid(self.pid_path(name)) or {}
            if info.get("started"):
                st.uptime = max(0.0, self.now() - float(info["started"]))
            if with_health and svc.health and port_open:
                st.health = self.health_of(svc)
            st.state = "실행 중" if (port_open or not port) else "시작 중/응답 없음"
        elif port_open:
            st.state = "다른 프로그램이 포트 사용"
            if with_health and svc.health:
                st.health = self.health_of(svc)
        else:
            st.state = "꺼짐"
        return st

    # --- 켜기 ------------------------------------------------------------------------
    def start(self, name: str) -> Outcome:
        svc = self.registry[name]
        ctx = self.ctx
        if self.running_pid(name) is not None:
            return Outcome(name, "already", "이미 실행 중이에요 (PID %d)" % self.running_pid(name))
        stale = self.pid_path(name)
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass
        spec = svc.build(ctx)
        from .configgen import ensure_data_dirs
        try:
            ensure_data_dirs(ctx)
        except OSError:
            pass
        if not spec.cwd.is_dir():
            return Outcome(name, "failed", "프로젝트 폴더가 없어요: %s (repo_root 확인)" % spec.cwd)
        for key in svc.config_keys:
            cfg = ctx.cfg(key)
            if not cfg.exists():
                return Outcome(name, "failed", "설정 파일이 없어요: %s → 먼저 `homelab init`" % cfg)
        port = ctx.port(name)
        host = service_host(ctx, name)
        if port and self.port_in_use(host, port):
            who = self.health_of(svc)
            hint = "응답이 있어요(%s)" % who.label if who.state != "down" else "응답은 없어요"
            return Outcome(name, "conflict",
                           "포트 %d 을(를) 다른 프로그램이 쓰고 있어요 (%s). 그 프로그램을 끄거나 "
                           "homelab.json 의 ports 로 포트를 바꾸세요." % (port, hint))
        log_path = ctx.layout.log_file(name)
        max_bytes = int(float(ctx.settings.get("log_max_mb") or 2) * 1024 * 1024)
        backups = int(ctx.settings.get("log_backups") or 3)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logrun.rotate_if_big(log_path, max_bytes, backups)
        argv = [ctx.python, "-I", str(LOGRUN), "--log", str(log_path), "--max-bytes", str(max_bytes),
                "--backups", str(backups), "--"] + spec.argv
        env = dict(os.environ)
        env.update(spec.env)
        kwargs: dict = {}
        if sys.platform == "win32":  # pragma: no cover
            kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
                                       | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        else:
            kwargs["start_new_session"] = True
        try:
            proc = self.popen(argv, cwd=str(spec.cwd), env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
                              close_fds=True, **kwargs)
        except OSError as e:
            return Outcome(name, "failed", "실행하지 못했어요: %s (python 경로 확인: %s)" % (e, ctx.python))
        self._children[name] = proc
        _detach(proc)
        write_pid(self.pid_path(name), {"pid": proc.pid, "started": self.now(), "port": port,
                                        "log": str(log_path)})
        return self._wait_ready(name, svc, proc.pid, port, host, log_path)

    def _wait_ready(self, name: str, svc: Service, pid: int, port: int, host: str, log_path: Path) -> Outcome:
        timeout = float(self.ctx.settings.get("start_timeout") or 10.0)
        t0 = self.clock()
        settle = 1.0
        while True:
            if not self.alive(pid):
                try:
                    self.pid_path(name).unlink()
                except OSError:
                    pass
                tail = [ln for ln in tail_lines(log_path, 6) if not ln.startswith("[homelab")][-4:]
                msg = "켜자마자 꺼졌어요. 로그: homelab logs %s" % name
                if tail:
                    msg += "\n      " + "\n      ".join(tail)
                return Outcome(name, "failed", msg)
            elapsed = self.clock() - t0
            if port:
                if self.port_in_use(host, port):
                    return Outcome(name, "started", "켰어요 (PID %d, 포트 %d)" % (pid, port))
            elif elapsed >= settle:
                return Outcome(name, "started", "켰어요 (PID %d)" % pid)
            if elapsed >= timeout:
                return Outcome(name, "slow", "프로세스는 떠 있는데 포트 %d 가 아직 안 열렸어요 "
                                             "(느린 시작일 수 있어요: homelab logs %s)" % (port, name))
            self.sleep(0.1)

    def up(self, names: list[str]) -> list[Outcome]:
        out = []
        for n in names:
            out.append(self.start(n))
        return out

    # --- 끄기 ------------------------------------------------------------------------
    def stop(self, name: str) -> Outcome:
        path = self.pid_path(name)
        info = read_pid(path)
        if not info:
            return Outcome(name, "not-running", "꺼져 있어요")
        pid = info["pid"]
        if not self.alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            return Outcome(name, "not-running", "꺼져 있어요 (낡은 PID 파일 정리)")
        done = self.terminate_fn(pid, alive=self.alive, sleep=self.sleep)
        if not done:
            return Outcome(name, "failed", "PID %d 를 끄지 못했어요. 작업 관리자에서 직접 종료하세요." % pid)
        try:
            path.unlink()
        except OSError:
            pass
        child = self._children.pop(name, None)
        if child is not None and hasattr(child, "poll"):
            try:
                child.poll()
            except Exception:
                pass
        return Outcome(name, "stopped", "껐어요 (PID %d)" % pid)

    def down(self, names: list[str]) -> list[Outcome]:
        return [self.stop(n) for n in reversed(names)]
