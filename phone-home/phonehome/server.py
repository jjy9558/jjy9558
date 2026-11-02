"""phone-home HTTP 서버: 런처, 로그인, 리버스 프록시(/s/<서비스>/...), 사장님 책상(/desk).

인증: 클라이언트 토큰 하나. ``X-Phone-Token`` 헤더 / ``Authorization: Bearer`` / 로그인 뒤 받는
HttpOnly·SameSite=Strict 쿠키(토큰에서 HMAC으로 만든 값, 토큰 원문 아님). 모두 상수 시간 비교.
클라이언트가 보낸 토큰/쿠키/Authorization과 모든 뒤쪽 토큰 헤더는 버리고, 서버가 뒤쪽 토큰을 붙인다.
"""
import hashlib
import hmac
import re
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from ._jjkit.httpkit import HttpError, TokenHandler, make_server as _make_server, route, token_matches
from . import pages
from .desk import Desk, DeskError
from .proxy import Backend, BackendError, RESPONSE_DROP, health_all, safe_rest, split_service_path

COOKIE = "ph_session"
HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}$|^\[[0-9A-Fa-f:.]{2,45}\]$")


def session_value(token: str) -> str:
    return hmac.new(token.encode("utf-8"), b"phone-home-session-v1", hashlib.sha256).hexdigest()


class App:
    def __init__(self, cfg: dict, clock=time.time, runner=None):
        self.cfg = cfg
        srv = cfg["server"]
        self.token = srv.get("token") or ""
        self.max_body = int(float(srv.get("max_body_mb") or 8) * 1024 * 1024)
        max_resp = int(float(srv.get("max_response_mb") or 64) * 1024 * 1024)
        headers = set()
        for s in cfg["services"].values():
            if s.get("token_header"):
                headers.add(s["token_header"])
            headers.update((s.get("headers") or {}).keys())
        self.backends = {n: Backend(n, s, headers, max_resp) for n, s in cfg["services"].items()}
        kw = {"clock": clock}
        if runner is not None:
            kw["runner"] = runner
        self.desk = Desk(cfg["desk"], self.backends, **kw)
        self.clock = clock
        self._fail_lock = threading.Lock()
        self._fails: list[float] = []

    # 로그인 실패가 1분에 10번을 넘으면 잠깐 막는다(토큰 무차별 대입 완화)
    def login_blocked(self) -> bool:
        now = self.clock()
        with self._fail_lock:
            self._fails = [t for t in self._fails if now - t < 60]
            return len(self._fails) >= 10

    def login_failed(self) -> None:
        with self._fail_lock:
            self._fails.append(self.clock())

    def body_limit(self, name: str) -> int:
        mb = self.cfg["services"][name].get("max_body_mb")
        return int(float(mb) * 1024 * 1024) if mb is not None else self.max_body

    def tiles(self) -> list[dict]:
        out = []
        for name, s in self.cfg["services"].items():
            out.append({"name": name, "title": s["title"], "icon": s["icon"], "desc": s.get("desc") or "",
                        "link": self.backends[name].page_link(), "quick": s.get("quick") or None})
        return out


class Handler(TokenHandler):
    server_version = "PhoneHome/0.1"
    token_header = "X-Phone-Token"

    # ---- 인증 ----
    def _cookie_ok(self) -> bool:
        tok = getattr(self.server, "token", "") or ""
        if not tok:
            return False
        raw = self.headers.get("Cookie") or ""
        try:
            c = SimpleCookie()
            c.load(raw)
        except Exception:
            return False
        m = c.get(COOKIE)
        return bool(m) and hmac.compare_digest(m.value.encode("utf-8"), session_value(tok).encode("utf-8"))

    def authorized(self, query=None) -> bool:
        tok = getattr(self.server, "token", "") or ""
        return token_matches(self.supplied_token(None), tok) or self._cookie_ok()

    def _secure(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _set_cookie_header(self, value: str, max_age: int) -> dict:
        parts = ["%s=%s" % (COOKIE, value), "Path=/", "HttpOnly", "SameSite=Strict", "Max-Age=%d" % max_age]
        if self._secure():
            parts.append("Secure")
        return {"Set-Cookie": "; ".join(parts)}

    def _require_json(self) -> dict:
        # 우리 쪽 POST는 JSON만: 다른 사이트의 폼 전송(CSRF)은 이 형식을 못 보낸다
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            raise HttpError(415, "Content-Type: application/json 이 필요합니다")
        return self.read_json(64 * 1024)

    # ---- 라우팅 보강: passthrough 서버는 모든 경로를 한 서비스로 ----
    def find_route(self, method, path):
        if getattr(self.server, "passthrough", None):
            return "r_passthrough", True, path
        return super().find_route(method, path)

    def do_PATCH(self):
        self._dispatch("PATCH")

    # ---- 페이지 ----
    @route("GET", "/", auth=False)
    def r_index(self, q):
        if not self.authorized():
            self.send_html(401, pages.login_page())
            return None
        self.send_html(200, pages.launcher_page(self.app.tiles()))
        return None

    @route("GET", "/desk", auth=False)
    def r_desk(self, q):
        if not self.authorized():
            self.send_html(401, pages.login_page())
            return None
        snap = self.app.desk.snapshot()
        self.send_html(200, pages.desk_page(snap))
        return None

    @route("GET", "/favicon.ico", auth=False)
    def r_favicon(self, q):
        self.send_bytes(204, b"")
        return None

    @route("POST", "/login", auth=False)
    def r_login(self, q):
        if self.app.login_blocked():
            raise HttpError(429, "잠시 뒤에 다시 시도하세요")
        body = self._require_json()
        tok = str(body.get("token") or "").strip()
        if not token_matches(tok, getattr(self.server, "token", "") or ""):
            self.app.login_failed()
            raise HttpError(401)
        days = int(self.app.cfg["server"].get("cookie_days") or 30)
        self.send_json(200, {"ok": True}, self._set_cookie_header(session_value(self.server.token), days * 86400))
        return None

    @route("POST", "/logout", auth=False)
    def r_logout(self, q):
        self.send_json(200, {"ok": True}, self._set_cookie_header("", 0))
        return None

    @route("GET", "/go/", prefix=True)
    def r_go(self, q):
        name = self.route_rest
        b = self.app.backends.get(name)
        if b is None or not b.svc.get("page"):
            raise HttpError(404)
        page = b.svc["page"]
        if b.svc["path_mode"] == "passthrough":
            host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "127.0.0.1")
            host = host.split(",")[0].strip()
            hostname = host.rsplit(":", 1)[0] if not host.endswith("]") else host
            if not HOST_RE.match(hostname):
                raise HttpError(400, "Host 헤더 오류")
            scheme = "https" if self._secure() else "http"
            loc = "%s://%s:%d%s" % (scheme, hostname, int(b.svc["listen_port"]), page)
        else:
            loc = "/s/%s%s" % (name, page)
        self.send_bytes(302, b"", "text/plain", {"Location": loc})
        return None

    # ---- API ----
    @route("GET", "/api/health")
    def r_health(self, q):
        t = float(self.app.cfg["server"].get("health_timeout") or 3)
        return 200, {"services": health_all(self.app.backends, t)}

    @route("GET", "/api/tiles")
    def r_tiles(self, q):
        return 200, {"tiles": self.app.tiles()}

    @route("GET", "/desk/api/items")
    def r_desk_items(self, q):
        return 200, self.app.desk.snapshot()

    def _desk_call(self, fn, *args):
        try:
            return 200, fn(*args)
        except DeskError as e:
            raise HttpError(e.code, e.message)

    @route("POST", "/desk/api/stamp")
    def r_desk_stamp(self, q):
        b = self._require_json()
        return self._desk_call(self.app.desk.stamp, str(b.get("key") or ""), str(b.get("stamp") or ""))

    @route("POST", "/desk/api/action")
    def r_desk_action(self, q):
        b = self._require_json()
        return self._desk_call(self.app.desk.run_action, b.get("action"), str(b.get("key") or ""))

    @route("POST", "/desk/api/decide")
    def r_desk_decide(self, q):
        b = self._require_json()
        return self._desk_call(self.app.desk.decide, str(b.get("source") or ""), str(b.get("id") or ""),
                               str(b.get("decision") or ""))

    # ---- 프록시 ----
    @route("GET", "/s/", prefix=True)
    def r_proxy_get(self, q):
        return self._proxy()

    @route("POST", "/s/", prefix=True)
    def r_proxy_post(self, q):
        return self._proxy()

    @route("PUT", "/s/", prefix=True)
    def r_proxy_put(self, q):
        return self._proxy()

    @route("DELETE", "/s/", prefix=True)
    def r_proxy_delete(self, q):
        return self._proxy()

    @route("PATCH", "/s/", prefix=True)
    def r_proxy_patch(self, q):
        return self._proxy()

    def _proxy(self):
        parts = split_service_path(self.path)
        if parts is None or parts[0] not in self.app.backends:
            raise HttpError(404)
        name, rest, query = parts
        b = self.app.backends[name]
        if b.svc["path_mode"] != "prefix":
            raise HttpError(404)
        if rest is None:
            self.send_bytes(302, b"", "text/plain", {"Location": "/s/%s/" % name})
            return None
        return self._forward(b, rest, query, rewrite=True)

    def r_passthrough(self, q):
        b = self.app.backends[self.server.passthrough]
        u = urlparse(self.path)
        if not self.path.startswith("/"):
            raise HttpError(400)
        if not safe_rest(u.path):
            raise HttpError(404)
        return self._forward(b, u.path, u.query, rewrite=False)

    def _forward(self, b: Backend, rest: str, query: str, rewrite: bool):
        method = "GET" if self.command == "HEAD" else self.command
        body = None
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            body = self.read_body(self.app.body_limit(b.name))
        try:
            status, headers, data = b.request(method, rest, query, body, b.forward_headers(self.headers))
        except BackendError as e:
            self.send_json(502, {"error": "뒤쪽 서비스(%s)에 연결할 수 없습니다" % b.name, "detail": str(e)})
            return None
        ctype = "application/octet-stream"
        extra = {}
        for k, v in headers:
            lk = k.lower()
            if lk == "content-type":
                ctype = v
            elif lk == "location" and rewrite:
                extra[k] = b.rewrite_location(v)
            elif lk not in RESPONSE_DROP:
                extra[k] = v
        if rewrite and ctype.lower().startswith("text/html"):
            try:
                data = b.rewrite_html(data.decode("utf-8")).encode("utf-8")
            except UnicodeDecodeError:
                pass
        self.send_bytes(status, data, ctype, extra)
        return None


def make_server(app: App, host: str | None = None, port: int | None = None, quiet: bool = True):
    srv_cfg = app.cfg["server"]
    host = host if host is not None else srv_cfg["host"]
    port = port if port is not None else int(srv_cfg["port"])
    if not app.token:
        raise ValueError("server.token 이 비어 있습니다 (python -m phonehome token new)")
    return _make_server(Handler, host=host, port=port, token=app.token, quiet=quiet, app=app)


def make_passthrough_servers(app: App, host: str | None = None, quiet: bool = True, port_override=None):
    """path_mode=passthrough 서비스마다 포트 하나. 쿠키는 포트를 가리지 않아서 로그인은 그대로 통한다."""
    host = host if host is not None else app.cfg["server"]["host"]
    out = []
    for name, s in app.cfg["services"].items():
        if s["path_mode"] != "passthrough":
            continue
        port = port_override(name) if port_override else int(s["listen_port"])
        out.append(_make_server(Handler, host=host, port=port, token=app.token, quiet=quiet, app=app,
                                passthrough=name))
    return out
