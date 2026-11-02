"""다른 로컬 프로그램과의 연결.

- 들어오는 말: POST /api/say {"text", "module"} -> 폰이 말한다 (예: life-nagger).
- 나가는 이벤트(선택, 기본 꺼짐): 폰의 '한숨' 같은 버튼 -> config.forward_events에 적힌 주소로만 POST.
  주소는 PC 설정에서만 정한다. 폰은 type 이름만 보낼 수 있고, 그것도 설정된 types 안에서만.
"""
import json
import os
import re
import urllib.request

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")
_MODULE = re.compile(r"[^\w.\-]")
_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
DEFAULT_TYPES = ["sigh"]


def clean_say_text(text, limit=300):
    t = " ".join(_CONTROL.sub(" ", str(text or "")).split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def clean_module(name):
    m = _MODULE.sub("", str(name or ""))[:40]
    return m or "external"


def forward_types(targets):
    out = set()
    for t in targets or []:
        out.update(t.get("types") or DEFAULT_TYPES)
    return sorted(x for x in out if _TYPE.match(x))


def http_post_json(url, body, headers, timeout=5):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=dict({"Content-Type": "application/json"}, **headers))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


class Forwarder:
    def __init__(self, targets, post=http_post_json, environ=None):
        self.targets = list(targets or [])
        self.post = post
        self.environ = os.environ if environ is None else environ

    def targets_for(self, kind):
        return [t for t in self.targets if t.get("url") and kind in (t.get("types") or DEFAULT_TYPES)]

    def send(self, kind, n=1):
        """대상마다 (url, 결과). 실패해도 예외를 밖으로 내지 않는다."""
        results = []
        for t in self.targets_for(kind):
            token = self.environ.get(t.get("token_env") or "", "") if t.get("token_env") else t.get("token", "")
            headers = {"X-Sidekick-Token": token} if token else {}
            try:
                results.append((t["url"], self.post(t["url"], {"type": kind, "n": n}, headers,
                                                     t.get("timeout", 5))))
            except Exception as e:
                results.append((t["url"], "error: %s" % type(e).__name__))
        return results


def validate_event(kind, n):
    if not isinstance(kind, str) or not _TYPE.match(kind):
        raise ValueError("type은 소문자 영문/숫자/_ (최대 32자)")
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 100:
        raise ValueError("n은 1~100 정수")
    return kind, n
