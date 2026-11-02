"""뽑기 횟수 분포를 정확히(DP) 계산하고 판정 결과를 만든다.

상태 = (천장 카운트, 확정 여부). 뽑기 한 번마다 상태 확률을 전이시키므로
몬테카를로 샘플링 없이 P(T = t) 를 그대로 구한다. 하드 천장이 있어서
최대 2 * hard_pity 번 안에는 반드시 픽업을 얻는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .banner import Banner, hit_probability
from .currency import Package, fmt_krw
from .validation import parse_number

_QUANTILE_TOL = 1e-12
_MASS_TOL = 1e-15


@dataclass(frozen=True)
class PityDistribution:
    """pmf[t] = 현재 상태에서 정확히 t번째 뽑기에 픽업을 얻을 확률."""

    pmf: tuple[float, ...]
    pity_count: int
    guaranteed: bool
    worst_case_pulls: int

    @property
    def expected_pulls(self) -> float:
        return math.fsum(t * p for t, p in enumerate(self.pmf))

    def cdf(self, pulls: int) -> float:
        """pulls 번 안에 픽업을 얻을 확률 P(T <= pulls)."""
        if pulls < 0:
            return 0.0
        return min(1.0, math.fsum(self.pmf[: pulls + 1]))

    def quantile(self, probability: float) -> int:
        """P(T <= n) >= probability 를 만족하는 가장 작은 n."""
        if not 0.0 < probability <= 1.0:
            raise ValueError("probability 는 (0, 1] 범위여야 합니다")
        total = 0.0
        for t, p in enumerate(self.pmf):
            total += p
            if total >= probability - _QUANTILE_TOL:
                return t
        return self.worst_case_pulls


def pity_distribution(banner: Banner, pity_count: object = 0,
                      guaranteed: bool = False) -> PityDistribution:
    """현재 천장 카운트/확정 상태에서 픽업까지 뽑기 횟수의 정확한 분포."""
    hard = banner.hard_pity
    k0 = int(parse_number(pity_count, "현재 천장 카운트", integer=True,
                          minimum=0, maximum=hard - 1))
    sure_now = bool(guaranteed) or banner.featured_share >= 1.0
    share = banner.featured_share

    hit = [hit_probability(banner, pull) for pull in range(1, hard + 1)]

    fresh = [0.0] * hard  # 확정 아님, 인덱스 = 천장 카운트
    sure = [0.0] * hard   # 확정 상태, 다음 5성 적중이 픽업
    (sure if sure_now else fresh)[k0] = 1.0

    pmf = [0.0]
    for _ in range(2 * hard + 1):
        next_fresh = [0.0] * hard
        next_sure = [0.0] * hard
        won = 0.0
        for k in range(hard):
            h = hit[k]
            for is_sure, mass in ((False, fresh[k]), (True, sure[k])):
                if mass == 0.0:
                    continue
                if h < 1.0:  # 빗나감 -> 천장 카운트 +1
                    target = next_sure if is_sure else next_fresh
                    target[k + 1] += mass * (1.0 - h)
                if h > 0.0:  # 적중
                    hit_mass = mass * h
                    if is_sure or share >= 1.0:
                        won += hit_mass
                    else:
                        won += hit_mass * share
                        next_sure[0] += hit_mass * (1.0 - share)  # 비픽업 -> 다음 적중 확정
        pmf.append(won)
        fresh, sure = next_fresh, next_sure
        if math.fsum(fresh) + math.fsum(sure) <= _MASS_TOL:
            break

    return PityDistribution(
        pmf=tuple(pmf),
        pity_count=k0,
        guaranteed=sure_now,
        worst_case_pulls=_worst_case_pulls(banner, k0, sure_now),
    )


def _worst_case_pulls(banner: Banner, k0: int, sure_now: bool) -> int:
    """양의 확률을 갖는 경로 중 픽업까지 최대 뽑기 횟수 (하드 천장 기준)."""
    hard = banner.hard_pity
    share = banner.featured_share
    hit = [hit_probability(banner, pull) for pull in range(1, hard + 1)]

    sure = [0] * hard
    for k in range(hard - 1, -1, -1):
        best = 1 if hit[k] > 0.0 else 0
        if hit[k] < 1.0:
            best = max(best, 1 + sure[k + 1])
        sure[k] = best

    fresh = [0] * hard
    for k in range(hard - 1, -1, -1):
        h = hit[k]
        best = 0
        if h > 0.0:
            best = 1 if share >= 1.0 else 1 + sure[0]  # 비픽업 후 확정 경로가 더 길다
        if h < 1.0:
            best = max(best, 1 + fresh[k + 1])
        fresh[k] = best

    return sure[k0] if sure_now else fresh[k0]


def verdict_line(expected_krw: float, p_within_budget: float) -> str:
    if p_within_budget >= 0.95:
        advice = "사실상 확정, 바로 뽑아도 됨"
    elif p_within_budget >= 0.5:
        advice = "절반 이상, 뽑기 무난"
    else:
        advice = "존버 추천"
    return (f"천장까지 약 {fmt_krw(expected_krw)}, "
            f"지금 있는 재화로 확률 {p_within_budget * 100:.0f}% — {advice}")


@dataclass(frozen=True)
class PityReport:
    banner: Banner
    package: Package
    currency_units: float
    distribution: PityDistribution
    budget_pulls: int
    p_within_budget: float
    expected_pulls: float
    median_pulls: int
    p90_pulls: int
    worst_pulls: int
    expected_krw: float
    p90_krw: int
    worst_krw: int
    verdict: str


def build_pity_report(banner: Banner, package: Package, currency_units: object,
                      pity_count: object = 0, guaranteed: bool = False) -> PityReport:
    units = float(parse_number(currency_units, "보유 재화", minimum=0))
    dist = pity_distribution(banner, pity_count, guaranteed)
    cost = banner.cost_per_pull

    budget = int(units // cost)
    p_budget = dist.cdf(budget)
    expected = dist.expected_pulls
    p90 = dist.quantile(0.9)
    worst = dist.worst_case_pulls
    expected_krw = package.krw_for_units(expected * cost)

    return PityReport(
        banner=banner,
        package=package,
        currency_units=units,
        distribution=dist,
        budget_pulls=budget,
        p_within_budget=p_budget,
        expected_pulls=expected,
        median_pulls=dist.quantile(0.5),
        p90_pulls=p90,
        worst_pulls=worst,
        expected_krw=expected_krw,
        p90_krw=package.purchase_cost(p90 * cost),
        worst_krw=package.purchase_cost(worst * cost),
        verdict=verdict_line(expected_krw, p_budget),
    )
