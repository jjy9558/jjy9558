"""HTTP 서버: 폰 페이지 + JSON API. 뼈대 = 폰이 듣고/보고 -> PC가 판단 -> 폰이 말하거나 PC가 행동."""
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .alarm import Alarm
from .approval import ApprovalBoard
from .butler import Butler
from .debugger import Debugger, default_runner
from .departure import Departure
from .events import EventQueue
from .macro import MacroPad
from .llm import client_from_config
from .relay import Forwarder, clean_module, clean_say_text, forward_types, validate_event
from .stadium import RateLimiter, Stadium

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _spawn_thread(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


class Sidekick:
    """모든 모듈을 묶는 앱 객체. 테스트에서는 llm/vision/clock/runner/spawn/monotonic/rng를 주입한다."""

    def __init__(self, cfg, llm=None, vision=None, clock=None, runner=default_runner,
                 spawn=_spawn_thread, transcriber=None, monotonic=None, rng=None,
                 departure_registry=None, forward_post=None,
                 macro_launcher=None, macro_opener=None, macro_keys=None, macro_sleep=None):
        self.cfg = cfg
        self.events = EventQueue()
        self.debugger = Debugger(cfg["debugger"], llm=llm, runner=runner)
        self.alarm = Alarm(cfg["alarm"], vision=vision, clock=clock)
        self.butler = Butler(cfg["butler"], clock=clock)
        self.departure = Departure(cfg["departure"], clock=clock, monotonic=monotonic,
                                   registry=departure_registry, runner=runner)
        self.stadium = Stadium(cfg["stadium"], monotonic=monotonic, rng=rng)
        self.approvals = ApprovalBoard(cfg["approval"], self.events, clock=monotonic or time.monotonic)
        sc = cfg["say"]
        mono = {"clock": monotonic} if monotonic else {}
        self.say_limiter = RateLimiter(sc.get("max_per_minute", 12), sc.get("min_interval_seconds", 1), **mono)
        self.forwarder = Forwarder(cfg.get("forward_events") or [],
                                   **({"post": forward_post} if forward_post else {}))
        self.forward_limiter = RateLimiter(30, 0.5, **mono)
        self.spawn = spawn
        self.transcriber = transcriber
        mk = {k: v for k, v in (("launcher", macro_launcher), ("opener", macro_opener),
                                ("key_sender", macro_keys), ("sleep", macro_sleep)) if v is not None}
        self.macros = MacroPad(cfg["macro"], clock=clock, monotonic=monotonic, internal={
            "departure": self._macro_departure, "arrival": self._macro_arrival,
            "stadium": self._macro_stadium}, **mk)

    # ---- 매크로 패드가 재사용하는 내부 동작 (config에 적힌 것만 호출됨) ----
    def _macro_departure(self, item):
        r = self.departure.leave(source="button")
        if r.get("say"):
            self.events.say(r["say"], module="departure")
        return r

    def _macro_arrival(self, item):
        r = self.butler.arrive(source="button")
        if r.get("say"):
            self.events.say(r["say"], module="butler")
        return r

    def _macro_stadium(self, item):
        r = self.stadium.notify(item["event"], item.get("detail") or "")
        if r.get("rate_limited"):
            return {"ok": False, "error": "관중석 레이트 리밋"}
        if r.get("ok"):
            self.events.push("stadium", r["say"], sound=r["sound"], emoji=r["emoji"], event=r["event"])
        return r

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
            if (method, path) == ("POST", "/api/macro"):  # NFC 키는 이 경로에서만, 해당 매크로 하나에만 통한다
                return self._macro_nfc()
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
                     "macros": self.app.macros.public_list() if self.app.macros.enabled else [],
                     "motion": c["butler"]["motion"],
                     "triggers": c["debugger"]["triggers"],
                     "vision": bool(self.app.alarm.vision),
                     "forward_types": forward_types(c.get("forward_events"))}

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

    # ---- 외부 프로그램 -> 폰 말하기 ----
    def r_say(self, q):
        sc = self.app.cfg["say"]
        if not sc.get("enabled", True):
            return 403, {"error": "say가 꺼져 있습니다 (config say.enabled)"}
        b = self._json()
        text = clean_say_text(b.get("text"), int(sc.get("max_chars", 300)))
        if not text:
            raise ValueError("text가 비었습니다")
        ok, wait = self.app.say_limiter.try_acquire()
        if not ok:
            return 429, {"error": "너무 자주 보냈어요", "retry_after": int(wait) + 1}
        ev = self.app.events.say(text, module=clean_module(b.get("module")))
        return 200, {"ok": True, "id": ev["id"], "text": text}

    # ---- 폰 버튼 이벤트 -> 설정된 로컬 프로그램 (선택) ----
    def r_phone_event(self, q):
        b = self._json()
        kind, n = validate_event(b.get("type"), b.get("n", 1))
        if not self.app.forwarder.targets_for(kind):
            return 200, {"ok": False, "forwarded": 0, "reason": "forward_events에 이 type을 받는 곳이 없습니다"}
        ok, wait = self.app.forward_limiter.try_acquire()
        if not ok:
            return 429, {"error": "너무 자주 보냈어요", "retry_after": int(wait) + 1}
        self.app.spawn(self.app.forwarder.send, kind, n)
        return 200, {"ok": True, "forwarded": len(self.app.forwarder.targets_for(kind)), "type": kind}

    # ---- 매크로 패드 ----
    def _macro_nfc(self):
        """메인 토큰 없이 온 /api/macro: 헤더의 값이 body.id 매크로의 nfc_key일 때만 통과."""
        key = (self.headers.get("X-Sidekick-Token") or "").strip()
        try:
            b = self._json()
            mid = b.get("id")
        except ValueError:
            return self._send(401, {"error": "토큰이 없거나 틀렸습니다"})
        if not self.app.macros.nfc_ok(mid, key):
            self.app.macros.log(mid if isinstance(mid, str) else "?", "nfc", "denied")
            return self._send(401, {"error": "토큰이 없거나 틀렸습니다"})
        try:
            code, body = self._macro_run(b, "nfc")
        except Exception as e:
            code, body = 500, {"error": "서버 오류: %s" % type(e).__name__}
        self._send(code, body)

    def _macro_run(self, b, source):
        mid = b.get("id")
        if not isinstance(mid, str) or not mid:
            raise ValueError("id가 필요합니다")
        try:
            r = self.app.macros.run(mid, source=source, confirmed=b.get("confirmed") is True)
        except KeyError:
            self.app.macros.log(mid[:60], source, "unknown")
            return 404, {"error": "그런 매크로가 없습니다"}
        if r.get("disabled"):
            return 403, r
        if r.get("rate_limited"):
            return 429, dict(r, error="너무 자주 눌렀어요")
        if not r["ok"] and not r.get("needs_confirm"):
            return 502, r
        return 200, r

    def r_macro(self, q):
        return self._macro_run(self._json(), "tab")  # 메인 토큰으로 온 요청은 항상 tab

    def r_macro_audit(self, q):
        n = max(1, min(int((q.get("n") or ["50"])[0]), 200))
        return 200, {"audit": self.app.macros.recent_audit(n)}

    # ---- 현관 출발 체크 ----
    def r_departure(self, q):
        source = str(self._json().get("source") or "button")
        result = self.app.departure.leave(source=source)
        if result.get("say"):
            self.app.events.say(result["say"], module="departure")
        return 200, result

    # ---- 커밋 관중석 ----
    def r_notify(self, q):
        body = self._json()
        lines = body.get("lines")
        if lines is not None:
            try:
                lines = int(lines)
            except (TypeError, ValueError):
                raise ValueError("lines는 정수여야 합니다")
        r = self.app.stadium.notify(str(body.get("event") or ""), body.get("detail") or "", lines)
        if r.get("rate_limited"):
            return 429, dict(r, error="너무 자주 보냈어요. %d초 뒤에 다시." % r["retry_after"])
        if r.get("ok"):
            self.app.events.push("stadium", r["say"], sound=r["sound"], emoji=r["emoji"], event=r["event"])
        return 200, r

    # ---- rm -rf 결재 보고 ----
    def r_approval_request(self, q):
        b = self._json()
        try:
            return 200, self.app.approvals.create(
                str(b.get("command") or ""), label=str(b.get("label") or ""),
                description=str(b.get("description") or ""), tool=str(b.get("tool") or ""),
                cwd=str(b.get("cwd") or ""), timeout=b.get("timeout_seconds"))
        except OverflowError as e:
            return 429, {"error": str(e)}

    def r_approval_wait(self, q):
        rid = (q.get("id") or [""])[0]
        wait = min(float((q.get("wait") or ["0"])[0]), 30.0)
        r = self.app.approvals.wait(rid, wait)
        return (200, r) if r else (404, {"error": "그런 결재 요청이 없습니다"})

    def r_approval_decide(self, q):
        b = self._json()
        try:
            if b.get("utterance") is not None:
                r = self.app.approvals.decide(str(b.get("id") or ""), utterance=str(b["utterance"]))
            else:
                r = self.app.approvals.decide(str(b.get("id") or ""), decision=str(b.get("decision") or ""),
                                              via="button")
        except KeyError:
            return 404, {"error": "그런 결재 요청이 없습니다"}
        return 200, r

    def r_approval_pending(self, q):
        return 200, {"pending": self.app.approvals.pending()}


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
    ("POST", "/api/departure"): Handler.r_departure,
    ("POST", "/api/say"): Handler.r_say,
    ("POST", "/api/phone_event"): Handler.r_phone_event,
    ("POST", "/api/notify"): Handler.r_notify,
    ("POST", "/api/macro"): Handler.r_macro,
    ("GET", "/api/macro/audit"): Handler.r_macro_audit,
    ("POST", "/api/approval/request"): Handler.r_approval_request,
    ("GET", "/api/approval/wait"): Handler.r_approval_wait,
    ("POST", "/api/approval/decide"): Handler.r_approval_decide,
    ("GET", "/api/approval/pending"): Handler.r_approval_pending,
}


def make_server(app, host="127.0.0.1", port=0, quiet=False):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.app = app
    srv.quiet = quiet
    return srv
