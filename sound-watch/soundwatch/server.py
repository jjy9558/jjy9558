"""폰 -> PC 오디오 수신 서버 (ThreadingHTTPServer, 토큰 인증, 기본 127.0.0.1).

엔드포인트
  GET  /                  폰 페이지 (비밀 없음, 토큰 불필요)
  GET  /api/ping          연결/토큰 확인
  POST /api/audio         16 kHz 모노 Int16 LE PCM (보통 1초). ?session=&seq=&sr=
  GET  /api/status        현재 레벨, 최근 인식 기록, 템플릿 목록
토큰은 `X-SoundWatch-Token` 또는 `Authorization: Bearer` 헤더로만 받는다(쿼리스트링은 로그에 남으므로 불허).
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from . import audio
from .engine import Engine
from .store import Store
from .templates import TemplateStore

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' blob:; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; worker-src 'self' blob:; img-src 'self' data:; base-uri 'none'; form-action 'none'")


class SoundWatchApp:
    def __init__(self, cfg: Dict[str, Any], store: Store, notifier=None, templates: Optional[TemplateStore] = None,
                 clips_dir: Optional[str] = None, clock=time.time):
        self.cfg = cfg
        self.token = cfg["server"]["token"]
        if not self.token:
            raise ValueError("server.token 이 비어 있습니다")
        self.store = store
        self.clock = clock
        self.tstore = templates
        self.engine = Engine(cfg, list(templates.templates.values()) if templates else [], store=store,
                             notifier=notifier, clips_dir=clips_dir, clock=clock)
        self.lock = threading.Lock()
        self.session: Optional[str] = None
        self.chunks = 0
        self.bytes = 0
        self.last_seq = -1
        self.started = clock()
        self._last_reload_check = 0.0

    def _maybe_reload_templates(self):
        now = self.clock()
        if not self.tstore or now - self._last_reload_check < 5.0:
            return
        self._last_reload_check = now
        if self.tstore.changed_on_disk():
            try:
                self.tstore.load()
                self.engine.set_templates(list(self.tstore.templates.values()))
            except Exception:
                pass  # 쓰는 중인 파일일 수 있음: 다음에 다시 시도

    def ingest(self, raw: bytes, session: str, seq: int, sr: int) -> Dict[str, Any]:
        x = audio.int16_bytes_to_floats(raw)
        if sr != audio.TARGET_SR:
            x = audio.resample(x, sr, audio.TARGET_SR)
        with self.lock:
            self._maybe_reload_templates()
            if session != self.session:
                if self.session is not None:
                    self.engine.skip(0.0)   # 새 세션: 진행 중 이벤트 마감/상태 초기화
                self.session = session
                self.last_seq = -1
            dets = self.engine.feed(x, wall=self.clock())
            self.chunks += 1
            self.bytes += len(raw)
            self.last_seq = seq
            return {"ok": True, "level_db": round(self.engine.level_db(), 1), "chunks": self.chunks,
                    "detections": [d.to_dict() for d in dets]}

    def status(self) -> Dict[str, Any]:
        with self.lock:
            e = self.engine
            return {
                "ok": True, "chunks": self.chunks, "bytes": self.bytes, "session": self.session,
                "level_db": round(e.level_db(), 1), "level_label": e.cal.label(),
                "calibrated": e.cal.calibrated, "threshold_db": self.cfg["noise"]["threshold_db"],
                "uptime_s": round(self.clock() - self.started, 1),
                "templates": sorted(self.tstore.templates) if self.tstore else [],
                "recent": [d.to_dict() for d in list(e.detections)[-20:]],
                "summary": e.summary(),
            }

    def close(self):
        with self.lock:
            self.engine.flush()
        self.store.commit()


class Handler(BaseHTTPRequestHandler):
    app: SoundWatchApp = None  # type: ignore
    quiet = True
    server_version = "sound-watch"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 쿼리/토큰이 로그에 남지 않게 기본 침묵
        if not self.quiet:
            super().log_message(fmt, *args)

    # ------------------------------------------------------------ 응답
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _authorized(self) -> bool:
        tok = self.headers.get("X-SoundWatch-Token")
        if not tok:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                tok = auth[7:]
        if not tok:
            return False
        return hmac.compare_digest(tok.encode("utf-8"), self.app.token.encode("utf-8"))

    # ------------------------------------------------------------ 라우팅
    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html", "/phone"):
            try:
                with open(os.path.join(WEB_DIR, "phone.html"), "rb") as f:
                    page = f.read()
            except OSError:
                return self._json(500, {"error": "phone.html 없음"})
            return self._send(200, page, "text/html; charset=utf-8", {"Content-Security-Policy": CSP})
        if u.path == "/favicon.ico":
            return self._send(204, b"")
        if u.path.startswith("/api/"):
            if not self._authorized():
                return self._json(401, {"error": "토큰이 없거나 틀립니다"})
            if u.path == "/api/ping":
                return self._json(200, {"ok": True, "version": "sound-watch"})
            if u.path == "/api/status":
                return self._json(200, self.app.status())
            return self._json(404, {"error": "없는 경로"})
        return self._json(404, {"error": "없는 경로"})

    def do_POST(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._json(404, {"error": "없는 경로"})
        if not self._authorized():
            # 본문은 읽지 않고 연결을 닫는다 (미인증 요청에 큰 본문을 받아주지 않기 위해)
            self.close_connection = True
            return self._json(401, {"error": "토큰이 없거나 틀립니다"})
        if u.path != "/api/audio":
            return self._json(404, {"error": "없는 경로"})
        limit = int(self.app.cfg["server"].get("max_body_kb", 512)) * 1024
        cl = self.headers.get("Content-Length")
        if cl is None or not cl.isdigit():
            self.close_connection = True
            return self._json(411, {"error": "Content-Length 필요"})
        n = int(cl)
        if n > limit:
            self.close_connection = True
            return self._json(413, {"error": "본문이 너무 큽니다 (%d > %d 바이트)" % (n, limit)})
        raw = self.rfile.read(n)
        q = parse_qs(u.query)
        try:
            sr = int(q.get("sr", ["16000"])[0])
            seq = int(q.get("seq", ["0"])[0])
        except ValueError:
            return self._json(400, {"error": "sr/seq 는 정수여야 합니다"})
        session = (q.get("session", [""])[0] or "default")[:64]
        if not 8000 <= sr <= 48000:
            return self._json(400, {"error": "sr 범위는 8000~48000"})
        if n == 0 or n % 2:
            return self._json(400, {"error": "Int16 PCM 은 짝수 바이트여야 합니다"})
        try:
            res = self.app.ingest(raw, session, seq, sr)
        except audio.AudioError as e:
            return self._json(400, {"error": str(e)})
        return self._json(200, res)


def make_server(app: SoundWatchApp, host: str = "127.0.0.1", port: int = 0, quiet: bool = True) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app, "quiet": quiet})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv
