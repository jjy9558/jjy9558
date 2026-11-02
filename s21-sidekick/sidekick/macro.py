"""매크로 패드: 폰 버튼(또는 NFC 태그) 하나로 PC에서 *미리 설정된* 동작만 실행한다.

폰이 보내는 건 매크로 id뿐이다. 실행할 프로그램·URL·키·글자는 전부 config.json의 macro.items에 있다.
동작 종류: launch(프로그램+인자 목록, shell=False) / url / keys(Windows SendInput) / text / sidekick(내부 모듈) / chain.
"""
import collections
import datetime as dt
import hmac
import json
import re
import subprocess
import threading
import time
import webbrowser
from urllib.parse import urlparse

from . import winkeys
from .stadium import EVENTS as STADIUM_EVENTS, RateLimiter

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
TYPES = ("launch", "url", "keys", "text", "sidekick", "chain")
SAFE_URL_SCHEMES = ("http", "https", "steam", "vscode", "spotify")
INTERNAL_ACTIONS = ("departure", "arrival", "stadium")
MIN_NFC_KEY_CHARS = 20
MAX_CHAIN_DELAY = 10.0
MAX_CHAIN_TOTAL_DELAY = 30.0


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def chain_steps(item):
    """chain의 steps를 [(id, delay)]로 정규화. 형식이 틀리면 ValueError."""
    steps = item.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps는 비어 있지 않은 목록이어야 합니다")
    out = []
    for s in steps:
        if isinstance(s, str):
            sid, delay = s, 0
        elif isinstance(s, dict) and set(s) <= {"id", "delay"}:
            sid, delay = s.get("id"), s.get("delay", 0)
        else:
            raise ValueError("steps 항목은 id 문자열 또는 {\"id\", \"delay\"} 여야 합니다")
        if not isinstance(sid, str) or not sid:
            raise ValueError("steps 항목에 id가 없습니다")
        if not _num(delay) or delay < 0 or delay > MAX_CHAIN_DELAY:
            raise ValueError("delay는 0~%d초 숫자여야 합니다" % MAX_CHAIN_DELAY)
        out.append((sid, float(delay)))
    return out


def _check_item(it, p):
    """한 항목의 type별 필수 필드 검사 (chain 참조는 제외)."""
    t = it["type"]
    if t == "launch":
        prog, args = it.get("program"), it.get("args", [])
        if not isinstance(prog, str) or not prog.strip():
            p("program(실행 파일 경로)이 필요합니다")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            p("args는 문자열 목록이어야 합니다")
        if "cwd" in it and not isinstance(it["cwd"], str):
            p("cwd는 문자열이어야 합니다")
    elif t == "url":
        u = it.get("url")
        if not isinstance(u, str) or urlparse(u).scheme.lower() not in SAFE_URL_SCHEMES:
            p("url은 %s 주소여야 합니다" % "/".join(SAFE_URL_SCHEMES))
    elif t == "keys":
        try:
            winkeys.parse_combo(it.get("keys"))
        except winkeys.KeyParseError as e:
            p("keys: %s" % e)
    elif t == "text":
        tx = it.get("text")
        if not isinstance(tx, str) or not tx:
            p("text가 필요합니다")
        elif len(tx) > winkeys.MAX_TEXT:
            p("text는 %d자 이하여야 합니다" % winkeys.MAX_TEXT)
    elif t == "sidekick":
        a = it.get("action")
        if a not in INTERNAL_ACTIONS:
            p("action은 %s 중 하나여야 합니다" % "/".join(INTERNAL_ACTIONS))
        elif a == "stadium" and it.get("event") not in STADIUM_EVENTS:
            p("stadium은 event(%s)가 필요합니다" % "/".join(sorted(STADIUM_EVENTS)))
    elif t == "chain":
        try:
            chain_steps(it)
        except ValueError as e:
            p(str(e))


def validate_macros(mcfg, server_token=""):
    """문제 문자열 목록 (비어 있으면 정상). config.validate와 테스트가 쓴다."""
    problems = []
    items = mcfg.get("items", [])
    if not isinstance(items, list):
        return ["macro.items는 목록이어야 합니다."]
    by_id, keys_seen = {}, set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            problems.append("macro.items[%d]: 객체여야 합니다." % i)
            continue
        mid = it.get("id")
        name = "macro.items[%d](%s)" % (i, mid)

        def p(msg, name=name):
            problems.append("%s: %s" % (name, msg))
        if not isinstance(mid, str) or not ID_RE.match(mid):
            p("id는 영문·숫자·_- 1~40자여야 합니다")
        elif mid in by_id:
            p("id가 중복됩니다")
        else:
            by_id[mid] = it
        if not isinstance(it.get("label"), str) or not it["label"].strip():
            p("label이 필요합니다")
        if "emoji" in it and not isinstance(it["emoji"], str):
            p("emoji는 문자열이어야 합니다")
        if "color" in it and not (isinstance(it["color"], str) and COLOR_RE.match(it["color"])):
            p("color는 #rgb 또는 #rrggbb 여야 합니다")
        if "confirm" in it and not isinstance(it["confirm"], bool):
            p("confirm은 true/false여야 합니다")
        if it.get("type") not in TYPES:
            p("알 수 없는 type: %r (가능: %s)" % (it.get("type"), ", ".join(TYPES)))
        else:
            _check_item(it, p)
        if "nfc_key" in it:
            k = it["nfc_key"]
            if not isinstance(k, str) or len(k) < MIN_NFC_KEY_CHARS:
                p("nfc_key는 %d자 이상의 무작위 문자열이어야 합니다 (python -m sidekick macro-key)" % MIN_NFC_KEY_CHARS)
            elif k in keys_seen:
                p("nfc_key가 다른 매크로와 같습니다 (매크로마다 따로 만드세요)")
            elif server_token and k == server_token:
                p("nfc_key가 server.token과 같습니다")
            else:
                keys_seen.add(k)
    for mid, it in by_id.items():  # chain 참조: 존재해야 하고, 다른 chain은 금지(재귀 불가)
        if it.get("type") != "chain":
            continue
        try:
            steps = chain_steps(it)
        except ValueError:
            continue
        maxlen = int(mcfg.get("max_chain", 8))
        if len(steps) > maxlen:
            problems.append("macro.items(%s): chain은 최대 %d단계입니다" % (mid, maxlen))
        if sum(d for _, d in steps) > MAX_CHAIN_TOTAL_DELAY:
            problems.append("macro.items(%s): chain 지연 합계는 %d초 이하여야 합니다" % (mid, MAX_CHAIN_TOTAL_DELAY))
        for sid, _ in steps:
            if sid == mid:
                problems.append("macro.items(%s): 자기 자신을 부를 수 없습니다 (재귀)" % mid)
            elif sid not in by_id:
                problems.append("macro.items(%s): 없는 매크로 id: %s" % (mid, sid))
            elif by_id[sid].get("type") == "chain":
                problems.append("macro.items(%s): chain 안에 chain(%s)을 넣을 수 없습니다 (재귀 금지)" % (mid, sid))
    return problems


def default_launcher(argv, cwd=None):
    """shell=False. 인자 목록 그대로 실행하고 기다리지 않는다."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    kw = {"creationflags": flags} if flags else {"start_new_session": True}
    subprocess.Popen(argv, shell=False, cwd=cwd or None, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


class MacroError(Exception):
    """사용자에게 보여 줄 실패 사유."""


class MacroPad:
    def __init__(self, cfg, launcher=default_launcher, opener=None, key_sender=None, internal=None,
                 sleep=time.sleep, clock=None, monotonic=None, audit_path=None):
        self.cfg = cfg
        self.items = {it["id"]: it for it in cfg.get("items", [])}
        self.launcher = launcher
        self.opener = opener  # None이면 webbrowser.open
        self._key_sender = key_sender
        self.internal = internal or {}
        self.sleep = sleep
        self.clock = clock or dt.datetime.now
        mono = {"clock": monotonic} if monotonic else {}
        self.limiter = RateLimiter(cfg.get("max_per_minute", 20), cfg.get("min_interval_seconds", 0.5), **mono)
        self.audit = collections.deque(maxlen=int(cfg.get("audit_keep", 200)))
        self.audit_path = audit_path if audit_path is not None else (cfg.get("audit_file") or "")
        self._lock = threading.Lock()

    # ---- 목록 / 인증 ----
    @property
    def enabled(self):
        return bool(self.cfg.get("enabled", True))

    def public_list(self):
        """폰에 보여 줄 목록. 동작 내용(경로·URL·글자)과 nfc_key는 보내지 않는다."""
        return [{"id": i["id"], "label": i["label"], "emoji": i.get("emoji", ""), "color": i.get("color", ""),
                 "confirm": bool(i.get("confirm")), "type": i["type"]}
                for i in self.items.values() if not i.get("hidden")]

    def nfc_ok(self, mid, key):
        """이 key가 *이 매크로 하나*의 nfc_key인가. 상수 시간 비교."""
        it = self.items.get(mid) if isinstance(mid, str) else None
        expected = (it or {}).get("nfc_key")
        ok = bool(expected) and bool(key) and it.get("nfc_enabled", True) and hmac.compare_digest(
            str(key).encode("utf-8"), expected.encode("utf-8"))
        return bool(ok)

    # ---- 감사 로그 ----
    def log(self, mid, source, outcome, detail=""):
        entry = {"time": self.clock().isoformat(timespec="seconds"), "id": str(mid)[:60],
                 "source": source, "outcome": outcome}
        if detail:
            entry["detail"] = str(detail)[:200]
        with self._lock:
            self.audit.append(entry)
            if self.audit_path:
                try:
                    with open(self.audit_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError:
                    pass
        return entry

    def recent_audit(self, n=50):
        with self._lock:
            return list(self.audit)[-n:][::-1]

    # ---- 실행 ----
    def run(self, mid, source="tab", confirmed=False):
        """KeyError = 없는 id. 나머지는 결과 dict (ok / needs_confirm / rate_limited / error)."""
        if not self.enabled:
            return {"ok": False, "disabled": True, "error": "매크로 패드가 꺼져 있습니다 (macro.enabled)"}
        it = self.items[mid]
        base = {"id": mid, "label": it["label"], "emoji": it.get("emoji", "")}
        if it.get("confirm") and not confirmed:
            self.log(mid, source, "needs_confirm")
            return dict(base, ok=False, needs_confirm=True)
        ok, wait = self.limiter.try_acquire()
        if not ok:
            self.log(mid, source, "rate_limited")
            return dict(base, ok=False, rate_limited=True, retry_after=int(wait) + 1)
        try:
            msg = self._execute(it, source)
        except Exception as e:
            err = str(e) if isinstance(e, (MacroError, winkeys.WindowsOnlyError)) else \
                "%s: %s" % (type(e).__name__, e)
            self.log(mid, source, "error", err)
            return dict(base, ok=False, error=err)
        self.log(mid, source, "ok", msg)
        return dict(base, ok=True, message=msg)

    def _execute(self, it, source):
        t = it["type"]
        if t == "chain":
            return self._chain(it, source)
        return self._do(it)

    def _chain(self, it, source):
        done = 0
        for sid, delay in chain_steps(it):
            sub = self.items.get(sid)
            if sub is None or sub["type"] == "chain":  # validate가 막지만 방어
                raise MacroError("체인 단계 %s를 실행할 수 없습니다" % sid)
            if delay:
                self.sleep(delay)
            try:
                msg = self._do(sub)
            except Exception as e:
                self.log(sid, "chain:" + it["id"], "error", str(e))
                raise MacroError("체인 %d번째(%s) 실패: %s" % (done + 1, sid, e))
            self.log(sid, "chain:" + it["id"], "ok", msg)
            done += 1
        return "%d단계 실행" % done

    def _sender(self):
        if self._key_sender is None:
            self._key_sender = winkeys.default_sender()  # Windows가 아니면 WindowsOnlyError
        return self._key_sender

    def _do(self, it):
        t = it["type"]
        if t == "launch":
            argv = [it["program"]] + list(it.get("args", []))
            try:
                self.launcher(argv, it.get("cwd") or None)
            except OSError as e:
                raise MacroError("실행 실패: %s" % e)
            return "실행: %s" % it["program"]
        if t == "url":
            if not (self.opener or webbrowser.open)(it["url"]):
                raise MacroError("브라우저를 열지 못했습니다")
            return "열기: %s" % it["url"]
        if t == "keys":
            self._sender().send_combo(winkeys.parse_combo(it["keys"]))
            return "키: %s" % it["keys"]
        if t == "text":
            self._sender().type_text(it["text"])
            return "텍스트 %d자 입력" % len(it["text"])
        if t == "sidekick":
            fn = self.internal.get(it["action"])
            if not fn:
                raise MacroError("내부 동작 %s 없음" % it["action"])
            r = fn(it) or {}
            if r.get("ok") is False:
                raise MacroError(r.get("error") or ("%s 건너뜀(쿨다운/꺼짐)" % it["action"]))
            return "sidekick: %s" % it["action"]
        raise MacroError("알 수 없는 type")
