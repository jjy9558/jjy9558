"""혼잣말 트리거 매칭: 띄어쓰기·문장부호·돼/되 표기 차이를 흡수한다."""
import re
import threading
import time

_STRIP = re.compile(r"[\s\.,!?~…·'\"`\-_/]+")
# 흔한 구어 표기 정규화: 돼→되, 됌→됨, 에러/오류/에라 통일
_REPLACE = [("돼", "되"), ("됌", "됨"), ("에라", "에러"), ("오류", "에러"), ("에러가", "에러")]


def normalize(text):
    t = _STRIP.sub("", (text or "").lower())
    for a, b in _REPLACE:
        t = t.replace(a, b)
    return t


class TriggerMatcher:
    """설정된 문구 중 하나라도 발화 안에 (정규화 후) 포함되면 매칭."""

    def __init__(self, phrases):
        self.phrases = [(p, normalize(p)) for p in phrases if normalize(p)]

    def match(self, utterance):
        n = normalize(utterance)
        for original, np in self.phrases:
            if np in n:
                return original
        return None


class Cooldown:
    def __init__(self, seconds, clock=time.monotonic):
        self.seconds = seconds
        self.clock = clock
        self._last = {}
        self._lock = threading.Lock()

    def remaining(self, key="default"):
        last = self._last.get(key)
        if last is None:
            return 0.0
        return max(0.0, self.seconds - (self.clock() - last))

    def touch(self, key="default"):
        with self._lock:
            self._last[key] = self.clock()

    def try_acquire(self, key="default"):
        """쿨다운이 끝났으면 시간을 기록하고 True."""
        with self._lock:
            if self.remaining(key) > 0:
                return False
            self._last[key] = self.clock()
            return True
