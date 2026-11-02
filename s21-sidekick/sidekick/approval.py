"""rm -rf 결재 보고 (서버 쪽): 훅이 올린 결재 요청을 폰에 띄우고, 승인/반려를 기다린다.

폰이 할 수 있는 건 '승인' 또는 '반려' 표시뿐이다. 폰에서 온 어떤 글자도 실행되지 않는다.
"""
import re
import secrets
import threading
import time

from .triggers import normalize

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# 정규화(공백·문장부호 제거, 돼->되) 후 비교한다. 반려 단어가 하나라도 있으면 반려가 우선.
DENY_WORDS = ["안되", "안해", "하지마", "하지말", "아니", "반려", "거절", "취소", "멈춰", "그만",
              "스톱", "stop", "ㄴㄴ", "노노", "nope", "놉", "절대"]
DENY_EXACT = {"노", "no", "ㄴ"}
ALLOW_WORDS = ["승인", "ㅇㅇ", "그래", "좋아", "오케이", "해도되", "해줘", "해라", "해봐", "해요",
               "하세요", "하자", "진행", "허가", "고고", "yes", "okay"]
ALLOW_EXACT = {"응", "해", "네", "예", "웅", "ㅇ", "ok", "고", "콜"}  # "어"는 망설임일 수 있어 제외
ALLOW_PREFIX = ("응", "네", "웅")


def parse_voice_decision(text):
    """'allow' | 'deny' | None(못 알아들음). 애매하면 None → 폰이 다시 묻는다 (자동 승인 없음)."""
    n = normalize(text)
    if not n:
        return None
    if n in DENY_EXACT or any(w in n for w in DENY_WORDS):
        return "deny"
    if n in ALLOW_EXACT or n.startswith(ALLOW_PREFIX) or any(w in n for w in ALLOW_WORDS):
        return "allow"
    return None


def _clean(text, limit):
    t = _CONTROL.sub(" ", str(text or "")).strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def summarize(label, command, limit=60):
    cmd = " ".join(_clean(command, 2000).split())
    if len(cmd) > limit:
        cmd = cmd[: limit - 1] + "…"
    return ("%s: %s" % (label, cmd)) if label else cmd


class ApprovalBoard:
    def __init__(self, cfg, events, clock=time.monotonic, keep=50):
        self.cfg = cfg
        self.events = events
        self.clock = clock
        self.keep = keep
        self._items = {}  # id -> dict (삽입 순서 유지)
        self._cond = threading.Condition()

    # ---- 내부 ----
    def _expire_locked(self):
        now = self.clock()
        for it in self._items.values():
            if it["status"] == "pending" and now >= it["deadline"]:
                it["status"] = "expired"
                it["via"] = "timeout"
                self.events.push("approval_done", "결재 시간이 지나서 만료됐어요.", request_id=it["id"], status="expired")
                self._cond.notify_all()
        while len(self._items) > self.keep:
            oldest = next(iter(self._items))
            if self._items[oldest]["status"] == "pending":
                break
            del self._items[oldest]

    def _public(self, it):
        left = max(0, int(it["deadline"] - self.clock())) if it["status"] == "pending" else 0
        return {"id": it["id"], "status": it["status"], "via": it.get("via"), "label": it["label"],
                "command": it["command"], "description": it["description"], "tool": it["tool"],
                "summary": it["summary"], "say": it["say"], "expires_in": left}

    # ---- API ----
    def create(self, command, label="", description="", tool="", cwd="", timeout=None):
        command = _clean(command, 2000)
        if not command.strip():
            raise ValueError("command가 비었습니다")
        timeout = float(timeout if timeout is not None else self.cfg.get("timeout_seconds", 45))
        timeout = min(max(timeout, 5.0), 600.0)
        with self._cond:
            self._expire_locked()
            if sum(1 for i in self._items.values() if i["status"] == "pending") >= self.cfg.get("max_pending", 10):
                raise OverflowError("대기 중인 결재가 너무 많습니다")
            label = _clean(label, 80)
            summary = summarize(label, command)
            it = {"id": secrets.token_hex(8), "status": "pending", "via": None, "label": label,
                  "command": command, "description": _clean(description, 300), "tool": _clean(tool, 40),
                  "cwd": _clean(cwd, 300), "summary": summary,
                  "say": "사장님, %s 해도 되겠습니까?" % summary, "deadline": self.clock() + timeout}
            self._items[it["id"]] = it
            pub = self._public(it)
            # 이벤트 큐가 'id'를 이벤트 번호로 쓰므로 결재 id는 request_id로 보낸다
            extra = {k: v for k, v in pub.items() if k not in ("say", "id")}
            self.events.push("approval", it["say"], request_id=it["id"], **extra)
            return pub

    def get(self, rid):
        with self._cond:
            self._expire_locked()
            it = self._items.get(rid)
            return self._public(it) if it else None

    def wait(self, rid, wait=0.0):
        end = self.clock() + max(0.0, wait)
        real_end = time.monotonic() + max(0.0, wait)
        with self._cond:
            while True:
                self._expire_locked()
                it = self._items.get(rid)
                if it is None:
                    return None
                left = min(end - self.clock(), real_end - time.monotonic())
                if it["status"] != "pending" or left <= 0:
                    return self._public(it)
                self._cond.wait(min(left, 1.0))

    def decide(self, rid, decision=None, utterance=None, via="button"):
        """decision 'allow'/'deny' 또는 utterance(음성 문장). 못 알아들으면 상태 그대로 + understood=False."""
        heard = None
        if decision is None and utterance is not None:
            heard = _clean(utterance, 200)
            decision = parse_voice_decision(heard)
            via = "voice"
        if decision is not None and decision not in ("allow", "deny"):
            raise ValueError("decision은 allow 또는 deny")
        with self._cond:
            self._expire_locked()
            it = self._items.get(rid)
            if it is None:
                raise KeyError(rid)
            if decision is None:
                return dict(self._public(it), understood=False, heard=heard)
            if it["status"] == "pending":  # 먼저 온 결정만 유효
                it["status"] = decision
                it["via"] = via
                self.events.push("approval_done",
                                 "승인. 진행합니다." if decision == "allow" else "반려. 막았습니다.",
                                 request_id=rid, status=decision)
                self._cond.notify_all()
            return dict(self._public(it), understood=True, heard=heard)

    def pending(self):
        with self._cond:
            self._expire_locked()
            return [self._public(i) for i in self._items.values() if i["status"] == "pending"]
