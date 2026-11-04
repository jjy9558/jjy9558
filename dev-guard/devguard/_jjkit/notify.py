# generated from jjkit@90522d91; 직접 고치지 말 것
"""알림 보내기: ntfy, s21-sidekick ``/api/say``, jarvis-hub ``notify.say`` 작업, 콘솔.

기준 구현: jarvis-hub/jarvis/notifier.py(JSON 게시), steam-buddy/steam_buddy/notify.py(``_encode_header``).

ntfy 한글 제목 처리 방식 (선택과 이유)
--------------------------------------
HTTP 헤더는 latin-1만 담을 수 있어서 ``Title: 환불 기한`` 을 그대로 보내면 urllib가
``UnicodeEncodeError``를 내거나 글자가 깨진다. ntfy는 두 가지 방법을 지원한다.

1. **JSON 게시(기본, ``mode="json"``)**: 서버 루트(``https://ntfy.sh/``)에 ``{"topic", "title",
   "message", "priority", "tags", "click"}`` JSON을 POST 한다. 모든 값이 UTF-8 본문에 들어가므로
   인코딩 문제가 아예 없다. 태그(이모지 이름)도 한글이어도 된다.
2. **헤더 게시(``mode="headers"``)**: ``/<topic>`` 에 본문=메시지, 제목/태그는 헤더로 보낸다.
   비ASCII 헤더는 RFC 2047 ``=?UTF-8?B?...?=`` 로 감싼다(ntfy가 디코딩한다). JSON 게시를 막는
   프록시/구버전 서버용 대안이다.

알림 실패가 본 작업을 멈추면 안 되므로 ``Notifier.send``는 예외를 던지지 않고 채널별 결과만 돌려준다.
개별 함수(``ntfy``, ``sidekick_say``, ``jarvis_notify``)는 실패 시 ``NotifyError``를 던진다.
"""
import base64
import sys
import urllib.request
from typing import Any, Iterable, TextIO

from .netutil import NetError, Opener, opener_for, request_json

__all__ = ["NotifyError", "encode_header", "ntfy", "sidekick_say", "jarvis_notify", "console",
           "Notifier", "PRIORITIES"]

PRIORITIES = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5, "max": 5}


class NotifyError(Exception):
    pass


def encode_header(text: str, max_bytes: int = 45) -> str:
    """ASCII면 그대로, 아니면 RFC 2047 B 인코딩(UTF-8 글자 중간에서 자르지 않고 여러 조각)."""
    text = " ".join(str(text).split())  # 헤더에 줄바꿈 금지
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        pass
    words, cur = [], b""
    for ch in text:
        b = ch.encode("utf-8")
        if cur and len(cur) + len(b) > max_bytes:
            words.append(cur)
            cur = b""
        cur += b
    if cur:
        words.append(cur)
    return " ".join("=?UTF-8?B?" + base64.b64encode(w).decode("ascii") + "?=" for w in words)


def _priority(p: int | str | None) -> int | None:
    if p is None or p == "":
        return None
    if isinstance(p, str) and not p.isdigit():
        if p.lower() not in PRIORITIES:
            raise NotifyError("priority는 1~5 또는 min/low/default/high/urgent")
        return PRIORITIES[p.lower()]
    n = int(p)
    return max(1, min(5, n))


def build_ntfy(topic: str, message: str, *, title: str = "", priority: int | str | None = None,
               tags: Iterable[str] = (), click: str = "", server: str = "https://ntfy.sh",
               token: str = "", mode: str = "json") -> tuple[str, Any, dict[str, str], bytes | None]:
    """(url, json_body 또는 None, headers, raw_body) — 테스트와 디버깅용으로 따로 둔다."""
    if not topic:
        raise NotifyError("ntfy topic이 비었습니다")
    base = server.rstrip("/")
    pr = _priority(priority)
    tags = [t for t in tags if t]
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    match mode:
        case "json":
            body: dict[str, Any] = {"topic": topic, "message": message[:4000]}
            if title:
                body["title"] = title[:250]
            if pr:
                body["priority"] = pr
            if tags:
                body["tags"] = tags
            if click:
                body["click"] = click
            return base + "/", body, headers, None
        case "headers":
            if title:
                headers["Title"] = encode_header(title[:250])
            if pr:
                headers["Priority"] = str(pr)
            if tags:
                headers["Tags"] = encode_header(",".join(tags))
            if click:
                headers["Click"] = click
            headers["Content-Type"] = "text/plain; charset=utf-8"
            return base + "/" + topic, None, headers, message[:4000].encode("utf-8")
        case _:
            raise NotifyError("mode는 json 또는 headers")


def ntfy(topic: str, message: str, *, title: str = "", priority: int | str | None = None,
         tags: Iterable[str] = (), click: str = "", server: str = "https://ntfy.sh",
         token: str = "", mode: str = "json", opener: Opener | None = None,
         timeout: float = 10.0) -> int:
    """ntfy로 푸시. 성공하면 HTTP 상태 코드."""
    url, body, headers, raw = build_ntfy(topic, message, title=title, priority=priority, tags=tags,
                                         click=click, server=server, token=token, mode=mode)
    try:
        if raw is None:
            status, _ = request_json(url, body, headers=headers, opener=opener, timeout=timeout)
        else:
            req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
            with opener_for(url, opener).open(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                resp.read()
    except NetError as e:
        raise NotifyError("ntfy 전송 실패: %s" % e) from e
    except Exception as e:  # urllib 오류(헤더 모드)
        raise NotifyError("ntfy 전송 실패: %s" % type(e).__name__) from e
    return int(status)


def sidekick_say(text: str, *, token: str, url: str = "http://127.0.0.1:8765",
                 module: str = "jjkit", opener: Opener | None = None,
                 timeout: float = 5.0) -> dict:
    """s21-sidekick 폰이 소리 내어 읽게 한다: ``POST /api/say`` (헤더 ``X-Sidekick-Token``)."""
    if not text or not text.strip():
        raise NotifyError("text가 비었습니다")
    try:
        _, data = request_json(url.rstrip("/") + "/api/say",
                               {"text": text.strip(), "module": module},
                               headers={"X-Sidekick-Token": token}, opener=opener, timeout=timeout)
    except NetError as e:
        raise NotifyError("sidekick say 실패: %s" % e) from e
    return data if isinstance(data, dict) else {"ok": True}


def jarvis_notify(text: str, *, device: str, token: str, url: str = "http://127.0.0.1:8800",
                  title: str = "", module: str = "jjkit", caps: Iterable[str] = (),
                  priority: int = 0, opener: Opener | None = None, timeout: float = 5.0) -> dict:
    """jarvis-hub에 ``notify.say`` 작업을 넣는다: ``POST /api/jobs``
    (헤더 ``X-Jarvis-Device``/``X-Jarvis-Token``). 그 기기의 ``submit`` 목록에 ``notify.say``가 있어야 한다."""
    if not text or not text.strip():
        raise NotifyError("text가 비었습니다")
    body = {"type": "notify.say", "payload": {"text": text.strip()[:500], "module": module},
            "caps": list(caps), "priority": int(priority), "title": (title or text)[:120]}
    try:
        _, data = request_json(url.rstrip("/") + "/api/jobs", body,
                               headers={"X-Jarvis-Device": device, "X-Jarvis-Token": token},
                               opener=opener, timeout=timeout)
    except NetError as e:
        raise NotifyError("jarvis 작업 등록 실패: %s" % e) from e
    return data if isinstance(data, dict) else {}


def console(title: str, message: str = "", stream: TextIO | None = None) -> None:
    out = stream or sys.stdout
    line = "[알림] " + title if title else "[알림]"
    print(line, file=out)
    if message:
        for ln in message.splitlines():
            print("  " + ln, file=out)


class Notifier:
    """설정 dict 하나로 여러 채널에 보낸다. 실패해도 예외 없이 결과만 돌려준다.

    cfg 예::

        {"ntfy": {"topic": "...", "server": "https://ntfy.sh", "token": "", "mode": "json"},
         "sidekick": {"url": "http://127.0.0.1:8765", "token": "..."},
         "jarvis": {"url": "http://127.0.0.1:8800", "device": "pc", "token": "..."},
         "console": true}

    값이 비었거나 ``"enabled": false`` 인 채널은 건너뛴다.
    """

    def __init__(self, cfg: dict | None = None, *, opener: Opener | None = None,
                 stream: TextIO | None = None):
        self.cfg = cfg or {}
        self.opener = opener
        self.stream = stream

    def _on(self, name: str) -> dict | None:
        c = self.cfg.get(name)
        if c is True:
            return {}
        if not isinstance(c, dict) or c.get("enabled") is False:
            return None
        return c

    def channels(self) -> list[str]:
        out = []
        for name, need in (("ntfy", "topic"), ("sidekick", "token"), ("jarvis", "token")):
            c = self._on(name)
            if c is not None and c.get(need):
                out.append(name)
        if self._on("console") is not None:
            out.append("console")
        return out

    def send(self, title: str, message: str = "", *, priority: int | str | None = None,
             tags: Iterable[str] = (), speak: str | None = None) -> dict[str, str]:
        """채널별 ``"ok"`` 또는 ``"error: ..."``. ``speak``가 있으면 말하기 채널은 그 문장을 읽는다."""
        results: dict[str, str] = {}
        say = speak or (title + (". " + message if message else ""))
        for name in self.channels():
            c = self._on(name) or {}
            try:
                match name:
                    case "ntfy":
                        ntfy(c["topic"], message or title, title=title if message else "",
                             priority=priority if priority is not None else c.get("priority"),
                             tags=list(tags) or c.get("tags") or (), click=c.get("click", ""),
                             server=c.get("server", "https://ntfy.sh"), token=c.get("token", ""),
                             mode=c.get("mode", "json"), opener=self.opener)
                    case "sidekick":
                        sidekick_say(say, token=c["token"], url=c.get("url", "http://127.0.0.1:8765"),
                                     module=c.get("module", "jjkit"), opener=self.opener)
                    case "jarvis":
                        jarvis_notify(say, device=c.get("device", ""), token=c["token"],
                                      url=c.get("url", "http://127.0.0.1:8800"), title=title,
                                      module=c.get("module", "jjkit"), caps=c.get("caps", ()),
                                      opener=self.opener)
                    case "console":
                        console(title, message, self.stream)
                results[name] = "ok"
            except Exception as e:  # 알림 실패가 본 작업을 멈추면 안 된다
                results[name] = "error: %s" % e
        return results
