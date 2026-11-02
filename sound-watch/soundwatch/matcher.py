"""템플릿 매칭: 스트리밍 부분수열 DTW (열린 시작, 프레임당 O(M)).

프레임 비용(0 = 같음, 1 = 다름):
- 둘 다 비활성: 0.02 (침묵끼리는 거의 공짜)
- 한쪽만 활성: 1.0 (약한 프레임이면 0.35)
- 둘 다 활성: 0.4*(1-코사인) + 0.6*음정차 (둘 다 음정 있을 때. 반음 2 이상 차이면 최대)
경로는 대각/수평/2행 점프 이동을 허용(0.5~2배 시간 신축). 점수 = 경로 비용 합 / 템플릿 활성 프레임 수.
추가 조건: 나쁜 셀(비용>0.6) 비율 <= 30%, 경로 길이가 템플릿의 0.5~2.0배.
"""
from __future__ import annotations

from operator import mul
from typing import List, Optional, Sequence

from .events import Detection
from .features import Frame, HOP, SR
from .templates import Sample, Template

INF = float("inf")
EPS_SILENT = 0.02
BAD_COST = 0.6
MAX_BAD_FRAC = 0.30
SPAN_MIN, SPAN_MAX = 0.5, 2.0
HOLD_FRAMES = 4  # 최저점 이후 이만큼(0.2 s) 더 개선 없으면 확정


def _cos_cost(tv: Sequence[float], sv: Sequence[float]) -> float:
    d = 1.0 - sum(map(mul, tv, sv))
    return 0.0 if d < 0.0 else (1.0 if d > 1.0 else d)


def cost(ta: int, tstrong: int, tpitch, tvec, sa: bool, sstrong: bool, spitch, svec) -> float:
    if ta:
        if not sa:
            return 1.0 if tstrong else 0.35
        cd = _cos_cost(tvec, svec)
        if tpitch is not None and spitch is not None:
            pd = abs(tpitch - spitch) / 2.0
            return 0.4 * cd + 0.6 * (pd if pd < 1.0 else 1.0)
        if tpitch is not None or spitch is not None:
            cd += 0.3
            return cd if cd < 1.0 else 1.0
        return cd
    if sa:
        return 1.0 if sstrong else 0.35
    return EPS_SILENT


def dtw_distance(a: Sample, b: Sample) -> float:
    """두 샘플 사이 양끝 고정 DTW 거리 (a 의 활성 프레임 수로 정규화). 불가능하면 1.0."""
    ma, mb = len(a), len(b)
    na = max(1, a.n_active())
    # cs[j][i] = 비용 (a 의 i 행 vs b 의 j 열)
    cs = [[cost(a.act[i], a.strong[i], a.pitch[i], a.vec[i],
                bool(b.act[j]), bool(b.strong[j]), b.pitch[j], b.vec[j]) for i in range(ma)]
          for j in range(mb)]
    prev = [INF] * ma
    for j in range(mb):
        c = cs[j]
        cur = [INF] * ma
        for i in range(ma):
            if j == 0:
                if i == 0:
                    cur[0] = c[0]
                elif i == 1:
                    cur[1] = c[0] + c[1]
                continue
            best = INF
            if i > 0:
                best = prev[i - 1]
            if prev[i] < best:
                best = prev[i]
            if i > 1 and prev[i - 2] != INF and prev[i - 2] + c[i - 1] < best:
                best = prev[i - 2] + c[i - 1]
            cur[i] = c[i] + best
        prev = cur
    d = prev[ma - 1]
    return 1.0 if d == INF else min(1.0, d / na)


class SampleMatcher:
    """한 샘플에 대한 증분 DTW.

    이동: 대각(행+1,열+1), 수평(같은 행 유지, 소리가 늘어남), 2행 점프(한 열에 두 행, 소리가 2배까지 빨라짐).
    수직 무제한 이동은 허용하지 않는다(짧은 소리 하나에 긴 템플릿 전체가 붙는 것을 막기 위해).
    """

    def __init__(self, sample: Sample):
        self.s = sample
        self.M = len(sample)
        self.nA = max(1, sample.n_active())
        self.reset()

    def reset(self):
        M = self.M
        self.D = [INF] * M
        self.S = [0] * M
        self.B = [0] * M

    def step(self, j: int, f: Frame):
        s = self.s
        M = self.M
        pD, pS, pB = self.D, self.S, self.B
        sa = f.active
        sstrong = sa and f.peak_excess >= 18.0
        spitch, svec = f.pitch, f.vec
        act, strong, pitch, vec = s.act, s.strong, s.pitch, s.vec
        cs = [cost(act[i], strong[i], pitch[i], vec[i], sa, sstrong, spitch, svec) for i in range(M)]
        D = [INF] * M
        S = [0] * M
        B = [0] * M
        bad0 = 1 if cs[0] > BAD_COST else 0
        D[0] = cs[0]
        S[0] = j
        B[0] = bad0
        for i in range(1, M):
            c = cs[i]
            best = pD[i - 1]
            bs = pS[i - 1]
            bb = pB[i - 1]
            add = c
            addb = 1 if c > BAD_COST else 0
            if pD[i] < best:
                best, bs, bb = pD[i], pS[i], pB[i]
            if i >= 2:
                alt = pD[i - 2] + cs[i - 1]
                if alt < best:
                    best, bs = alt, pS[i - 2]
                    bb = pB[i - 2] + (1 if cs[i - 1] > BAD_COST else 0)
            else:  # i == 1: 같은 열에서 새로 시작 (행 0,1 동시)
                alt = cs[0]
                if alt < best:
                    best, bs, bb = alt, j, bad0
            D[i] = add + best
            S[i] = bs
            B[i] = bb + addb
        self.D, self.S, self.B = D, S, B
        score = D[M - 1] / self.nA
        return score, B[M - 1] / self.nA, j - S[M - 1] + 1, S[M - 1]


class TemplateMatcher:
    """템플릿 하나(샘플 여러 개)의 스트리밍 감지기. 쿨다운/최저점 확정 포함."""

    def __init__(self, tmpl: Template, hold: int = HOLD_FRAMES):
        self.t = tmpl
        self.ms = [SampleMatcher(s) for s in tmpl.samples if len(s) >= 3]
        self.hold = hold
        self.j = -1
        self.last_fire_t = -1e18
        self.pending = None  # (score, j, start_j, t_end, t_start)
        self.last_scores = {}

    def reset(self):
        for m in self.ms:
            m.reset()
        self.pending = None

    def update(self, f: Frame) -> Optional[Detection]:
        self.j += 1
        j = self.j
        best = None
        for m in self.ms:
            score, bad, span, sj = m.step(j, f)
            ok = (score <= self.t.threshold and bad <= MAX_BAD_FRAC
                  and SPAN_MIN * m.M <= span <= SPAN_MAX * m.M)
            if ok and (best is None or score < best[0]):
                best = (score, sj)
        in_cooldown = f.t < self.last_fire_t + self.t.cooldown
        if best is not None and not in_cooldown:
            if self.pending is None or best[0] < self.pending[0]:
                self.pending = (best[0], j, best[1], f.t)
            elif j - self.pending[1] >= self.hold:
                return self._fire(f)
        elif self.pending is not None:
            if in_cooldown:
                self.pending = None
            else:
                return self._fire(f)
        if self.pending is not None and j - self.pending[1] >= self.hold:
            return self._fire(f)
        return None

    def flush(self) -> Optional[Detection]:
        """스트림 끝: 확정 대기 중인 후보가 있으면 내보낸다."""
        if self.pending is None:
            return None
        return self._fire(None)

    def _fire(self, f: Optional[Frame]) -> Detection:
        score, j_end, sj, t_end = self.pending
        self.pending = None
        self.last_fire_t = f.t if f is not None else t_end
        for m in self.ms:
            m.reset()
        t_start = max(0.0, t_end - (j_end - sj + 1) * HOP / float(SR))
        return Detection("template", self.t.name, t_start, t_end, score, self.t.message(),
                         {"cooldown": self.t.cooldown})
