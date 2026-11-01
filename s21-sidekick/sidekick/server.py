"""HTTP 서버: 폰 페이지 + JSON API. 뼈대 = 폰이 듣고/보고 -> PC가 판단 -> 폰이 말하거나 PC가 행동."""
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .alarm import Alarm
from .butler import Butler
from .debugger import Debugger, default_runner
from .events import EventQueue
from .llm import client_from_config

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _spawn_thread(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


class Sidekick:
    """모든 모듈을 묶는 앱 객체. 테스트에서는 llm/vision/clock/runner/spawn을 주입한다."""

    def __init__(self, cfg, llm=None, vision=None, clock=None, runner=default_runner,
                 spawn=_spawn_thread, transcriber=None):
        self.cfg = cfg
        self.events = EventQueue()
        self.debugger = Debugger(cfg["debugger"], llm=llm, runner=runner)
        self.alarm = Alarm(cfg["alarm"], vision=vision, clock=clock)
        self.butler = Butler(cfg["butler"], clock=clock)
        self.spawn = spawn
        self.transcriber = transcriber

    @classmethod
    def from_config(cls, cfg, **kw):
        kw.setdefault("llm", client_from_config(cfg["llm"]))
        kw.setdefault("vision", client_from_config(cfg["vision"]))
        return cls(cfg, **kw)

    def token_ok(self, supplied):
        expected = self.cfg["server"]["token"]
        return bool(expected) and bool(supplied) and hmac.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8"))

    # ---- 혼잣말 디버거 ----
    def handle_utterance(self, text):
        status, phrase = self.debugger.check(text)
        if status == "trigger":
            self.events.say("잠깐, 볼게요.", module="debugger")
            self.spawn(self._investigate, text)
        return {"triggered": status == "trigger", "status": status or "ignored", "phrase": phrase,
                "cooldown_left": int(self.debugger.cooldown.remaining("debugger"))}

    def _investigate(self, text):
        try:
            summary = self.debugger.investigate(text)
        except Exception as e:
            summary = "조사하다가 문제가 생겼어요: %s" % e
        self.events.say(summary, module="debugger")


class Handler(BaseHTTPRequestHandler):
    server_version = "S21Sidekick/0.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):  # 발화 내용이 로그에 남지 않도록 경로만
        if not getattr(self.server, "quiet", False):
            super().log_message(fmt, *args)

    # ---- 응답 도우미 ----
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        limit = int(self.app.cfg["server"].get("max_body_mb", 8) * 1024 * 1024)
        if n > limit:
            raise ValueError("요청이 너무 큽니다")
        return self.rfile.read(n) if n else b""

    def _json(self):
        raw = self._body()
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise ValueError("JSON 형식이 아닙니다")
        if not isinstance(obj, dict):
            raise ValueError("JSON 객체가 필요합니다")
        return obj

    def _authorized(self):
        tok = self.headers.get("X-Sidekick-Token") or ""
        auth = self.headers.get("Authorization") or ""
        if not tok and auth.startswith("Bearer "):
            tok = auth[7:]
        return self.app.token_ok(tok.strip())

    # ---- 라우팅 ----
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        url = urlparse(self.path)
        path = url.path
        if method == "GET" and path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return self._send(401, {"error": "토큰이 없거나 틀렸습니다"})
        route = ROUTES.get((method, path))
        if not route:
            return self._send(404, {"error": "not found"})
        try:
            code, body = route(self, parse_qs(url.query))
        except ValueError as e:
            code, body = 400, {"error": str(e)}
        except Exception as e:
            code, body = 500, {"error": "서버 오류: %s" % type(e).__name__}
        self._send(code, body)

    # ---- 엔드포인트 ----
    def r_ping(self, q):
        return 200, {"ok": True}

    def r_config(self, q):
        c = self.app.cfg
        return 200, {"alarm": self.app.alarm.status(),
                     "motion": c["butler"]["motion"],
                     "triggers": c["debugger"]["triggers"],
                     "vision": bool(self.app.alarm.vision)}

    def r_events(self, q):
        since = int((q.get("since") or ["0"])[0])
        wait = min(float((q.get("wait") or ["0"])[0]), 30.0)
        return 200, {"events": self.app.events.since(since, wait), "last_id": self.app.events.last_id}

    def r_utterance(self, q):
        text = str(self._json().get("text") or "").strip()
        if not text:
            raise ValueError("text가 비었습니다")
        return 200, self.app.handle_utterance(text[:500])

    def r_transcribe(self, q):
        audio = self._body()
        if not audio:
            raise ValueError("오디오가 비었습니다")
        try:
            if self.app.transcriber:
                text = self.app.transcriber(audio)
            else:
                from .stt import transcribe
                text = transcribe(audio, self.app.cfg["stt"])
        except Exception as e:  # STTUnavailable 포함: 설치 안내를 그대로 전달
            return 503, {"error": str(e)}
        result = self.app.handle_utterance(text) if text else {"triggered": False, "status": "empty"}
        return 200, dict(result, text=text)

    def r_alarm_status(self, q):
        return 200, self.app.alarm.status()

    def r_alarm_check(self, q):
        img = str(self._json().get("image") or "")
        if not img.startswith("data:image/"):
            raise ValueError("image는 data:image/... 형식이어야 합니다")
        return 200, self.app.alarm.check_photo(img)

    def r_alarm_snooze(self, q):
        return 200, self.app.alarm.snooze()

    def r_alarm_emergency(self, q):
        return 200, self.app.alarm.emergency_off(self._json().get("code", ""))

    def r_alarm_answer(self, q):
        return 200, self.app.alarm.answer(self._json().get("answer", ""))

    def r_alarm_set(self, q):
        self.app.alarm.set_time(str(self._json().get("time") or ""))
        return 200, self.app.alarm.status()

    def r_alarm_test(self, q):
        self.app.alarm.ring_now()
        return 200, self.app.alarm.status()

    def r_arrival(self, q):
        source = str(self._json().get("source") or "button")
        result = self.app.butler.arrive(source=source)
        if result.get("say"):
            self.app.events.say(result["say"], module="butler")
        return 200, result


ROUTES = {
    ("GET", "/api/ping"): Handler.r_ping,
    ("GET", "/api/config"): Handler.r_config,
    ("GET", "/api/events"): Handler.r_events,
    ("POST", "/api/utterance"): Handler.r_utterance,
    ("POST", "/api/transcribe"): Handler.r_transcribe,
    ("GET", "/api/alarm/status"): Handler.r_alarm_status,
    ("POST", "/api/alarm/check"): Handler.r_alarm_check,
    ("POST", "/api/alarm/snooze"): Handler.r_alarm_snooze,
    ("POST", "/api/alarm/emergency"): Handler.r_alarm_emergency,
    ("POST", "/api/alarm/answer"): Handler.r_alarm_answer,
    ("POST", "/api/alarm/set"): Handler.r_alarm_set,
    ("POST", "/api/alarm/test"): Handler.r_alarm_test,
    ("POST", "/api/arrival"): Handler.r_arrival,
}


def make_server(app, host="127.0.0.1", port=0, quiet=False):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.app = app
    srv.quiet = quiet
    return srv
