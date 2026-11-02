"""노크(충격음 반복) 감지기: 10 ms 포락선에서 급상승 + 빠른 감쇠(충격성) 를 '타격'으로 보고,
타격이 일정한 리듬으로 min_hits 번 이상 이어지면 노크로 판정한다.

- 지속음/삐 소리는 감쇠 조건(120 ms 안에 10 dB 하락)에서 걸러지고, 짧은 순음은 피크 집중도로 걸러진다.
- 한계: 접시 부딪히는 소리, 키보드, 문 닫는 소리 등 짧은 충격음이 반복되면 오탐이 가능하고,
  아랫집 쿵쿵(감쇠가 더 느린 저주파 충격)은 대체로 걸러지지만 보장하지 않는다.
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from operator import mul
from typing import List, Optional, Sequence

from . import dsp
from .events import Detection

SR = 16000
ENV_HOP = 160  # 10 ms


class KnockDetector:
    def __init__(self, sr: int = SR, min_hits: int = 3, onset_db: float = 14.0, decay_db: float = 10.0,
                 min_ioi: float = 0.10, max_ioi: float = 0.90, max_ratio: float = 3.0,
                 min_level_dbfs: float = -60.0, cooldown: float = 5.0):
        self.sr = sr
        self.min_hits = min_hits
        self.onset_db = onset_db
        self.decay_db = decay_db
        self.min_ioi = min_ioi
        self.max_ioi = max_ioi
        self.max_ratio = max_ratio
        self.min_level_dbfs = min_level_dbfs
        self.cooldown = cooldown
        self.reset()

    def reset(self):
        self._left: List[float] = []
        self._recent: List[float] = []   # 최근 원시 샘플 (톤성 검사용, 약 0.5 s)
        self.k = 0                      # 처리한 포락선 프레임 수
        self._env: List[float] = []     # 최근 포락선(최대 200)
        self._env0 = 0                  # _env[0] 의 프레임 번호
        self._win = deque()
        self._sorted: List[float] = []
        self._cand = None
        self._hits: List = []           # (k, level_db)
        self._last_hit_k = -10 ** 9
        self._last_event_t = -1e18

    # ----------------------------------------------------------------- 내부
    def _bg(self) -> float:
        s = self._sorted
        return s[int(0.25 * (len(s) - 1))] if s else dsp.DB_FLOOR

    def _push_env(self, e: float):
        self._win.append(e)
        bisect.insort(self._sorted, e)
        if len(self._win) > 100:
            old = self._win.popleft()
            del self._sorted[bisect.bisect_left(self._sorted, old)]
        self._env.append(e)
        if len(self._env) > 200:
            del self._env[:50]
            self._env0 += 50

    def _env_at(self, k: int) -> float:
        return self._env[k - self._env0]

    def feed(self, samples: Sequence[float]) -> List[Detection]:
        out: List[Detection] = []
        self._left.extend(samples)
        self._recent.extend(samples)
        buf = self._left
        n = len(buf) // ENV_HOP
        # _recent 는 이미 이번 청크 끝까지 포함하므로, 프레임 처리 시 '현재 청크 끝' 기준으로 역산한다.
        consumed = n * ENV_HOP
        tail_extra = len(buf) - consumed  # 아직 처리 안 된 꼬리 샘플 수
        for i in range(n):
            blk = buf[i * ENV_HOP:(i + 1) * ENV_HOP]
            e = dsp.power_to_db(sum(map(mul, blk, blk)) / ENV_HOP)
            k = self.k
            prev = self._env[-1] if self._env else dsp.DB_FLOOR
            prev2 = self._env[-2] if len(self._env) > 1 else prev
            bg = self._bg()
            self._push_env(e)
            self.k += 1
            self._step(k, e, prev, prev2, bg, tail_extra + (n - 1 - i) * ENV_HOP, out)
        if n:
            del buf[:consumed]
        if len(self._recent) > 8000:
            del self._recent[:len(self._recent) - 8000]
        return out

    def _step(self, k: int, e: float, prev: float, prev2: float, bg: float, samples_after: int, out):
        c = self._cand
        if c is None:
            if (e - bg >= self.onset_db and e - min(prev, prev2) >= 8.0 and e >= self.min_level_dbfs
                    and k - self._last_hit_k > 6):
                self._cand = {"k0": k, "peak": e, "kp": k}
        else:
            if k - c["k0"] <= 6 and e > c["peak"]:
                c["peak"], c["kp"] = e, k
            if k - c["kp"] >= 12:
                kp = c["kp"]
                seg = [self._env_at(x) for x in range(kp + 1, kp + 13)]
                impulsive = min(seg) <= c["peak"] - self.decay_db
                self._cand = None
                if impulsive:
                    # 피크 프레임 이후 샘플 수 = (k - kp) * HOP + samples_after
                    back_frames = k - kp
                    if not self._tonal_at(back_frames, samples_after):
                        self._hits.append((kp, c["peak"]))
                        self._last_hit_k = kp
        # 그룹 마감
        if self._hits and k - self._hits[-1][0] > int(1.2 * self.sr / ENV_HOP):
            ev = self._finish_group(k)
            if ev is not None:
                out.append(ev)
            self._hits = []

    def _tonal_at(self, back_frames: int, samples_after: int) -> bool:
        # 피크 프레임 시작 = 현재 끝에서 (back_frames+1)*HOP + samples_after 샘플 전
        total_back = (back_frames + 1) * ENV_HOP + samples_after
        end = len(self._recent) - total_back + 2 * ENV_HOP
        start = max(0, end - 512)
        seg = self._recent[start:start + 512]
        n = 512 if len(seg) >= 512 else (256 if len(seg) >= 256 else 0)
        if not n:
            return False
        seg = seg[:n]
        pw = dsp.rfft_power(list(map(mul, seg, dsp.hann(n))))
        lo = int(300 * n / self.sr)
        hi = int(7000 * n / self.sr)
        band = pw[lo:hi + 1]
        tot = sum(band)
        if tot <= 1e-18:
            return False
        kp = max(range(len(band)), key=band.__getitem__)
        return sum(band[max(0, kp - 2):kp + 3]) / tot >= 0.5

    def _finish_group(self, k_now: int) -> Optional[Detection]:
        hits = self._hits
        # max_ioi 보다 벌어진 곳에서 나누고, 조건을 만족하는 가장 긴 묶음을 고른다.
        groups, cur = [], [hits[0]]
        for h in hits[1:]:
            if (h[0] - cur[-1][0]) * ENV_HOP / self.sr > self.max_ioi:
                groups.append(cur)
                cur = [h]
            else:
                cur.append(h)
        groups.append(cur)
        best = None
        for g in groups:
            if len(g) < self.min_hits:
                continue
            iois = [(g[i + 1][0] - g[i][0]) * ENV_HOP / self.sr for i in range(len(g) - 1)]
            if min(iois) < self.min_ioi:
                continue
            if len(iois) >= 2 and max(iois) / min(iois) > self.max_ratio:
                continue
            if best is None or len(g) > len(best[0]):
                best = (g, iois)
        if best is None:
            return None
        g, iois = best
        t0 = g[0][0] * ENV_HOP / self.sr
        t1 = g[-1][0] * ENV_HOP / self.sr
        if t0 < self._last_event_t + self.cooldown:
            return None
        self._last_event_t = t1
        return Detection("knock", "노크", t0, t1, 0.0, "노크 소리가 났어요 (%d회)" % len(g),
                         {"hits": len(g), "interval": round(sum(iois) / len(iois), 3),
                          "peak_dbfs": round(max(h[1] for h in g), 1)})

    def flush(self) -> List[Detection]:
        out = []
        if self._hits:
            ev = self._finish_group(self.k)
            if ev is not None:
                out.append(ev)
            self._hits = []
        return out
