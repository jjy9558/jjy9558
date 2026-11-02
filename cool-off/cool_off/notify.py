"""ntfy(https://ntfy.sh) 알림. 네트워크 오류는 예외로 터뜨리지 않고 (False, 사유)로 돌려줍니다."""

from __future__ import annotations

import http.client
import re
import urllib.error
import urllib.request

NTFY_URL = "https://ntfy.sh"
_TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class NotifyError(Exception):
    """알림 설정이 잘못됐을 때."""


class Notifier:
    def __init__(self, topic: str, opener=None, timeout: float = 10.0, base_url: str = NTFY_URL):
        if not _TOPIC_RE.fullmatch(topic or ""):
            raise NotifyError("ntfy 주제는 영문/숫자/-/_ 조합으로 1~64자여야 해요.")
        self.topic = topic
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    def send(self, message: str, title: str = "cool-off") -> tuple:
        url = f"{self.base_url}/{self.topic}"
        request = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            method="POST",
            headers={"Title": title, "Content-Type": "text/plain; charset=utf-8"},
        )
        try:
            response = self.opener(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            return False, f"네트워크 오류: {exc.reason}"
        except (OSError, ValueError, http.client.HTTPException) as exc:
            return False, f"네트워크 오류: {exc or type(exc).__name__}"
        try:
            status = getattr(response, "status", None) or getattr(response, "code", None) or 200
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if not 200 <= int(status) < 300:
            return False, f"HTTP {status}"
        return True, "전송 완료"
