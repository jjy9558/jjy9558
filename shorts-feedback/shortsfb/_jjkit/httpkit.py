# generated from jjkit@01bc6d08; 직접 고치지 말 것
"""토큰 인증 HTTP 서버 기반 (``http.server`` 위에 얇게).

기준 구현: s21-sidekick/sidekick/server.py.

- 기본 바인드는 ``127.0.0.1``. 다른 주소에 열면서 토큰이 비어 있으면 ``make_server``가 거부한다.
- 토큰 비교는 ``hmac.compare_digest``. 서버 토큰이 비어 있으면 인증이 필요한 경로는 모두 401(실패 시 닫힘).
- 토큰은 ``token_header``(기본 ``X-Token``) 또는 ``Authorization: Bearer ...``.
  ``allow_query_token=True``면 ``?token=`` 도 받는다(폰 북마크용. 주소창/로그에 남는 점 주의).
- 요청 본문은 ``max_body`` 바이트(기본 1MiB)까지. 넘으면 413.
- 응답에는 ``Cache-Control: no-store``, ``X-Content-Type-Options: nosniff``, ``Referrer-Policy: no-referrer``.
- ``port=0``으로 열면 빈 포트를 고른다(테스트용). ``serve_in_thread``로 띄우고 ``srv.shutdown()``.

예::

    class H(TokenHandler):
        token_header = "X-Sidekick-Token"

        @route("GET", "/api/ping", auth=False)
        def ping(self, q):
            return 200, {"ok": True}

        @route("POST", "/api/say")
        def say(self, q):
            body = self.read_json()
            return 200, {"text": body.get("text", "")}

    srv = make_server(H, token="...", port=8765)
    srv.serve_forever()
"""
import hmac
import ipaddress
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

__all__ = ["HttpError", "TokenHandler", "route", "make_server", "serve_in_thread", "server_url",
           "generate_token", "token_matches", "is_loopback_host"]


class HttpError(Exception):
    def __init__(self, code: int, message: str = ""):
        super().__init__(message or str(code))
        self.code = code
        self.message = message or {400: "잘못된 요청", 401: "토큰이 없거나 틀렸습니다",
                                   403: "금지됨", 404: "not found", 405: "허용되지 않는 메서드",
                                   413: "요청이 너무 큽니다"}.get(code, "오류")


def route(method: str, path: str, *, auth: bool = True, prefix: bool = False) -> Callable:
    """핸들러 메서드에 붙이는 데코레이터. 메서드는 ``(self, query) -> (code, body) | None``.

    ``prefix=True``면 ``path``로 시작하는 모든 경로(긴 prefix 우선). 남은 부분은 ``self.route_rest``.
    body가 dict/list면 JSON, bytes면 그대로(``self.reply_type``), str이면 text/plain.
    None을 돌려주면 메서드가 직접 응답을 보낸 것으로 본다.
    """
    def deco(fn: Callable) -> Callable:
        routes = list(getattr(fn, "_jj_routes", []))
        routes.append((method.upper(), path, auth, prefix))
        fn._jj_routes = routes
        return fn
    return deco


def token_matches(supplied: str, expected: str) -> bool:
    return bool(expected) and bool(supplied) and hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8"))


def generate_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class TokenHandler(BaseHTTPRequestHandler):
    server_version = "jjkit/1"
    token_header = "X-Token"
    allow_query_token = False
    max_body = 1024 * 1024

    _exact: dict[tuple[str, str], tuple[str, bool]] = {}
    _prefix: list[tuple[str, str, str, bool]] = []

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        exact = dict(getattr(cls, "_exact", {}))
        pref = list(getattr(cls, "_prefix", []))
        for name, fn in list(vars(cls).items()):
            for method, path, auth, is_prefix in getattr(fn, "_jj_routes", []):
                if is_prefix:
                    pref = [p for p in pref if (p[0], p[1]) != (method, path)]
                    pref.append((method, path, name, auth))
                else:
                    exact[(method, path)] = (name, auth)
        pref.sort(key=lambda p: len(p[1]), reverse=True)
        cls._exact, cls._prefix = exact, pref

    # ---- 편의 속성 ----
    @property
    def app(self) -> Any:
        return getattr(self.server, "app", None)

    def log_message(self, fmt: str, *args: Any) -> None:  # 기본은 조용히 (본문/토큰이 로그에 남지 않게)
        if not getattr(self.server, "quiet", True):
            super().log_message(fmt, *args)

    # ---- 응답 ----
    def send_bytes(self, code: int, data: bytes, ctype: str = "application/octet-stream",
                   headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, code: int, obj: Any, headers: dict[str, str] | None = None) -> None:
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", headers)

    def send_html(self, code: int, html: str | bytes, headers: dict[str, str] | None = None) -> None:
        data = html.encode("utf-8") if isinstance(html, str) else html
        self.send_bytes(code, data, "text/html; charset=utf-8", headers)

    def send_error_json(self, code: int, message: str) -> None:
        self.send_json(code, {"error": message})

    # ---- 요청 ----
    def read_body(self, limit: int | None = None) -> bytes:
        lim = self.max_body if limit is None else limit
        raw_len = self.headers.get("Content-Length")
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            raise HttpError(411, "Content-Length가 필요합니다")
        try:
            n = int(raw_len or 0)
        except ValueError:
            raise HttpError(400, "Content-Length 형식 오류")
        if n < 0:
            raise HttpError(400, "Content-Length 형식 오류")
        if n > lim:
            self.close_connection = True
            raise HttpError(413)
        return self.rfile.read(n) if n else b""

    def read_json(self, limit: int | None = None) -> dict:
        raw = self.read_body(limit)
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "JSON 형식이 아닙니다")
        if not isinstance(obj, dict):
            raise HttpError(400, "JSON 객체가 필요합니다")
        return obj

    def supplied_token(self, query: dict[str, list[str]] | None = None) -> str:
        tok = self.headers.get(self.token_header) or ""
        auth = self.headers.get("Authorization") or ""
        if not tok and auth[:7].lower() == "bearer ":
            tok = auth[7:]
        if not tok and self.allow_query_token and query:
            tok = (query.get("token") or [""])[0]
        return tok.strip()

    def authorized(self, query: dict[str, list[str]] | None = None) -> bool:
        return token_matches(self.supplied_token(query), getattr(self.server, "token", "") or "")

    # ---- 라우팅 ----
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def find_route(self, method: str, path: str) -> tuple[str, bool, str] | None:
        hit = self._exact.get((method, path))
        if hit:
            return hit[0], hit[1], ""
        for m, p, name, auth in self._prefix:
            if m == method and path.startswith(p):
                return name, auth, path[len(p):]
        return None

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        self.route_rest = ""
        try:
            found = self.find_route(method, url.path)
            if found is None:
                known = any(p == url.path for (_, p) in self._exact) or any(
                    url.path.startswith(p) for (_, p, _, _) in self._prefix)
                raise HttpError(405 if known else 404)
            name, auth, self.route_rest = found
            if auth and not self.authorized(query):
                raise HttpError(401)
            result = getattr(self, name)(query)
            if result is None:
                return
            code, body = result
            if isinstance(body, (bytes, bytearray)):
                self.send_bytes(code, bytes(body), getattr(self, "reply_type", "application/octet-stream"))
            elif isinstance(body, str):
                self.send_bytes(code, body.encode("utf-8"), "text/plain; charset=utf-8")
            else:
                self.send_json(code, body)
        except HttpError as e:
            self.send_error_json(e.code, e.message)
        except ValueError as e:
            self.send_error_json(400, str(e)[:200])
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # 내부 사정(경로, 스택)은 밖으로 내보내지 않는다
            self.send_error_json(500, "서버 오류: %s" % type(e).__name__)


def make_server(handler: type[BaseHTTPRequestHandler], *, host: str = "127.0.0.1", port: int = 0,
                token: str = "", quiet: bool = True, app: Any = None,
                allow_public_without_token: bool = False, **attrs: Any) -> ThreadingHTTPServer:
    """서버를 만든다(아직 serve 안 함). ``srv.token``, ``srv.app``, ``srv.quiet`` 과 ``attrs``를 붙인다."""
    if not is_loopback_host(host) and not token and not allow_public_without_token:
        raise ValueError("%s 에 토큰 없이 열 수 없습니다 (127.0.0.1을 쓰거나 token을 설정)" % host)
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    srv.token = token
    srv.app = app
    srv.quiet = quiet
    for k, v in attrs.items():
        setattr(srv, k, v)
    return srv


def serve_in_thread(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    return t


def server_url(srv: ThreadingHTTPServer) -> str:
    host, port = srv.server_address[:2]
    if ":" in str(host):
        host = "[%s]" % host
    return "http://%s:%d" % (host, port)
