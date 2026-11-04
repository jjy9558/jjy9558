"""e2e 하네스: 형제 프로젝트를 진짜 서버/CLI 하위 프로세스로 띄우고, 임시 HOME 안에서만 돌린다.

- 포트는 실행 시점에 빈 포트를 골라 쓰고(``free_port``), 토큰도 실행 시점에 만든다(``token``).
- 자식 프로세스 환경에서는 프록시 변수와 각 프로젝트의 데이터 위치 환경변수를 지우고
  ``HOME``/``USERPROFILE`` 을 임시 폴더로 바꾼다. 외부 네트워크로 나가는 길은 없다.
- LLM 은 ``FakeLLM`` (127.0.0.1 port=0 의 OpenAI 호환 가짜 서버)으로 대신한다.
- 모든 하위 프로세스는 ``Stack.close`` 에서 종료(terminate → kill)한다.

표준 라이브러리만 쓴다. 다른 프로젝트 폴더는 읽기/실행만 하고 고치지 않는다.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]

# 폴더 -> 파이썬 패키지 (python -m <pkg>)
PROJECTS = {
    "voice-journal": "vjournal",
    "morning-brief": "mbrief",
    "phone-home": "phonehome",
    "paper-butler": "paperbutler",
    "cool-off": "cool_off",
    "s21-sidekick": "sidekick",
    "jarvis-hub": "jarvis",
    "life-nagger": "nagger",
    "object-memory": "objmem",
    "story-factory": "storyfactory",
    "shorts-linter": "shortslint",
    "decision-pet": "decision_pet",
    "steam-buddy": "steam_buddy",
    "kakao-wrapped": "kakao_wrapped",
}

# 자식 프로세스에 새면 안 되는 환경변수 (프록시 + 각 프로젝트 데이터 위치/토큰)
_DROP_PREFIXES = ("VJOURNAL_", "VJ_", "SIDEKICK_", "OBJMEM_", "PAPERBUTLER_", "COOLOFF_", "STEAM_BUDDY_",
                  "DECISION_PET_", "JARVIS_", "MBRIEF_", "PHONE_HOME_", "STORY_FACTORY_", "SHORTSLINT_",
                  "SOUNDWATCH_", "NAGGER_", "NTFY_", "CLAUDE_CONFIG_DIR")
_PROXY_VARS = {"http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "no_proxy", "socks_proxy"}

DEFAULT_TIMEOUT = 30


def project_dir(name: str) -> Path:
    return ROOT / name


def have(*names: str) -> list[str]:
    """없는 프로젝트 이름 목록 (비어 있으면 전부 있음)."""
    return [n for n in names if not (project_dir(n) / PROJECTS[n] / "__init__.py").is_file()]


def token(tag: str = "e2e") -> str:
    return "%s-%s" % (tag, secrets.token_urlsafe(18))


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def child_env(home: Path, extra: dict | None = None) -> dict:
    env = {}
    for k, v in os.environ.items():
        if k.lower() in _PROXY_VARS or k.lower().endswith("_proxy"):
            continue
        if k.upper().startswith(_DROP_PREFIXES):
            continue
        env[k] = v
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData"),
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1", "TZ": os.environ.get("TZ", ""),
    })
    if not env["TZ"]:
        env.pop("TZ")
    env.pop("PYTHONPATH", None)
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    return env


# ---------------------------------------------------------------- HTTP (프록시 없음)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method: str, url: str, body=None, headers: dict | None = None, timeout: float = 10):
    """(status, headers, bytes). HTTP 오류 코드도 예외 없이 돌려준다."""
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        return e.code, dict(e.headers or {}), raw


def http_json(method: str, url: str, body=None, headers: dict | None = None, timeout: float = 10):
    status, _, raw = http(method, url, body, headers, timeout)
    try:
        obj = json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        obj = {"_raw": raw.decode("utf-8", "replace")}
    return status, obj


def wait_until(fn: Callable[[], object], timeout: float = 20, interval: float = 0.1, what: str = "조건"):
    """fn() 이 참값을 돌려줄 때까지 기다린다. 시간 초과면 AssertionError."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            last = fn()
        except Exception as e:  # 서버가 아직 안 떴을 때 등
            last = e
        else:
            if last:
                return last
        time.sleep(interval)
    raise AssertionError("%s 이(가) %.0f초 안에 되지 않음 (마지막 값: %r)" % (what, timeout, last))


# ---------------------------------------------------------------- 가짜 OpenAI 호환 LLM
class FakeLLM:
    """POST /v1/chat/completions 에 responder(body) -> str 로 답한다. 요청은 ``requests`` 에 쌓인다."""

    def __init__(self, responder: Callable[[dict], str] | None = None):
        self.requests: list[dict] = []
        self.responder = responder or (lambda body: "{}")
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    return self._send(200, {"data": [{"id": "fake"}]})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    body = {}
                outer.requests.append({"path": self.path, "body": body})
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    return self._send(404, {"error": "not found"})
                try:
                    content = outer.responder(body)
                except Exception as e:  # 응답기 버그는 500 으로 (호출 쪽 폴백을 보게)
                    return self._send(500, {"error": str(e)})
                self._send(200, {"id": "fake", "object": "chat.completion", "model": body.get("model", "fake"),
                                 "choices": [{"index": 0, "finish_reason": "stop",
                                              "message": {"role": "assistant", "content": content}}],
                                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/v1" % self.srv.server_address[1]

    @staticmethod
    def text_of(body: dict) -> str:
        """메시지 본문(문자열/멀티파트)을 이어 붙인 글."""
        out = []
        for m in body.get("messages") or []:
            c = m.get("content")
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        out.append(part["text"])
        return "\n".join(out)

    @staticmethod
    def has_image(body: dict) -> bool:
        for m in body.get("messages") or []:
            c = m.get("content")
            if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
                return True
        return False

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


# ---------------------------------------------------------------- 하위 프로세스
class Service:
    def __init__(self, name: str, proc: subprocess.Popen, log_path: Path, port: int, log_fh):
        self.name = name
        self.proc = proc
        self.log_path = log_path
        self.port = port
        self._log_fh = log_fh
        self.url = "http://127.0.0.1:%d" % port

    def log(self) -> str:
        try:
            self._log_fh.flush()
        except (OSError, ValueError):
            pass
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            return ""

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        try:
            self._log_fh.close()
        except OSError:
            pass


class Stack:
    """테스트 하나의 세계: 임시 HOME, 하위 프로세스들, 가짜 LLM. close() 로 전부 정리."""

    def __init__(self, prefix: str = "e2e-"):
        self.tmp = Path(tempfile.mkdtemp(prefix=prefix))
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.home / "AppData").mkdir()
        self.logs = self.tmp / "logs"
        self.logs.mkdir()
        self.services: list[Service] = []
        self.fakes: list[FakeLLM] = []
        self.extra_env: dict = {}

    # -- 경로/환경 --
    def path(self, *parts: str) -> Path:
        p = self.tmp.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def env(self, extra: dict | None = None) -> dict:
        e = dict(self.extra_env)
        e.update(extra or {})
        return child_env(self.home, e)

    def write_json(self, rel: str, obj) -> Path:
        p = self.path(rel)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def fake_llm(self, responder=None) -> FakeLLM:
        f = FakeLLM(responder)
        self.fakes.append(f)
        return f

    # -- CLI 한 번 --
    def run(self, project: str, args: list[str], *, input: str | None = None, env: dict | None = None,
            timeout: float = 60, check: bool = True, module: str | None = None,
            code: str | None = None) -> subprocess.CompletedProcess:
        """``python -m <pkg> args`` 를 그 프로젝트 폴더에서 실행 (code 를 주면 ``python -c code args``)."""
        cmd = [sys.executable] + (["-c", code] if code is not None else ["-m", module or PROJECTS[project]]) + \
            [str(a) for a in args]
        p = subprocess.run(cmd, cwd=str(project_dir(project)), env=self.env(env), input=input,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if check and p.returncode != 0:
            raise AssertionError("%s %s 실패 (rc=%s)\nstdout:\n%s\nstderr:\n%s"
                                 % (project, " ".join(map(str, args)), p.returncode, p.stdout[-3000:],
                                    p.stderr[-3000:]))
        return p

    def popen(self, project: str, args: list[str], *, env: dict | None = None, script: str | None = None,
              stdin=subprocess.PIPE) -> subprocess.Popen:
        """오래 걸리는 CLI(훅 등)를 따로 띄운다. 정리는 close() 에서."""
        cmd = [sys.executable] + ([script] if script else ["-m", PROJECTS[project]]) + [str(a) for a in args]
        p = subprocess.Popen(cmd, cwd=str(project_dir(project)), env=self.env(env), stdin=stdin,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.services.append(Service(project + "-popen", p, self.logs / "none.log", 0, open(os.devnull, "w")))
        return p

    # -- 서버 --
    def serve(self, project: str, args_for_port: Callable[[int], list[str]], *, ready_path: str = "/",
              env: dict | None = None, timeout: float = 25, name: str | None = None) -> Service:
        """빈 포트를 골라 서버를 띄우고 ready_path 가 HTTP 로 답할 때까지(401 포함) 기다린다.
        포트 경쟁으로 바로 죽으면 다른 포트로 두 번 더 시도한다."""
        name = name or project
        last_log = ""
        for attempt in range(3):
            port = free_port()
            log_path = self.logs / ("%s-%d.log" % (name, attempt))
            fh = open(log_path, "w", encoding="utf-8")
            cmd = [sys.executable, "-m", PROJECTS[project]] + [str(a) for a in args_for_port(port)]
            proc = subprocess.Popen(cmd, cwd=str(project_dir(project)), env=self.env(env),
                                    stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT)
            svc = Service(name, proc, log_path, port, fh)
            self.services.append(svc)
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if proc.poll() is not None:
                    break
                try:
                    status, _, _ = http("GET", svc.url + ready_path, timeout=2)
                    if status:
                        return svc
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.1)
            last_log = svc.log()
            svc.stop()
            if "in use" not in last_log and "10048" not in last_log:
                break
        raise AssertionError("%s 서버가 뜨지 않음. 로그:\n%s" % (name, last_log))

    # -- 정리 --
    def close(self):
        for s in reversed(self.services):
            try:
                s.stop()
            except Exception:
                pass
        self.services.clear()
        for f in self.fakes:
            try:
                f.close()
            except Exception:
                pass
        self.fakes.clear()
        for _ in range(5):  # Windows: 막 죽은 프로세스가 파일을 잠깐 잡고 있을 수 있음
            try:
                shutil.rmtree(self.tmp)
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.3)
        shutil.rmtree(self.tmp, ignore_errors=True)


def import_from(project: str, module: str):
    """형제 프로젝트의 모듈을 이 프로세스로 import (계약 검사 함수 등 읽기만)."""
    import importlib
    sys.dont_write_bytecode = True        # 남의 폴더에 __pycache__ 를 만들지 않게
    d = str(project_dir(project))
    if d not in sys.path:
        sys.path.insert(0, d)
    return importlib.import_module(module)


def ro_sqlite(path: Path):
    import sqlite3
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
