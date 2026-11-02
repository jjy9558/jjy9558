# -*- coding: utf-8 -*-
"""작은 HTTP 서버: 토큰 인증, 기본 127.0.0.1.

POST /entry {text, mood?, flag?}          일기 추가
GET  /checkout/questions                  오늘의 3문답 문구
POST /checkout {win?, blocked?, next?}    3문답 저장
GET  /morning                             아침 브리핑(JSON)
GET  /search?q=...   GET /why?q=...       검색
GET  /health                              (인증 불필요)
인증: ``Authorization: Bearer <token>`` 또는 ``X-VJ-Token: <token>``.
"""
import hmac
import json
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import checkout as C
from .search import search_entries, why
from .store import Journal

MAX_BODY = 64 * 1024


def make_server(db_path, token, host="127.0.0.1", port=0, cors_origin=None, llm=None, now=None):
    if not token or len(token) < 8:
        raise ValueError("토큰은 8자 이상이어야 합니다")
    lock = threading.Lock()   # sqlite 쓰기 직렬화

    class Handler(BaseHTTPRequestHandler):
        server_version = "vjournal"

        def log_message(self, fmt, *args):   # 본문/토큰이 로그에 남지 않게 조용히
            pass

        def _cors(self):
            if cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Vary", "Origin")

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            got = self.headers.get("X-VJ-Token")
            auth = self.headers.get("Authorization", "")
            if not got and auth.lower().startswith("bearer "):
                got = auth[7:].strip()
            return bool(got) and hmac.compare_digest(got.encode("utf-8"), token.encode("utf-8"))

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-VJ-Token")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("Content-Length 가 잘못됨")
            if n > MAX_BODY:
                raise OverflowError("본문이 너무 큼")
            raw = self.rfile.read(n) if n else b""
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype == "text/plain":
                return {"text": raw.decode("utf-8", "replace")}
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                raise ValueError("JSON 이 아님")
            if not isinstance(data, dict):
                raise ValueError("JSON 객체가 필요함")
            return data

        def _route(self, method):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = urllib.parse.parse_qs(parsed.query)
            if method == "GET" and path == "/health":
                return self._send(200, {"ok": True})
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            try:
                data = self._body() if method == "POST" else {}
            except OverflowError as e:
                return self._send(413, {"error": str(e)})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            j = Journal(db_path, now=now, llm=llm)
            try:
                with lock:
                    return self._handle(j, method, path, qs, data)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            finally:
                j.close()

        def _handle(self, j, method, path, qs, data):
            if method == "POST" and path == "/entry":
                text = data.get("text")
                if not isinstance(text, str) or not text.strip():
                    return self._send(400, {"error": "text 가 필요함"})
                if len(text) > 20000:
                    return self._send(413, {"error": "text 가 너무 김"})
                res = j.add_entry(text, mood=data.get("mood"), flag=bool(data.get("flag")))
                return self._send(201, res)
            if method == "GET" and path == "/checkout/questions":
                return self._send(200, {"questions": [{"key": k, "text": q}
                                                       for k, q in C.questions_for(j.today())]})
            if method == "POST" and path == "/checkout":
                answers = data.get("answers") if isinstance(data.get("answers"), dict) else data
                answers = {k: v for k, v in answers.items() if k in C.KEYS and isinstance(v, str)}
                saved = C.save_answers(j, answers)
                return self._send(201, {"saved": len(saved)})
            if method == "GET" and path == "/morning":
                m = C.morning(j)
                m["text"] = C.render_morning(m)
                return self._send(200, m)
            if method == "GET" and path in ("/search", "/why"):
                q = (qs.get("q") or [""])[0].strip()
                if not q:
                    return self._send(400, {"error": "q 가 필요함"})
                if path == "/search":
                    hits = search_entries(j.entries(), q, limit=20)
                    return self._send(200, {"results": [
                        {"score": round(s, 3), "id": e["id"], "ts": e["ts"], "text": e["text"]} for s, e in hits]})
                return self._send(200, {"results": why(j.entries(), q, j.goals())})
            return self._send(404, {"error": "not found"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return ThreadingHTTPServer((host, port), Handler)


def load_or_create_token(path):
    import os
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            t = fh.read().strip()
        if len(t) >= 8:
            return t, False
    t = secrets.token_urlsafe(24)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(t + "\n")
    return t, True
