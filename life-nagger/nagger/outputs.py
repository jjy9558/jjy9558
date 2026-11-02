"""출력 채널: 콘솔 / 토스트 / ntfy / s21-sidekick say. 네트워크 호출은 http_post 주입으로 가짜 가능."""
import json
import os
import urllib.error
import urllib.request

from .model import LEVELS, MODULE_LABELS


def http_post(url, data, headers=None, timeout=10):
    """(status, body_bytes). HTTP 오류 코드도 예외 대신 반환. 네트워크 자체 오류는 예외."""
    req = urllib.request.Request(url, data=data, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if hasattr(e, "read") else b""


def _label(msg):
    return MODULE_LABELS.get(msg.module, msg.module)


class Console:
    def __init__(self, cfg, write=print):
        self.min_level = LEVELS[cfg.get("min_level", "info")]
        self.write = write

    def send(self, msg, now):
        self.write("[%s] [%s] %s" % (now.strftime("%H:%M"), _label(msg), msg.text))


class Toast:
    def __init__(self, cfg, platform, write=print):
        self.min_level = LEVELS[cfg.get("min_level", "warn")]
        self.method = cfg.get("method", "auto")
        self.platform = platform
        self.write = write

    def send(self, msg, now):
        ok = self.platform.toast("life-nagger - " + _label(msg), msg.text, self.method)
        if not ok:   # 토스트도 msg도 안 되면 최소한 눈에 띄게 출력
            self.write("*** [%s] %s ***" % (_label(msg), msg.text))


class Ntfy:
    def __init__(self, cfg, post=http_post):
        self.min_level = LEVELS[cfg.get("min_level", "warn")]
        self.url = cfg.get("url", "https://ntfy.sh").rstrip("/")
        self.topic = cfg.get("topic", "")
        self.post = post
        if not self.topic:
            raise ValueError("outputs.ntfy.topic이 비었습니다")

    def send(self, msg, now):
        # 헤더는 latin-1만 안전하므로 한글 제목은 본문 첫머리에 넣는다.
        body = ("[%s] %s" % (_label(msg), msg.text)).encode("utf-8")
        self.post("%s/%s" % (self.url, self.topic), body, {"Content-Type": "text/plain; charset=utf-8"})


class SidekickSay:
    """s21-sidekick의 외부 'say' 엔드포인트로 말하게 한다.

    TODO(s21-sidekick): 현재 sidekick 서버(sidekick/server.py)에는 외부에서 폰에 말을 시키는
    엔드포인트가 없다 (events.say()는 내부 모듈용). 아래가 필요하다:
        POST /api/say  {"text": "...", "module": "nagger"}  ->  self.app.events.say(text, module=module)
    그 전까지 404/405를 받으면 이 채널은 스스로 꺼지고 `unsupported`가 True가 된다.
    """

    def __init__(self, cfg, post=http_post, environ=None):
        self.min_level = LEVELS[cfg.get("min_level", "warn")]
        self.base = cfg.get("base_url", "http://127.0.0.1:8765").rstrip("/")
        env = os.environ if environ is None else environ
        self.token = env.get("SIDEKICK_TOKEN") or cfg.get("token") or ""
        self.post = post
        self.unsupported = False

    def send(self, msg, now):
        if self.unsupported:
            return
        data = json.dumps({"text": msg.text, "module": "nagger-" + msg.module}).encode("utf-8")
        status, _ = self.post(self.base + "/api/say", data,
                              {"Content-Type": "application/json", "X-Sidekick-Token": self.token})
        if status in (404, 405):
            self.unsupported = True
        elif status >= 400:
            raise RuntimeError("sidekick say 실패: HTTP %d" % status)


class Notifier:
    def __init__(self, outputs):
        self.outputs = outputs
        self.errors = []

    def send(self, msg, now):
        for o in self.outputs:
            if msg.level < o.min_level:
                continue
            try:
                o.send(msg, now)
            except Exception as e:     # 한 채널 장애가 잔소리 전체를 막으면 안 됨
                self.errors.append("%s: %s" % (type(o).__name__, e))
                del self.errors[:-20]


def build_outputs(cfg, platform, write=print, post=http_post):
    oc, outs = cfg["outputs"], []
    if oc["console"]["enabled"]:
        outs.append(Console(oc["console"], write))
    if oc["toast"]["enabled"]:
        outs.append(Toast(oc["toast"], platform, write))
    if oc["ntfy"]["enabled"]:
        outs.append(Ntfy(oc["ntfy"], post))
    if oc["sidekick"]["enabled"]:
        outs.append(SidekickSay(oc["sidekick"], post))
    return Notifier(outs)
