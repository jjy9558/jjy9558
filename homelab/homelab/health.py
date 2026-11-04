"""건강 확인: 포트가 열려 있는지, 서비스의 ping 경로가 어떻게 답하는지."""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from ._jjkit.netutil import NetError, request_json

# state: up | auth | error | down | none
LABELS = {"up": "정상", "auth": "토큰 거절", "error": "오류", "down": "응답 없음", "none": "-"}


@dataclass
class Health:
    state: str
    status: int | None = None
    ms: int = 0

    @property
    def label(self) -> str:
        base = LABELS.get(self.state, self.state)
        if self.state == "error" and self.status:
            return "%s(%d)" % (base, self.status)
        return base

    @property
    def ok(self) -> bool:
        return self.state == "up"


def port_in_use(host: str, port: int, timeout: float = 0.4) -> bool:
    """그 주소에 누가 듣고 있으면 True (연결해 보는 방식)."""
    if not port:
        return False
    if host in ("0.0.0.0", "", "::"):
        host = "127.0.0.1"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_http(url: str, headers: dict[str, str] | None = None, *, any_http_ok: bool = False,
               opener=None, timeout: float = 2.0) -> Health:
    t0 = time.monotonic()
    try:
        status, _ = request_json(url, headers=headers or {}, opener=opener, timeout=timeout)
    except NetError as e:
        ms = int((time.monotonic() - t0) * 1000)
        if e.status is None:
            return Health("down", None, ms)
        if any_http_ok:
            return Health("up", e.status, ms)
        if e.status in (401, 403):
            return Health("auth", e.status, ms)
        return Health("error", e.status, ms)
    return Health("up", status, int((time.monotonic() - t0) * 1000))
