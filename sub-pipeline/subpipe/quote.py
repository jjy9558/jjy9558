"""Quote sheet (견적서) in Markdown: per-minute rate x review multiplier."""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Dict

from . import price

_MODES = {"half_up": ROUND_HALF_UP, "up": ROUND_CEILING, "down": ROUND_FLOOR}


def round_amount(value, unit: int = 1, mode: str = "half_up") -> int:
    """Round a won amount to `unit` won. Half-up (not banker's rounding)."""
    if unit < 1 or mode not in _MODES:
        raise price.PriceError("unit은 1 이상, mode는 half_up|up|down 이어야 합니다")
    q = (Decimal(str(value)) / unit).quantize(Decimal(1), rounding=_MODES[mode])
    return int(q) * unit


def compute(seconds: float, rate: int, multiplier: float = 1.0, minute_rounding: str = "ceil",
            unit: int = 1, amount_mode: str = "half_up") -> Dict[str, float]:
    if multiplier <= 0:
        raise price.PriceError("검수 배수는 0보다 커야 합니다")
    bm = price.billable_minutes(seconds, minute_rounding)
    eff = Decimal(str(rate)) * Decimal(str(multiplier))
    raw = Decimal(str(bm)) * eff
    return {"seconds": seconds, "billable_minutes": bm, "rate": rate, "multiplier": multiplier,
            "effective_rate": float(eff), "raw": float(raw),
            "amount": round_amount(raw, unit, amount_mode)}


def render_md(title: str, q: Dict[str, float], minute_rounding: str, unit: int, amount_mode: str,
              notice: str = "") -> str:
    s = int(q["seconds"])
    out = ["# 견적서: %s" % title, "",
           "| 항목 | 내용 |", "|---|---|",
           "| 영상 길이 | %d분 %02d초 |" % (s // 60, s % 60),
           "| 과금 분량 | %g분 (분 단위 %s) |" % (q["billable_minutes"], minute_rounding),
           "| 분당 단가 | %s |" % price.won(q["rate"]),
           "| 검수 배수 | x%g (적용 단가 %s/분) |" % (q["multiplier"], price.won(q["effective_rate"])),
           "| 금액 반올림 | %d원 단위, %s |" % (unit, amount_mode),
           "| **견적 금액** | **%s** |" % price.won(q["amount"]), ""]
    if notice:
        out += [notice, ""]
    return "\n".join(out)
