"""침대 탈출 알람: 시간이 되면 울리고, 칫솔/세면대 사진이 승인되어야 멈춘다.

안전장치: 스누즈 횟수 제한, 비상 해제 코드, 최대 울림 시간(창) 이후 자동 종료.
"""
import datetime as dt
import hmac
import json
import random
import re
import threading

REJECT_DEFAULT = "아직 누워 있음."
JUDGE_SYSTEM = (
    "너는 기상 알람 심판이다. 사진 속 사람이 정말 침대에서 나왔는지 판정한다. "
    "반드시 JSON 한 줄로만 답한다: {\"approved\": true|false, \"reason\": \"짧은 한국어 이유\"}"
)


def parse_verdict(text):
    """모델 출력에서 (approved, reason)을 뽑는다. 애매하면 반려(실패 시 닫힘)."""
    t = (text or "").strip()
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            ap = obj.get("approved")
            if isinstance(ap, str):
                ap = ap.strip().lower() in ("true", "yes", "1", "approve", "approved", "승인")
            return bool(ap), str(obj.get("reason") or "").strip()
        except (ValueError, AttributeError):
            pass
    low = t.lower()
    neg = re.search(r"reject|denied|not approved|반려|거절|불합격|아직|누워", low)
    pos = re.search(r"\bapprove|\bapproved\b|\bpass\b|승인|통과|합격", low)
    if pos and not neg:
        return True, ""
    return False, ""


def _parse_hhmm(s):
    h, m = s.strip().split(":")
    return dt.time(int(h), int(m))


class Alarm:
    def __init__(self, cfg, vision=None, clock=None, rng=None):
        self.cfg = cfg
        self.vision = vision
        self.clock = clock or dt.datetime.now
        self.rng = rng or random.Random()
        self.time = _parse_hhmm(cfg["time"])
        self.enabled = cfg.get("enabled", True)
        self._states = {}
        self._override = None  # 테스트 울림 시작 시각
        self._armed_at = self.clock()  # 이보다 먼저 시작된 창은 무시 (설정 직후 바로 울리지 않게)
        self._fail = {"count": 0, "until": None}
        self._lock = threading.RLock()

    # ---- 상태 ----
    def _window(self, now):
        """현재 시각이 포함된 알람 창의 시작 시각, 없으면 None."""
        span = dt.timedelta(minutes=self.cfg["max_ring_minutes"])
        starts = []
        if self._override:
            starts.append(self._override)
        if self.enabled:
            today = dt.datetime.combine(now.date(), self.time)
            starts += [s for s in (today, today - dt.timedelta(days=1)) if s >= self._armed_at]
        for s in starts:
            if s <= now < s + span:
                return s
        return None

    def _state(self, start):
        return self._states.setdefault(start.isoformat(), {
            "dismissed": False, "snoozes": 0, "snoozed_until": None,
            "first_frame": None, "challenge": None, "how": None})

    def set_time(self, hhmm):
        with self._lock:
            self.time = _parse_hhmm(hhmm)
            self.cfg["time"] = "%02d:%02d" % (self.time.hour, self.time.minute)
            self._armed_at = self.clock()

    def ring_now(self):
        with self._lock:
            self._override = self.clock().replace(microsecond=0)
            self._states.pop(self._override.isoformat(), None)

    def status(self):
        with self._lock:
            now = self.clock()
            start = self._window(now)
            out = {"time": self.cfg["time"], "enabled": self.enabled, "ringing": False,
                   "snoozes_left": self.cfg["max_snoozes"],
                   "mode": "vision" if self.vision else "manual"}
            if not start:
                return out
            st = self._state(start)
            out["snoozes_left"] = self.cfg["max_snoozes"] - st["snoozes"]
            if st["dismissed"]:
                out["dismissed_by"] = st["how"]
                return out
            if st["snoozed_until"] and now < st["snoozed_until"]:
                out["snoozed_until"] = st["snoozed_until"].strftime("%H:%M:%S")
                return out
            out["ringing"] = True
            return out

    def _active_state(self):
        now = self.clock()
        start = self._window(now)
        if not start:
            return now, None
        st = self._state(start)
        if st["dismissed"]:
            return now, None
        return now, st

    def _dismiss(self, st, how):
        st["dismissed"] = True
        st["how"] = how
        self._override = None

    # ---- 동작 ----
    def snooze(self):
        with self._lock:
            now, st = self._active_state()
            if not st:
                return {"ok": False, "say": "지금은 울리는 알람이 없어요."}
            if st["snoozes"] >= self.cfg["max_snoozes"]:
                return {"ok": False, "say": "스누즈를 다 썼어요. 일어나서 칫솔을 보여주세요."}
            st["snoozes"] += 1
            st["snoozed_until"] = now + dt.timedelta(minutes=self.cfg["snooze_minutes"])
            left = self.cfg["max_snoozes"] - st["snoozes"]
            return {"ok": True, "snoozes_left": left,
                    "say": "%d분 뒤에 다시 울려요. 남은 스누즈 %d번." % (self.cfg["snooze_minutes"], left)}

    def emergency_off(self, code):
        with self._lock:
            now = self.clock()
            if self._fail["until"] and now < self._fail["until"]:
                wait = int((self._fail["until"] - now).total_seconds()) + 1
                return {"ok": False, "say": "코드를 너무 많이 틀렸어요. %d초 뒤에 다시." % wait}
            expected = str(self.cfg["emergency_code"]).strip()
            if expected and hmac.compare_digest(str(code).strip().encode(), expected.encode()):
                self._fail = {"count": 0, "until": None}
                _, st = self._active_state()
                if st:
                    self._dismiss(st, "emergency")
                self._override = None
                return {"ok": True, "say": "비상 해제했어요."}
            self._fail["count"] += 1
            if self._fail["count"] >= 5:
                self._fail = {"count": 0, "until": now + dt.timedelta(seconds=60)}
            return {"ok": False, "say": "비상 코드가 틀렸어요."}

    def check_photo(self, image_data_uri):
        with self._lock:
            now, st = self._active_state()
            if not st:
                return {"verdict": "idle", "say": "지금은 울리는 알람이 없어요."}
            if not self.vision:
                return self._manual_frame(now, st)
        # VLM 호출은 락 밖에서 (느릴 수 있음)
        prompt = ("통과 조건: %s. 사진이 조건을 만족하면 approved=true. 누워 있거나, 이불/천장/어두운 방이거나, "
                  "조건이 불확실하면 approved=false." % self.cfg["requirement"])
        try:
            raw = self.vision.ask_with_image(prompt, image_data_uri, system=JUDGE_SYSTEM, max_tokens=120)
        except Exception as e:
            return {"verdict": "error", "say": "판정 모델에 연결할 수 없어요. 비상 코드를 쓰세요.", "error": str(e)}
        approved, reason = parse_verdict(raw)
        with self._lock:
            now, st = self._active_state()
            if approved and st:
                self._dismiss(st, "photo")
                return {"verdict": "approved", "say": "승인. 좋은 아침이에요!" + (" " + reason if reason else "")}
        return {"verdict": "rejected", "say": "반려. " + (reason or REJECT_DEFAULT)}

    # ---- VLM 없을 때: 카메라 N초 유지 + 산수 ----
    def _manual_frame(self, now, st):
        need = self.cfg["fallback_camera_seconds"]
        if st["first_frame"] is None:
            st["first_frame"] = now
        elapsed = (now - st["first_frame"]).total_seconds()
        if elapsed < need:
            return {"verdict": "wait", "remaining": int(need - elapsed) + 1,
                    "say": "카메라를 계속 켜 두세요."}
        if not st["challenge"]:
            a, b, c = self.rng.randint(12, 39), self.rng.randint(3, 9), self.rng.randint(10, 99)
            st["challenge"] = ("%d × %d + %d = ?" % (a, b, c), a * b + c)
        return {"verdict": "challenge", "question": st["challenge"][0],
                "say": "문제를 풀면 꺼져요. " + st["challenge"][0]}

    def answer(self, value):
        with self._lock:
            _, st = self._active_state()
            if not st or not st["challenge"]:
                return {"ok": False, "say": "풀 문제가 없어요."}
            try:
                ok = int(str(value).strip()) == st["challenge"][1]
            except ValueError:
                ok = False
            if ok:
                self._dismiss(st, "manual")
                return {"ok": True, "say": "정답. 좋은 아침이에요!"}
            st["challenge"] = None  # 새 문제
            return {"ok": False, "say": "틀렸어요. 새 문제를 낼게요."}
