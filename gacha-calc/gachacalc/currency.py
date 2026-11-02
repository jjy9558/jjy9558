"""재화 환율 환산. 충전 패키지(예: '6480원에 300 젬') 기준."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .validation import ValidationError, parse_number, parse_text

_PACKAGE_RE = re.compile(
    r"^\s*(?P<price>[\d,]+)\s*원\s*(?:에서|에|으로)?\s*(?P<units>[\d,]+)\s*(?P<unit>\S*)\s*$"
)


@dataclass(frozen=True)
class Package:
    price_krw: int
    units: int
    unit_name: str = "재화"

    def __post_init__(self) -> None:
        price = int(parse_number(self.price_krw, "패키지 가격(원)", integer=True, minimum=1))
        units = int(parse_number(self.units, "패키지 재화 수", integer=True, minimum=1))
        object.__setattr__(self, "price_krw", price)
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "unit_name", parse_text(self.unit_name, "재화 이름"))

    @property
    def krw_per_unit(self) -> float:
        return self.price_krw / self.units

    def krw_for_units(self, units: float) -> float:
        """환율 기준 환산 (연속값, 실제로는 패키지 단위로만 살 수 있다)."""
        return units * self.krw_per_unit

    def purchase_cost(self, units: float) -> int:
        """필요한 재화를 채우기 위해 패키지를 올림 구매할 때의 실제 결제액."""
        packages = math.ceil(units / self.units - 1e-9)
        return max(packages, 0) * self.price_krw


def parse_package(text: str) -> Package:
    """'6480원에 300 젬' 형식을 Package 로 바꾼다."""
    if not isinstance(text, str):
        raise ValidationError("충전 패키지는 문자열이어야 합니다")
    match = _PACKAGE_RE.match(text)
    if match is None:
        raise ValidationError(f"충전 패키지 형식이 아닙니다: {text!r} (예: '6480원에 300 젬')")
    return Package(
        price_krw=parse_number(match["price"], "패키지 가격(원)", integer=True, minimum=1),
        units=parse_number(match["units"], "패키지 재화 수", integer=True, minimum=1),
        unit_name=match["unit"] or "재화",
    )


def fmt_won(amount: float) -> str:
    """312 -> '312원'."""
    return f"{round(amount):,}원"


def fmt_krw(amount: float) -> str:
    """234567 -> '23만원', 34567 -> '3.5만원', 123456789 -> '1억 2,346만원'."""
    n = round(amount)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n < 10_000:
        return f"{sign}{n:,}원"
    if n < 100_000_000:
        man = n / 10_000
        if man >= 10:
            text = f"{man:,.0f}"
        else:
            text = f"{man:.1f}".rstrip("0").rstrip(".")
        return f"{sign}{text}만원"
    eok, rest = divmod(n, 100_000_000)
    man = round(rest / 10_000)
    if man == 10_000:
        eok, man = eok + 1, 0
    if man == 0:
        return f"{sign}{eok:,}억원"
    return f"{sign}{eok:,}억 {man:,}만원"
