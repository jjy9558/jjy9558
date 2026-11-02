"""테스트 도우미: 가짜 뒤쪽 서비스(127.0.0.1 port=0), 임시 설정."""
import http.client
import json
import secrets
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from phonehome import config as C
from phonehome.server import App, make_server
from phonehome._jjkit.httpkit import serve_in_thread


def fake_token(tag: str) -> str:
    return "%s-%s" % (tag, secrets.token_hex(8))


class FakeBackend:
    """요청을 기록하고, routes[(method, path)] = (code, body, ctype) 로 답한다."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                path = self.path.split("?", 1)[0]
                outer.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                       "body": body})
                hit = outer.routes.get((self.command, path))
                if callable(hit):
                    hit = hit(self, body)
                code, data, ctype = hit or (404, {"error": "not found"}, None)
                if not isinstance(data, bytes):
                    data = json.dumps(data, ensure_ascii=False).encode("utf-8")
                    ctype = ctype or "application/json"
                self.send_response(code)
                self.send_header("Content-Type", ctype or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Set-Cookie", "backend=1")
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = _go

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        serve_in_thread(self.srv)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def dead_url() -> str:
    """아무도 듣지 않는 포트."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d" % port


class Harness:
    def __init__(self, services: dict, desk: dict | None = None, server: dict | None = None, runner=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.token = fake_token("client")
        d = {"cards_path": str(self.dir / "cards-latest.json"), "log_path": str(self.dir / "desk-log.jsonl")}
        d.update(desk or {})
        raw = {"server": dict({"token": self.token, "port": 0}, **(server or {})), "services": services, "desk": d}
        self.cfg = C.normalize(raw)
        self.app = App(self.cfg, runner=runner)
        self.srv = make_server(self.app, port=0)
        serve_in_thread(self.srv)
        self.port = self.srv.server_address[1]

    def request(self, method, path, body=None, headers=None, token=True):
        h = dict(headers or {})
        if token is True:
            h["X-Phone-Token"] = self.token
        elif token:
            h["X-Phone-Token"] = token
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.putrequest(method, path, skip_host="Host" in h, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if body is not None:
                c.putheader("Content-Length", str(len(body)))
            c.endheaders()
            if body:
                c.send(body)
            r = c.getresponse()
            data = r.read()
            return r.status, dict(r.getheaders()), data
        finally:
            c.close()

    def json(self, method, path, body=None, **kw):
        code, hdrs, data = self.request(method, path, body, **kw)
        try:
            return code, json.loads(data.decode("utf-8") or "{}")
        except ValueError:
            return code, {"_raw": data.decode("utf-8", "replace")}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()
