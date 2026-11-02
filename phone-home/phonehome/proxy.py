"""뒤쪽 서비스 호출: 경로 검사, 헤더 거르기, 토큰 붙이기, HTML 경로 고쳐 쓰기, 건강 확인.

http.client를 직접 써서 환경변수 프록시를 타지 않는다(뒤쪽은 대개 127.0.0.1).
"""
import http.client
import json
import re
import threading
import time
from urllib.parse import unquote, urlparse

from .config import NAME_RE

# 클라이언트 요청에서 절대 넘기지 않는 헤더(소문자)
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
              "te", "trailer", "trailers", "transfer-encoding", "upgrade"}
CLIENT_DROP = HOP_BY_HOP | {"host", "content-length", "cookie", "authorization", "x-phone-token",
                            "accept-encoding", "origin", "referer"}
# 응답에서 버리는 헤더(send_bytes가 다시 붙이는 것 포함)
RESPONSE_DROP = HOP_BY_HOP | {"content-length", "content-type", "set-cookie", "content-encoding",
                              "cache-control", "x-content-type-options", "referrer-policy", "date", "server"}
SEGMENT_BAD = {"..", "."}


class BackendError(Exception):
    """뒤쪽에 연결 못 함 / 응답이 너무 큼 → 502."""


def split_service_path(raw_path: str) -> tuple[str, str | None, str] | None:
    """``/s/<name>/<rest>?<q>`` → (name, '/<rest>', query). 형식이 틀리거나 경로 탈출이면 None.

    rest가 None이면 ``/s/<name>`` (끝 슬래시 없음) → 호출하는 쪽이 리다이렉트.
    """
    if not raw_path.startswith("/s/"):
        return None
    path, _, query = raw_path.partition("?")
    path = path.split("#", 1)[0]
    name, sep, rest = path[3:].partition("/")
    if not NAME_RE.match(name):
        return None
    if not sep:
        return name, None, query
    rest = "/" + rest
    if not safe_rest(rest):
        return None
    return name, rest, query


def safe_rest(rest: str) -> bool:
    """퍼센트 인코딩을 두 번까지 풀어서 ``..``/``.`` 조각, 역슬래시, NUL, ``//`` 시작이 없을 때만 True."""
    seen = rest
    for _ in range(3):
        if "\\" in seen or "\x00" in seen or seen.startswith("//"):
            return False
        if any(seg in SEGMENT_BAD for seg in seen.split("/")):
            return False
        nxt = unquote(seen)
        if nxt == seen:
            break
        seen = nxt
    return True


class Backend:
    """설정의 서비스 하나."""

    def __init__(self, name: str, svc: dict, all_token_headers: set[str], max_response: int):
        self.name = name
        self.svc = svc
        u = urlparse(svc["url"])
        self.scheme, self.host = u.scheme, u.hostname
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.base_path = u.path.rstrip("/")
        self.timeout = float(svc.get("timeout") or 40)
        self.max_response = max_response
        self.drop = CLIENT_DROP | {h.lower() for h in all_token_headers}

    def page_link(self) -> str:
        """타일/책상에서 거는 링크. 실제 주소는 서버의 /go/<name> 이 정한다."""
        return "/go/%s" % self.name if self.svc.get("page") else ""

    # ---- 헤더 ----
    def auth_headers(self) -> dict[str, str]:
        h = {str(k): str(v) for k, v in (self.svc.get("headers") or {}).items()}
        if self.svc.get("token_header") and self.svc.get("token"):
            h[self.svc["token_header"]] = self.svc["token"]
        return h

    def forward_headers(self, client_headers) -> dict[str, str]:
        out = {}
        for k, v in client_headers.items():
            if k.lower() in self.drop:
                continue
            out[k] = v
        out["Accept-Encoding"] = "identity"
        out["X-Forwarded-Prefix"] = "/s/%s" % self.name
        out.update(self.auth_headers())  # 클라이언트 토큰 대신 뒤쪽 토큰
        return out

    # ---- 요청 ----
    def _conn(self, timeout: float):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=timeout)

    def request(self, method: str, rest: str, query: str = "", body: bytes | None = None,
                headers: dict[str, str] | None = None, timeout: float | None = None):
        """(status, [(k, v)...], body). 연결 실패·시간 초과·응답 과대면 BackendError."""
        target = (self.base_path + rest) or "/"
        if query:
            target += "?" + query
        hdrs = dict(headers if headers is not None else self.auth_headers())
        hdrs["Connection"] = "close"
        if body is not None:
            hdrs["Content-Length"] = str(len(body))
        conn = self._conn(timeout or self.timeout)
        try:
            conn.request(method, target, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read(self.max_response + 1)
            if len(data) > self.max_response:
                raise BackendError("응답이 너무 큽니다")
            return resp.status, resp.getheaders(), data
        except BackendError:
            raise
        except (OSError, http.client.HTTPException) as e:
            raise BackendError(type(e).__name__)
        finally:
            conn.close()

    def call_json(self, method: str, path: str, obj: dict | None = None, timeout: float = 8.0):
        """서버 내부용(사장님 책상): JSON 요청/응답. (status, dict)."""
        rest, _, query = path.partition("?")
        body = None
        headers = self.auth_headers()
        if obj is not None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, _, data = self.request(method, rest, query, body, headers, timeout)
        try:
            parsed = json.loads(data.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            parsed = {}
        return status, parsed if isinstance(parsed, dict) else {"value": parsed}

    def health(self, timeout: float = 3.0) -> dict:
        t0 = time.monotonic()
        try:
            status, _, _ = self.request("GET", self.svc.get("health") or "/", timeout=timeout)
        except BackendError as e:
            return {"ok": False, "state": "down", "error": str(e)}
        ms = int((time.monotonic() - t0) * 1000)
        if 200 <= status < 300:
            return {"ok": True, "state": "up", "code": status, "ms": ms}
        state = "auth" if status in (401, 403) else "error"
        return {"ok": False, "state": state, "code": status, "ms": ms}

    # ---- 응답 고쳐 쓰기 ----
    def rewrite_location(self, value: str) -> str:
        if value.startswith("/") and not value.startswith("//"):
            return "/s/%s%s" % (self.name, value)
        return value

    def rewrite_html(self, html: str) -> str:
        prefix = "/s/%s" % self.name
        for p in self.svc.get("rewrite") or []:
            if not isinstance(p, str) or not p.startswith("/"):
                continue
            html = re.sub(r"([\"'`])" + re.escape(p), lambda m: m.group(1) + prefix + p, html)
        html = re.sub(r"(\s(?:href|src|action)=[\"'])/(?!/|s/%s/)" % re.escape(self.name),
                      lambda m: m.group(1) + prefix + "/", html, flags=re.I)
        storage = self.svc.get("page_storage") or {}
        if storage:
            script = ("<script>(function(){var m=%s;try{for(var k in m){if(!localStorage.getItem(k))"
                      "localStorage.setItem(k,m[k]);}}catch(e){}})();</script>"
                      % json.dumps(storage).replace("<", "\\u003c"))
            m = re.search(r"<head[^>]*>", html, flags=re.I)
            html = html[:m.end()] + script + html[m.end():] if m else script + html
        return html


def health_all(backends: dict[str, Backend], timeout: float = 3.0) -> dict[str, dict]:
    """모든 서비스를 동시에 확인."""
    out: dict[str, dict] = {}
    lock = threading.Lock()

    def one(name, b):
        r = b.health(timeout)
        with lock:
            out[name] = r

    threads = [threading.Thread(target=one, args=(n, b), daemon=True) for n, b in backends.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 2)
    for n in backends:
        out.setdefault(n, {"ok": False, "state": "down", "error": "timeout"})
    return out
