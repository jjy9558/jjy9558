"""수집/질의 HTTP 서버. 기본 127.0.0.1, 모든 /api/* 는 토큰 필요."""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import ask as askmod
from .core import FrameError

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "phone.html")


def make_server(app, host=None, port=None):
    token = app.cfg.get("token") or ""
    if len(token) < 8:
        raise ValueError("token 이 비어 있거나 너무 짧습니다 (config --init 으로 생성하거나 OBJMEM_TOKEN 환경변수 사용)")
    token_b = token.encode("utf-8")
    max_bytes = app.cfg["max_frame_bytes"]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 30
        server_version = "objmem"

        def log_message(self, fmt, *args):
            if app.cfg.get("quiet"):
                return
            sys.stderr.write("[objmem] %s - %s\n" % (self.address_string(), fmt % args))

        # -------------------------------------------------- 공통
        def _send(self, code, body, ctype="application/json; charset=utf-8", close=False):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            got = self.headers.get("X-ObjMem-Token") or ""
            if not got:
                a = self.headers.get("Authorization") or ""
                if a.lower().startswith("bearer "):
                    got = a[7:].strip()
            return hmac.compare_digest(got.encode("utf-8"), token_b)

        # -------------------------------------------------- GET
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                with open(STATIC, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if not u.path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            if u.path == "/api/ping":
                return self._send(200, {"ok": True})
            if u.path == "/api/status":
                return self._send(200, app.store.stats())
            if u.path == "/api/ask":
                text = (q.get("q") or [""])[0].strip()
                if not text or len(text) > 300:
                    return self._send(400, {"error": "q 가 비었거나 너무 깁니다"})
                llm = (q.get("llm") or [""])[0]
                use_llm = None if llm == "" else llm in ("1", "true", "yes")
                return self._send(200, askmod.answer(app, text, use_llm))
            if u.path == "/api/photo":
                try:
                    fid = int((q.get("id") or [""])[0])
                except ValueError:
                    return self._send(400, {"error": "id"})
                f = app.store.frame(fid)
                if f is None or not f["path"] or not os.path.isfile(f["path"]):
                    return self._send(404, {"error": "no photo"})
                with open(f["path"], "rb") as fh:
                    return self._send(200, fh.read(), "image/jpeg")
            return self._send(404, {"error": "not found"})

        # -------------------------------------------------- POST
        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/api/frame":
                return self._send(404, {"error": "not found"}, close=True)
            if not self._authed():
                return self._send(401, {"error": "unauthorized"}, close=True)   # 본문을 읽지 않음
            cl = self.headers.get("Content-Length")
            if cl is None or not cl.isdigit():
                return self._send(411, {"error": "Content-Length required"}, close=True)
            n = int(cl)
            if n > max_bytes:
                return self._send(413, {"error": "too large", "max": max_bytes}, close=True)
            data = self.rfile.read(n)
            if len(data) != n:
                return self._send(400, {"error": "short body"}, close=True)
            q = parse_qs(u.query)
            camera = (q.get("camera") or [""])[0]
            phash = (q.get("hash") or [""])[0] or None
            try:
                res = app.ingest_bytes(data, camera, phash)
            except FrameError as e:
                return self._send(415, {"error": str(e)})
            res.pop("path", None)               # 서버 경로는 밖으로 내보내지 않음
            return self._send(200, res)

    srv = ThreadingHTTPServer((host or app.cfg["host"], app.cfg["port"] if port is None else port), Handler)
    srv.daemon_threads = True
    return srv
