"""소음 로거: 100 ms A-가중 레벨 프레임 -> 기준 초과 구간 -> 병합된 이벤트 + 분 단위 집계.

레벨은 내부적으로 A-가중 dBFS 로 다루고, 임계값만 표시 dB 에서 변환해 받는다.
시간은 스트림 시작 기준 초(엔진이 epoch 로 바꿔 저장).
"""
from __future__ import annotations

from typing import Callable, List, Optional

from . import dsp
from .events import NoiseEvent

FRAME_S = 0.1


class NoiseLogger:
    def __init__(self, threshold_dbfs: float, hysteresis_db: float = 2.0, min_duration_s: float = 0.3,
                 merge_gap_s: float = 2.0, on_event: Optional[Callable[[NoiseEvent], None]] = None,
                 on_minute: Optional[Callable[[int, float, int, float], None]] = None, abs_start: float = 0.0):
        self.thr_on = threshold_dbfs
        self.thr_off = threshold_dbfs - hysteresis_db
        self.min_duration = min_duration_s
        self.merge_gap = merge_gap_s
        self.on_event = on_event
        self.on_minute = on_minute
        self.abs_start = abs_start       # 스트림 0초의 epoch (분 집계 경계용)
        self.reset()

    def reset(self):
        self._in_seg = False
        self._seg_t0 = 0.0
        self._seg_n = 0
        self._cand: List[tuple] = []      # (t, level) 후보 이벤트 프레임들
        self._ev_start: Optional[float] = None
        self._ev_end = 0.0
        self._segs = 0
        self._min = None   # [minute, sum_power, n, max]
        self.last_level: Optional[float] = None

    # ------------------------------------------------------------------ 분 집계
    def _minute_add(self, t: float, level: float):
        m = int((self.abs_start + t) // 60)
        cur = self._min
        if cur is not None and cur[0] != m:
            self._flush_minute()
            cur = None
        if cur is None:
            cur = self._min = [m, 0.0, 0, dsp.DB_FLOOR]
        cur[1] += 10 ** (level / 10.0)
        cur[2] += 1
        if level > cur[3]:
            cur[3] = level

    def _flush_minute(self):
        if self._min is not None and self.on_minute:
            m, sp, n, mx = self._min
            self.on_minute(m, sp, n, mx)
        self._min = None

    # ------------------------------------------------------------------ 프레임 입력
    def push(self, t: float, level: float):
        """t: 프레임 시작(스트림 상대 초), level: A-가중 dBFS."""
        self.last_level = level
        self._minute_add(t, level)
        above = level >= (self.thr_off if self._in_seg else self.thr_on)
        if above:
            if not self._in_seg:
                self._in_seg = True
                self._seg_t0 = t
                self._seg_n = 0
                if self._ev_start is None:
                    self._cand = []
            self._seg_n += 1
        else:
            if self._in_seg:
                self._close_segment(t)
        if self._ev_start is not None or self._in_seg:
            self._cand.append((t, level))
        if self._ev_start is not None and not self._in_seg and t - self._ev_end > self.merge_gap:
            self._finalize()

    def _close_segment(self, t_end: float):
        self._in_seg = False
        dur = self._seg_n * FRAME_S
        if dur >= self.min_duration - 1e-9:
            if self._ev_start is None:
                self._ev_start = self._seg_t0
            self._ev_end = self._seg_t0 + dur
            self._segs += 1
        elif self._ev_start is None:
            self._cand = []

    def _finalize(self):
        start, end = self._ev_start, self._ev_end
        frames = [lv for (t, lv) in self._cand if start - 1e-9 <= t < end - 1e-9]
        self._ev_start = None
        segs = self._segs
        self._segs = 0
        self._cand = []
        if not frames:
            return
        peak = max(frames)
        leq = dsp.db_sum_energy(frames)
        dur = end - start
        # 거친 자동 분류: 짧고(<3초) 최대가 평균보다 10 dB 이상 높으면 충격성
        kind = "impact" if (dur < 3.0 and peak - leq >= 6.0) else "sustained"
        ev = NoiseEvent(start, end, dur, peak, leq, kind, segs)
        if self.on_event:
            self.on_event(ev)

    def flush(self):
        """스트림 끝/중단: 진행 중 구간 마감, 미완 이벤트 확정, 분 집계 기록."""
        if self._in_seg:
            self._close_segment(self._seg_t0 + self._seg_n * FRAME_S)
        if self._ev_start is not None:
            self._finalize()
        self._cand = []
        self._flush_minute()
