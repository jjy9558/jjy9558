"""사용자가 붙여넣은 시세 줄 -> 원 단위 숫자 -> 중앙값/범위. 수집·검색은 하지 않는다."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

# 숫자 + 단위 표현: "28만원", "25만 5천", "270,000원", "1.5만", "3천원"
_MAN = re.compile(r"(\d+(?:[.,]\d+)?)\s*만\s*(?:(\d+(?:,\d{3})*)\s*천)?\s*(?:(\d{1,4})\s*원)?\s*원?")
_CHEON = re.compile(r"(?<![\d만])(\d+(?:,\d{3})*)\s*천\s*원?")
_WON = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{4,9})\s*원")
_BARE = re.compile(r"(?<![\d,.A-Za-z-])(\d{1,3}(?:,\d{3})+|\d{5,9})\s*$")
_K = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[kK](?![A-Za-z])")


class PriceParseError(ValueError):
    pass


def parse_won(text: str) -> int | None:
    """'15만', '150,000', '15만 5천', '850k', '12000원' 한 덩어리를 원으로. 못 읽으면 None."""
    t = (text or "").strip().replace("₩", "").replace("KRW", "")
    if not t:
        return None
    m = _MAN.fullmatch(t.replace(" ", " ").strip())
    if m:
        man = float(m.group(1).replace(",", ""))
        cheon = int(m.group(2).replace(",", "")) * 1000 if m.group(2) else 0
        rest = int(m.group(3)) if m.group(3) else 0
        return int(round(man * 10000)) + cheon + rest
    m = _CHEON.fullmatch(t)
    if m:
        return int(m.group(1).replace(",", "")) * 1000
    m = _K.fullmatch(t)
    if m:
        return int(round(float(m.group(1)) * 1000))
    digits = re.fullmatch(r"(\d{1,3}(?:,\d{3})+|\d+)\s*원?", t)
    if digits:
        return int(digits.group(1).replace(",", ""))
    return None


def _line_prices(line: str) -> list[int]:
    found: list[tuple[int, int, int]] = []  # (start, end, value)

    def add(m, val):
        if val >= 1000:
            found.append((m.start(), m.end(), val))

    for m in _MAN.finditer(line):
        man = float(m.group(1).replace(",", ""))
        cheon = int(m.group(2).replace(",", "")) * 1000 if m.group(2) else 0
        rest = int(m.group(3)) if m.group(3) else 0
        add(m, int(round(man * 10000)) + cheon + rest)
    for m in _CHEON.finditer(line):
        if not any(a <= m.start() < b for a, b, _ in found):
            add(m, int(m.group(1).replace(",", "")) * 1000)
    for m in _WON.finditer(line):
        if not any(a <= m.start() < b or m.start() <= a < m.end() for a, b, _ in found):
            add(m, int(m.group(1).replace(",", "")))
    if not found:
        for m in _BARE.finditer(line):
            v = int(m.group(1).replace(",", ""))
            if 1990 <= v <= 2035 and "," not in m.group(1):
                continue  # 연도로 보이는 4자리
            add(m, v)
    for m in _K.finditer(line):
        if not any(a <= m.start() < b for a, b, _ in found):
            add(m, int(round(float(m.group(1)) * 1000)))
    found.sort()
    # 한 줄에서 하나만 시세로 쓴다: 마지막 금액(보통 '제목 ... 28만원' 형태)
    return [found[-1][2]] if found else []


@dataclass
class PriceSummary:
    values: list[int] = field(default_factory=list)
    skipped_lines: list[str] = field(default_factory=list)
    count: int = 0
    low: int = 0
    median: int = 0
    high: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"count": self.count, "low": self.low, "median": self.median, "high": self.high,
                "values": self.values, "warnings": self.warnings}


def parse_paste(text: str) -> tuple[list[int], list[str]]:
    values: list[int] = []
    skipped: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        got = _line_prices(line)
        if got:
            values.extend(got)
        else:
            skipped.append(line)
    return values, skipped


def round_won(value: float, unit: int = 1000) -> int:
    return int(round(value / unit)) * unit


def summarize(text: str) -> PriceSummary:
    values, skipped = parse_paste(text)
    s = PriceSummary(values=values, skipped_lines=skipped, count=len(values))
    if not values:
        s.warnings.append("붙여넣은 글에서 가격을 하나도 읽지 못했습니다")
        return s
    s.low, s.high = min(values), max(values)
    s.median = round_won(statistics.median(values))
    if s.count < 3:
        s.warnings.append("시세가 %d건뿐입니다. 3건 이상이면 중앙값이 덜 흔들립니다" % s.count)
    elif s.count < 5:
        s.warnings.append("시세 %d건: 참고용입니다. 5건 이상이 안정적입니다" % s.count)
    if s.median and (s.high > s.median * 3 or s.low < s.median / 3):
        s.warnings.append("중앙값에서 크게 벗어난 값이 있습니다(%s ~ %s). 다른 상태/구성의 글이 섞였는지 확인하세요"
                          % (format(s.low, ","), format(s.high, ",")))
    if skipped:
        s.warnings.append("가격을 못 읽어 건너뛴 줄 %d개" % len(skipped))
    return s
