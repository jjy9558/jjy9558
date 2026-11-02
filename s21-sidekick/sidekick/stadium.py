"""커밋 관중석: git push / CI / PR 머지 소식에 폰이 환호·야유·불꽃놀이로 반응한다.

소리는 폰에서 Web Audio로 합성한다 (녹음 파일 없음). PC는 어떤 소리·이모지·대사를 낼지만 정한다.
"""
import random
import re
import threading
import time
from collections import deque

EVENTS = {
    "push_ok": {"sound": "cheer", "emoji": "🎉",
                "lines": ["푸시 성공! 관중석이 들썩입니다!", "푸시 성공! 나이스 샷!"]},
    "push_fail": {"sound": "boo", "emoji": "👎",
                  "lines": ["푸시 실패. 관중석에서 야유가 쏟아집니다.", "푸시가 막혔어요. 우우우."]},
    "ci_fail": {"sound": "boo", "emoji": "🍅",
                "lines": ["CI가 터졌습니다! 우우우!", "빨간불이에요. CI 실패."]},
    "ci_ok": {"sound": "cheer", "emoji": "✅",
              "lines": ["CI 통과! 초록불입니다!", "CI 올 그린! 나이스 플레이!"]},
    "pr_merged": {"sound": "fireworks", "emoji": "🎆",
                  "lines": ["PR 머지! 축포를 쏘아 올립니다!", "머지 완료! 오늘의 MVP!"]},
}
SMALL_PR = {"sound": "cheer", "emoji": "👏", "lines": ["PR 머지! 깔끔합니다."]}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_detail(text, limit=80):
    t = " ".join(_CONTROL.sub(" ", str(text or "")).split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


class RateLimiter:
    """슬라이딩 윈도(분당 N회) + 최소 간격. 연속 push 스팸에도 폰이 미쳐 날뛰지 않게."""

    def __init__(self, max_per_minute=4, min_interval=3.0, clock=time.monotonic):
        self.max = max(1, int(max_per_minute))
        self.min_interval = float(min_interval)
        self.clock = clock
        self._times = deque()
        self._lock = threading.Lock()

    def try_acquire(self):
        """(허용 여부, 다시 시도까지 남은 초)."""
        with self._lock:
            now = self.clock()
            while self._times and now - self._times[0] >= 60:
                self._times.popleft()
            wait = 0.0
            if self._times and now - self._times[-1] < self.min_interval:
                wait = self.min_interval - (now - self._times[-1])
            if len(self._times) >= self.max:
                wait = max(wait, 60 - (now - self._times[0]))
            if wait > 0:
                return False, wait
            self._times.append(now)
            return True, 0.0


class Stadium:
    def __init__(self, cfg, monotonic=None, rng=None):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.limiter = RateLimiter(cfg.get("max_per_minute", 4), cfg.get("min_interval_seconds", 3),
                                   **({"clock": monotonic} if monotonic else {}))

    def notify(self, event, detail="", lines=None):
        """폰에 보낼 연출을 정한다. 알 수 없는 이벤트는 ValueError."""
        if event not in EVENTS:
            raise ValueError("알 수 없는 이벤트: %s (가능: %s)" % (event, ", ".join(sorted(EVENTS))))
        if not self.cfg.get("enabled", True):
            return {"ok": False, "disabled": True}
        spec = EVENTS[event]
        if event == "pr_merged" and lines is not None and int(lines) < self.cfg.get("big_pr_lines", 300):
            spec = SMALL_PR
        ok, wait = self.limiter.try_acquire()
        if not ok:
            return {"ok": False, "rate_limited": True, "retry_after": int(wait) + 1}
        say = self.rng.choice(spec["lines"])
        detail = clean_detail(detail)
        if detail:
            say = "%s %s." % (say, detail.rstrip("."))
        return {"ok": True, "event": event, "sound": spec["sound"], "emoji": spec["emoji"], "say": say}
