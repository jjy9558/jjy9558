"""스펙트럼 특징 추출: 50 ms 슬라이스(1024 샘플 창, 800 샘플 hop @16 kHz).

슬라이스마다
- 멜 간격 삼각 필터뱅크 24밴드 평균 파워(dB), 지배 주파수(포물선 보간), 피크 집중도, 스펙트럼 평탄도
- 배경(밴드별 8초 구간 20% 분위수)보다 얼마나 큰지(`excess`) 로 '활성' 여부와 단위벡터 지문을 만든다.
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from dataclasses import dataclass
from operator import mul
from typing import List, Optional, Sequence

from . import dsp

SR = 16000
WIN = 1024
HOP = 800
FMIN = 150.0
FMAX = 7600.0
NBANDS = 24
MARGIN_DB = 6.0       # 배경 위 이만큼까지는 잡음 변동으로 본다
ACTIVE_DB = 6.0       # 마진 위로 이만큼 이상 튀어야 '활성' 프레임
STRONG_DB = 18.0      # 이 이상이면 '강한' 프레임 (템플릿 가장자리 가중치용)
EXCESS_CAP_DB = 40.0
TONAL_FRAC = 0.30     # 피크±2 bin 이 대역 파워의 30% 이상이면 음정 있는 프레임
FLOOR_WINDOW = 160    # 8 s
FLOOR_PCT = 0.20
FLOOR_MIN_FRAMES = 20  # 1 s 워밍업


def _mel(f: float) -> float:
    return 1127.0 * math.log(1.0 + f / 700.0)


def _imel(m: float) -> float:
    return 700.0 * (math.exp(m / 1127.0) - 1.0)


def hz_to_semitone(f: float) -> float:
    return 12.0 * math.log2(f / 440.0)


def semitone_to_hz(p: float) -> float:
    return 440.0 * 2 ** (p / 12.0)


_FB_CACHE = {}  # type: dict


def filterbank(n: int = WIN, sr: int = SR):
    """밴드별 [(bin, weight)] 희소 삼각 필터와 weight 합."""
    key = (n, sr)
    fb = _FB_CACHE.get(key)
    if fb is None:
        m0, m1 = _mel(FMIN), _mel(FMAX)
        edges = [_imel(m0 + (m1 - m0) * i / (NBANDS + 1)) for i in range(NBANDS + 2)]
        df = sr / float(n)
        bands = []
        for b in range(NBANDS):
            lo, ce, hi = edges[b], edges[b + 1], edges[b + 2]
            items = []
            for k in range(int(lo / df), int(hi / df) + 2):
                f = k * df
                if f <= lo or f >= hi:
                    continue
                w = (f - lo) / (ce - lo) if f <= ce else (hi - f) / (hi - ce)
                if w > 0:
                    items.append((k, w))
            if not items:  # 아주 좁은 밴드: 가장 가까운 bin
                items = [(int(round(ce / df)), 1.0)]
            bands.append((items, sum(w for _, w in items)))
        fb = bands
        _FB_CACHE[key] = fb
    return fb


@dataclass
class Slice:
    idx: int
    t: float                 # 스트림 시작 기준 창 중심 시각(초)
    rms_db: float            # 비가중 dBFS
    band_db: List[float]
    freq: float              # 지배 주파수 Hz (0 = 없음)
    peak_frac: float
    flatness: float
    centroid: float


class SliceExtractor:
    """스트리밍 슬라이스 추출기."""

    def __init__(self, sr: int = SR):
        if sr != SR:
            raise ValueError("슬라이스 추출기는 16 kHz 전용입니다 (입력을 먼저 리샘플하세요)")
        self.sr = sr
        self._buf: List[float] = []
        self.idx = 0
        self._fb = filterbank()
        self._win = dsp.hann(WIN)
        self._klo = int(math.ceil(FMIN * WIN / sr))
        self._khi = int(FMAX * WIN / sr)

    def reset(self):
        self._buf = []
        self.idx = 0

    def feed(self, samples: Sequence[float]) -> List[Slice]:
        self._buf.extend(samples)
        out = []
        buf = self._buf
        pos = 0
        while len(buf) - pos >= WIN:
            frame = buf[pos:pos + WIN]
            out.append(self._analyze(frame))
            pos += HOP
        if pos:
            del buf[:pos]
        return out

    def _analyze(self, frame: List[float]) -> "Slice":
        sr = self.sr
        rms_db = dsp.power_to_db(sum(map(mul, frame, frame)) / WIN)
        win = self._win
        power = dsp.rfft_power(list(map(mul, frame, win)))
        band_db = []
        for items, wsum in self._fb:
            s = 0.0
            for k, w in items:
                s += power[k] * w
            band_db.append(dsp.power_to_db(s / wsum / (WIN * WIN)))
        klo, khi = self._klo, self._khi
        seg = power[klo:khi + 1]
        total = sum(seg)
        if total <= 1e-18:
            return Slice(self.idx_next(), 0.0, rms_db, band_db, 0.0, 0.0, 1.0, 0.0)
        kp = klo + max(range(len(seg)), key=seg.__getitem__)
        a = math.log(power[kp - 1] + 1e-30) if kp > 0 else math.log(power[kp] + 1e-30)
        b = math.log(power[kp] + 1e-30)
        c = math.log(power[kp + 1] + 1e-30) if kp + 1 < len(power) else b
        den = a - 2.0 * b + c
        delta = 0.5 * (a - c) / den if den < -1e-12 else 0.0
        delta = max(-1.0, min(1.0, delta))
        freq = (kp + delta) * sr / WIN
        pk = sum(power[max(klo, kp - 2):min(khi, kp + 2) + 1]) / total
        logs = sum(math.log(p + 1e-30) for p in seg) / len(seg)
        flat = math.exp(logs) / (total / len(seg))
        cen = sum((klo + i) * p for i, p in enumerate(seg)) / total * sr / WIN
        return Slice(self.idx_next(), 0.0, rms_db, band_db, freq, pk, min(1.0, flat), cen)

    def idx_next(self) -> int:
        i = self.idx
        self.idx += 1
        return i


class FloorTracker:
    """밴드별 최근 8초 구간의 20% 분위수를 배경 잡음 추정치로 쓴다 (bisect 로 증분 갱신)."""

    def __init__(self, nb: int = NBANDS, window: int = FLOOR_WINDOW, pct: float = FLOOR_PCT):
        self.nb = nb
        self.window = window
        self.pct = pct
        self._hist = [deque() for _ in range(nb)]
        self._sorted = [[] for _ in range(nb)]

    def update(self, band_db: Sequence[float]):
        for b in range(self.nb):
            v = band_db[b]
            h = self._hist[b]
            s = self._sorted[b]
            h.append(v)
            bisect.insort(s, v)
            if len(h) > self.window:
                old = h.popleft()
                del s[bisect.bisect_left(s, old)]

    def count(self) -> int:
        return len(self._hist[0])

    def floor(self) -> List[float]:
        out = []
        for s in self._sorted:
            out.append(s[min(len(s) - 1, int(self.pct * len(s)))])
        return out


@dataclass
class Frame:
    """인식용 프레임: 슬라이스 + 배경 대비 정보."""
    t: float
    rms_db: float
    freq: float
    pitch: Optional[float]     # 반음 (A4=0), 음정 있을 때만
    tonal: bool
    active: bool
    peak_excess: float
    vec: Optional[List[float]]  # 활성일 때 단위벡터(밴드 excess)
    flatness: float
    peak_frac: float


def make_frame(sl: Slice, floor: Sequence[float]) -> Frame:
    ex = [min(EXCESS_CAP_DB, max(0.0, d - f - MARGIN_DB)) for d, f in zip(sl.band_db, floor)]
    peak = max(ex)
    active = peak >= ACTIVE_DB
    vec = None
    if active:
        norm = math.sqrt(sum(map(mul, ex, ex)))
        vec = [v / norm for v in ex]
    tonal = active and sl.peak_frac >= TONAL_FRAC and sl.freq > 0
    pitch = hz_to_semitone(sl.freq) if tonal else None
    return Frame(sl.t, sl.rms_db, sl.freq, pitch, tonal, active, peak, vec, sl.flatness, sl.peak_frac)


def slice_time(idx: int) -> float:
    return (idx * HOP + WIN / 2.0) / SR


class FeatureStream:
    """스트리밍: 샘플 -> Frame. 처음 1초는 배경 추정을 위해 모았다가 한꺼번에 내보낸다."""

    def __init__(self, sr: int = SR):
        self.ext = SliceExtractor(sr)
        self.tracker = FloorTracker()
        self._warm: List[Slice] = []
        self._warmed = False

    def reset(self):
        self.ext.reset()
        self.tracker = FloorTracker()
        self._warm = []
        self._warmed = False

    def feed(self, samples: Sequence[float]) -> List[Frame]:
        out: List[Frame] = []
        for sl in self.ext.feed(samples):
            sl.t = slice_time(sl.idx)
            if not self._warmed:
                self._warm.append(sl)
                self.tracker.update(sl.band_db)
                if len(self._warm) >= FLOOR_MIN_FRAMES:
                    fl = self.tracker.floor()
                    out.extend(make_frame(w, fl) for w in self._warm)
                    self._warm = []
                    self._warmed = True
                continue
            self.tracker.update(sl.band_db)
            out.append(make_frame(sl, self.tracker.floor()))
        return out

    def flush(self) -> List[Frame]:
        """스트림 끝: 워밍업이 덜 찬 슬라이스를 지금까지의 배경 추정으로 내보냄."""
        out = []
        if self._warm:
            fl = self.tracker.floor()
            out = [make_frame(w, fl) for w in self._warm]
            self._warm = []
            self._warmed = True
        return out


def extract_clip(samples: Sequence[float], pct: float = 0.10) -> List[Frame]:
    """파일 단위(등록용): 클립 전체의 밴드별 pct 분위수를 배경으로 쓴다."""
    ext = SliceExtractor()
    slices = ext.feed(samples)
    if not slices:
        return []
    for sl in slices:
        sl.t = slice_time(sl.idx)
    floor = []
    for b in range(NBANDS):
        col = sorted(s.band_db[b] for s in slices)
        floor.append(col[min(len(col) - 1, int(pct * len(col)))])
    return [make_frame(s, floor) for s in slices]
