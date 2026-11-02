"""GPU power log (nvidia-smi --query-gpu=timestamp,power.draw --format=csv) -> kWh -> electricity cost.

The default tiers are PLACEHOLDERS for illustration only. They are NOT real KEPCO (한전) rates;
set your own via --config / --tiers.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional, Tuple

PLACEHOLDER_TIERS: List[Tuple[Optional[float], float]] = [(200.0, 100.0), (400.0, 200.0), (None, 300.0)]
# (upper bound of the tier in monthly kWh or None = unbounded, won per kWh)
PLACEHOLDER_NOTE = "예시용 임의 값입니다 (실제 한전 요금 아님). --config 의 power.tiers 또는 --tiers 로 직접 지정하세요."


class PowerError(RuntimeError):
    pass


_FORMATS = ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _ts(s: str) -> Optional[datetime]:
    s = s.strip()
    for f in _FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def parse_log(text: str) -> Tuple[List[Tuple[datetime, float]], int]:
    """Return (samples, skipped_rows). Header optional; assumes timestamp,power order without one.
    Rows with '[N/A]' or unparsable values are skipped."""
    samples, skipped = [], 0
    ts_col, pw_col = 0, 1
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line:
            continue
        cols = [c.strip() for c in line.split(",")]
        low = [c.lower() for c in cols]
        if any("timestamp" in c for c in low) or any("power" in c for c in low):
            for n, c in enumerate(low):
                if "timestamp" in c:
                    ts_col = n
                elif "power.draw" in c or c.startswith("power"):
                    pw_col = n
            continue
        if len(cols) <= max(ts_col, pw_col):
            skipped += 1
            continue
        t = _ts(cols[ts_col])
        m = _NUM.search(cols[pw_col])
        if t is None or m is None:
            skipped += 1
            continue
        samples.append((t, float(m.group())))
    return samples, skipped


def energy_kwh(samples: List[Tuple[datetime, float]], max_gap_s: float = 60.0) -> Tuple[float, float]:
    """Trapezoid integration. Gaps longer than max_gap_s are not counted. Returns (kWh, counted_seconds)."""
    samples = sorted(samples)
    joules, secs = 0.0, 0.0
    for (t0, w0), (t1, w1) in zip(samples, samples[1:]):
        dt = (t1 - t0).total_seconds()
        if dt <= 0 or dt > max_gap_s:
            continue
        joules += (w0 + w1) / 2.0 * dt
        secs += dt
    return joules / 3.6e6, secs


def parse_tiers(spec: str) -> List[Tuple[Optional[float], float]]:
    """'200=100,400=200,inf=300' -> [(200,100),(400,200),(None,300)]"""
    tiers = []
    for part in spec.split(","):
        k, _, v = part.partition("=")
        try:
            tiers.append((None if k.strip().lower() in ("inf", "") else float(k), float(v)))
        except ValueError:
            raise PowerError("잘못된 구간 지정: %r (예: 200=100,400=200,inf=300)" % part)
    return validate_tiers(tiers)


def tiers_from_config(cfg: dict):
    raw = (cfg.get("power") or {}).get("tiers")
    if not raw:
        return None
    try:
        return validate_tiers([(None if t.get("upto_kwh") is None else float(t["upto_kwh"]), float(t["rate"]))
                               for t in raw])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PowerError('config power.tiers 형식: [{"upto_kwh": 200, "rate": 100}, {"upto_kwh": null, "rate": 300}]')


def validate_tiers(tiers):
    if not tiers or tiers[-1][0] is not None:
        raise PowerError("마지막 구간은 상한 없음(inf / null)이어야 합니다")
    last = 0.0
    for up, rate in tiers:
        if rate < 0 or (up is not None and up <= last):
            raise PowerError("구간 상한은 오름차순, 단가는 0 이상이어야 합니다")
        last = up if up is not None else last
    return tiers


def tiered_cost(kwh: float, tiers) -> float:
    cost, lo = 0.0, 0.0
    for up, rate in tiers:
        hi = kwh if up is None else min(kwh, up)
        if hi > lo:
            cost += (hi - lo) * rate
        if up is None or kwh <= up:
            break
        lo = up
    return cost


def job_cost(kwh: float, tiers, base_kwh: float = 0.0) -> float:
    """Marginal cost of this job on top of `base_kwh` already used this month (누진 반영)."""
    return tiered_cost(base_kwh + kwh, tiers) - tiered_cost(base_kwh, tiers)
