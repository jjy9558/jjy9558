"""클립(±수 초 WAV) 저장: 링 버퍼, 프라이버시 저역통과, 말소리 휴리스틱, 보관 기한 정리.

개인정보/법 안내: 다른 사람의 대화를 동의 없이 녹음하는 것은 법적 문제가 될 수 있다(통신비밀보호법 등).
기본값은 클립을 저장하지 않고, 켜더라도 말소리처럼 보이면 저장하지 않으며 저역(기본 500 Hz 이하)만 남긴다.
이는 완전한 보장이 아니므로 사용 전에 관련 법령과 공동주택 규약을 직접 확인해야 한다.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import time
from array import array
from operator import mul
from typing import Callable, List, Optional, Sequence

from . import audio, dsp

SR = 16000


class Ring:
    """최근 N초 오디오(Int16)를 절대 샘플 번호로 접근하는 링 버퍼."""

    def __init__(self, seconds: float, sr: int = SR):
        self.sr = sr
        self.cap = int(seconds * sr)
        self.buf = array("h")
        self.end = 0          # 지금까지 들어온 총 샘플 수 (다음 샘플의 절대 번호)

    def push(self, samples: Sequence[float]):
        a = audio.floats_to_int16(samples)
        self.buf.extend(a)
        self.end += len(a)
        if len(self.buf) > self.cap:
            del self.buf[:len(self.buf) - self.cap]

    def skip(self, n: int):
        """공백(수신 끊김): 무음으로 채움 (용량 한도 내에서)."""
        n = int(n)
        self.end += n
        self.buf.extend(array("h", [0]) * min(n, self.cap))
        if len(self.buf) > self.cap:
            del self.buf[:len(self.buf) - self.cap]

    def get(self, a: int, b: int) -> Optional[List[float]]:
        """절대 샘플 [a, b) -> float 리스트. 이미 밀려났으면 가능한 부분만."""
        first = self.end - len(self.buf)
        a = max(a, first)
        b = min(b, self.end)
        if b <= a:
            return None
        seg = self.buf[a - first:b - first]
        return [v / 32768.0 for v in seg]


def speech_likelihood(x: Sequence[float], sr: int = SR, max_frames: int = 120) -> float:
    """말소리 비슷한 정도(0~1) 휴리스틱: '유성음 프레임 비율' x '음높이가 변하는가'.

    4 kHz 로 줄여 85-400 Hz 기본주파수의 자기상관 피크가 큰 프레임을 유성음으로 본다.
    일정한 웅웅(냉장고)·경보음은 음높이가 거의 안 변해 점수가 낮고, 쿵쿵 같은 충격음은 유성음이 아니다.
    """
    d = sr // 4000
    y = [sum(x[i:i + d]) / d for i in range(0, len(x) - d + 1, d)]
    frame = 128   # 32 ms @ 4 kHz
    hop = 64
    total = max(1, (len(y) - frame) // hop)
    step = max(1, total // max_frames)
    voiced, active, pitches = 0, 0, []
    gate = None
    levels = []
    for i in range(0, len(y) - frame, hop * step):
        seg = y[i:i + frame]
        levels.append(sum(map(mul, seg, seg)) / frame)
    if not levels:
        return 0.0
    gate = max(levels) * 1e-3   # 최대보다 30 dB 아래는 무시
    k = 0
    for i in range(0, len(y) - frame, hop * step):
        seg = y[i:i + frame]
        e = levels[k]
        k += 1
        if e < gate or e < 1e-8:
            continue
        active += 1
        m = sum(seg) / frame
        seg = [v - m for v in seg]
        r0 = sum(map(mul, seg, seg))
        if r0 <= 0:
            continue
        best, lag_best = 0.0, 0
        for lag in range(10, 48):   # 4000/lag = 83..400 Hz
            r = sum(map(mul, seg[:-lag], seg[lag:])) / r0
            if r > best:
                best, lag_best = r, lag
        if best >= 0.6:
            voiced += 1
            pitches.append(12.0 * math.log2(4000.0 / lag_best))
    if active < 8 or voiced < 5:
        return 0.0
    ratio = voiced / float(active)
    mean = sum(pitches) / len(pitches)
    sd = math.sqrt(sum((p - mean) ** 2 for p in pitches) / len(pitches))
    var_score = min(1.0, sd / 1.0)    # 표준편차 1 반음 이상이면 '변하는' 것으로
    return ratio * var_score


class ClipSaver:
    def __init__(self, directory: str, cfg: dict, sr: int = SR, wall: Callable[[], float] = time.time):
        self.dir = directory
        self.cfg = cfg
        self.sr = sr
        self.wall = wall
        self.pending: List[dict] = []
        self.ring = Ring(cfg["pre_s"] + cfg["max_s"] + cfg["post_s"] + 5.0, sr)

    def push(self, samples):
        self.ring.push(samples)

    def request(self, start_sample: int, end_sample: int, epoch_start: float, callback: Callable[[Optional[str]], None]):
        """이벤트 [start,end) 샘플 주위 클립 요청. 필요한 뒤쪽 샘플이 도착하면 저장."""
        pre = int(self.cfg["pre_s"] * self.sr)
        post = int(self.cfg["post_s"] * self.sr)
        mx = int(self.cfg["max_s"] * self.sr)
        a = max(0, start_sample - pre)
        b = min(end_sample + post, start_sample + mx)
        self.pending.append({"a": a, "b": b, "t": epoch_start, "cb": callback})

    def service(self):
        """링 버퍼가 요청 끝까지 차면 WAV 로 저장하고 콜백(상대 경로)."""
        keep = []
        for p in self.pending:
            if self.ring.end >= p["b"]:
                p["cb"](self._write(p))
            else:
                keep.append(p)
        self.pending = keep

    def flush(self):
        """스트림 종료: 아직 못 채운 요청도 가진 데이터까지 저장."""
        for p in self.pending:
            p["b"] = min(p["b"], self.ring.end)
            p["cb"](self._write(p))
        self.pending = []

    def _write(self, p) -> Optional[str]:
        x = self.ring.get(p["a"], p["b"])
        if not x or len(x) < self.sr // 2:
            return None
        if self.cfg.get("skip_speech", True) and speech_likelihood(x, self.sr) >= 0.25:
            return None
        lp = float(self.cfg.get("lowpass_hz", 0) or 0)
        if lp > 0:
            x = dsp.lowpass4(x, lp, self.sr)
        os.makedirs(self.dir, exist_ok=True)
        when = dt.datetime.fromtimestamp(p["t"])
        name = when.strftime("%Y%m%d_%H%M%S") + ".wav"
        path = os.path.join(self.dir, name)
        i = 1
        while os.path.exists(path):
            path = os.path.join(self.dir, when.strftime("%Y%m%d_%H%M%S") + "_%d.wav" % i)
            i += 1
        audio.write_wav(path, x, self.sr)
        return os.path.basename(path)


def purge_old_clips(directory: str, retention_days: float, now: Optional[float] = None) -> List[str]:
    """보관 기한이 지난 WAV 삭제. 삭제한 파일명 목록 반환."""
    now = now if now is not None else time.time()
    removed = []
    if not os.path.isdir(directory):
        return removed
    cutoff = now - retention_days * 86400.0
    for fn in os.listdir(directory):
        if not fn.lower().endswith(".wav"):
            continue
        p = os.path.join(directory, fn)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
                removed.append(fn)
        except OSError:
            pass
    return removed
