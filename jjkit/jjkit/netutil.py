"""HTTP 호출 공통 도우미 (urllib만 사용).

- ``is_local_url``: localhost, 루프백, 사설망, 링크 로컬, ``.local``/``.lan`` 등이면 True.
- ``opener_for``: 로컬 주소면 환경변수 프록시를 타지 않는 opener를 돌려준다.
  (회사/클라우드 프록시가 ``127.0.0.1`` 요청까지 가로채는 사고를 막는다.)
- ``request_json``: JSON을 보내고 JSON(또는 빈 본문)을 받는다. 실패는 ``NetError``.

opener는 ``open(request, timeout=...)`` 메서드가 있는 아무 객체나 된다. 테스트는 가짜 opener를 넣는다.
"""
import ipaddress
import json
import urllib.error
import urllib.request
from typing import Any, Protocol
from urllib.parse import urlparse

LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localhost")


class Opener(Protocol):
    def open(self, req: urllib.request.Request, timeout: float = ...) -> Any: ...


class NetError(Exception):
    """HTTP 호출 실패. ``status``는 HTTP 상태 코드(연결 실패면 None)."""

    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host in ("localhost", "ip6-localhost") or host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def no_proxy_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def opener_for(url: str, opener: Opener | None = None) -> Opener:
    """주입된 opener가 있으면 그것, 로컬 주소면 프록시를 끈 opener, 아니면 기본 opener."""
    if opener is not None:
        return opener
    if is_local_url(url):
        return no_proxy_opener()
    return urllib.request.build_opener()


def _decode_json(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def request_json(url: str, body: Any = None, *, method: str | None = None,
                 headers: dict[str, str] | None = None, opener: Opener | None = None,
                 timeout: float = 10.0) -> tuple[int, Any]:
    """``body``가 None이 아니면 JSON으로 보낸다. (상태 코드, 해석된 응답)을 돌려준다.

    4xx/5xx와 연결 실패는 ``NetError``로 바꾼다. 응답이 JSON이 아니면 문자열 그대로.
    """
    data = None
    hdrs = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs,
                                 method=method or ("POST" if data is not None else "GET"))
    op = opener_for(url, opener)
    try:
        with op.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return int(status), _decode_json(resp.read())
    except urllib.error.HTTPError as e:
        try:
            payload = _decode_json(e.read())
        except Exception:
            payload = None
        raise NetError("HTTP %d" % e.code, status=e.code, body=payload) from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise NetError("연결 실패: %s" % type(e).__name__) from e
