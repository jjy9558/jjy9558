"""한국어 서류 텍스트 파싱 유틸: 금액, 날짜, 시각, 청구월, 식별번호."""
from __future__ import annotations

import calendar
import datetime as dt
import re
import unicodedata
from typing import List, NamedTuple, Optional, Tuple

# --------------------------------------------------------------------------
# 정규화
# --------------------------------------------------------------------------


def normalize_text(s: str) -> str:
    """NFKC 정규화(전각->반각, ㈜->(주), ㎥->m3), 줄바꿈/공백 정리."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace(" ", " ").replace("​", "").replace("﻿", "")
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --------------------------------------------------------------------------
# 금액
# --------------------------------------------------------------------------
_N = r"\d{1,3}(?:,\d{3}){1,6}|\d{1,13}"
_BODY = (
    r"(?=\d)"
    r"(?:(?P<eok>" + _N + r")\s*억\s*)?"
    r"(?:(?P<cheonman>" + _N + r")\s*천\s*만\s*)?"
    r"(?:(?P<man>(?:" + _N + r")(?:\.\d+)?)\s*만\s*)?"
    r"(?:(?P<cheon>" + _N + r")\s*천\s*)?"
    r"(?:(?P<baek>" + _N + r")\s*백\s*)?"
    r"(?P<rest>" + _N + r")?"
)
AMOUNT_RE = re.compile(
    r"(?:(?P<cur>₩|KRW|krw)\s*)?(?<![\d,])" + _BODY +
    r"(?:\s*(?P<won>원(?!본|래|룸|금|가|문|인|장|칙|산지|형)))?"
)

_MAX_AMOUNT = 10 ** 11


def _to_int(s: str) -> int:
    return int(s.replace(",", "")[:20])


class AmountTok(NamedTuple):
    start: int
    end: int
    value: int
    marked: bool      # '원' 또는 통화기호가 붙음
    has_unit: bool    # 억/만/천/백 표기 포함
    has_comma: bool


def iter_amounts(text: str, marked_only: bool = True) -> List[AmountTok]:
    out: List[AmountTok] = []
    for m in AMOUNT_RE.finditer(text):
        if not any(m.group(g) for g in ("eok", "cheonman", "man", "cheon", "baek", "rest")):
            continue
        value = 0
        if m.group("eok"):
            value += _to_int(m.group("eok")) * 100_000_000
        if m.group("cheonman"):
            value += _to_int(m.group("cheonman")) * 10_000_000
        if m.group("man"):
            value += int(round(float(m.group("man").replace(",", "")) * 10_000))
        if m.group("cheon"):
            value += _to_int(m.group("cheon")) * 1_000
        if m.group("baek"):
            value += _to_int(m.group("baek")) * 100
        if m.group("rest"):
            value += _to_int(m.group("rest"))
        marked = bool(m.group("won") or m.group("cur"))
        has_unit = any(m.group(g) for g in ("eok", "cheonman", "man", "cheon", "baek"))
        full = m.group(0)
        tok = AmountTok(m.start(), m.end(), value, marked, has_unit, "," in full)
        if marked_only and not marked:
            continue
        if value > _MAX_AMOUNT:
            continue
        if not marked:
            # 날짜/시각/전화번호 일부 같은 숫자는 제외
            before = text[tok.start - 1] if tok.start > 0 else ""
            after = text[tok.end:tok.end + 2]
            if (before and before in ".-/:,*") or re.match(r"[.\-/:,]\d", after):
                continue
        out.append(tok)
    return out


def parse_amount_str(s: str) -> Optional[int]:
    """'12,340원', '1만2천원', '₩5,000', '12340' 등 금액 문자열 하나를 정수(원)로."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    s = unicodedata.normalize("NFKC", str(s)).strip()
    toks = iter_amounts(s, marked_only=False)
    if not toks:
        return None
    return toks[0].value


def amount_after(text: str, label_re: "re.Pattern[str]", window: int = 36,
                 skip_ctx: Optional["re.Pattern[str]"] = None) -> Optional[int]:
    """라벨 뒤 window 글자 안에서 첫 금액. '원' 없이 숫자만 있어도 (3자리 이상/콤마) 허용."""
    for m in label_re.finditer(text):
        if skip_ctx is not None and skip_ctx.search(text[max(0, m.start() - 8):m.start()]):
            continue
        seg_start = m.end()
        seg = text[seg_start:seg_start + window]
        # 두 번째 줄바꿈 이후는 다른 항목
        nl = [i for i, c in enumerate(seg) if c == "\n"]
        if len(nl) >= 2:
            seg = seg[:nl[1]]
        for tok in iter_amounts(seg, marked_only=False):
            if tok.value == 0:
                continue
            if tok.marked or tok.has_comma or tok.has_unit or tok.value >= 100:
                return tok.value
    return None


# --------------------------------------------------------------------------
# 날짜 / 시각
# --------------------------------------------------------------------------
_DATE_RE = re.compile(
    r"""
    (?P<a>(?<![\d.])(?P<y1>20\d{2})\s*[.\-/년]\s*(?P<m1>\d{1,2})\s*[.\-/월]\s*(?P<d1>\d{1,2})(?!\d)(?:일(?![가-힣]))?)
   |(?P<b>(?<![\d.])(?P<y2>[2-3]\d)\.(?P<m2>\d{1,2})\.(?P<d2>\d{1,2})(?!\d))
   |(?P<c>(?<![\d])(?P<m3>\d{1,2})\s*월\s*(?P<d3>\d{1,2})\s*일)
   |(?P<e>(?<![\d.\-/])(?P<m5>\d{1,2})[./\-](?P<d5>\d{1,2})(?=\s*(?:오전|오후)?\s*\d{1,2}:\d{2}))
   |(?P<f>(?<![\d./\-])(?P<m4>\d{1,2})/(?P<d4>\d{1,2})(?![\d/]))
   |(?P<g>(?<![\d])(?P<d6>\d{1,2})\s*일(?!\s*(?:이내|전|간|동안|후|이상|이하|째|치|마다|씩|차)))
    """,
    re.X,
)

_TIME_RE = re.compile(r"(?P<ap>오전|오후)?\s*(?P<h>\d{1,2}):(?P<mi>\d{2})(?::\d{2})?")


class DateTok(NamedTuple):
    start: int
    end: int
    kind: str                 # 'ymd' | 'md' | 'd'
    year: Optional[int]
    month: Optional[int]
    day: int


def scan_dates(text: str) -> List[DateTok]:
    toks: List[DateTok] = []
    for m in _DATE_RE.finditer(text):
        lg = m.lastgroup
        g = m.groupdict()
        try:
            if g.get("a"):
                y, mo, d = int(g["y1"]), int(g["m1"]), int(g["d1"])
                kind = "ymd"
            elif g.get("b"):
                y, mo, d = 2000 + int(g["y2"]), int(g["m2"]), int(g["d2"])
                kind = "ymd"
            elif g.get("c"):
                y, mo, d = None, int(g["m3"]), int(g["d3"])
                kind = "md"
            elif g.get("e"):
                y, mo, d = None, int(g["m5"]), int(g["d5"])
                kind = "md"
            elif g.get("f"):
                y, mo, d = None, int(g["m4"]), int(g["d4"])
                kind = "md"
            else:
                y, mo, d = None, None, int(g["d6"])
                kind = "d"
        except (TypeError, ValueError):
            continue
        if mo is not None and not (1 <= mo <= 12):
            continue
        if not (1 <= d <= 31):
            continue
        if y is not None:
            try:
                dt.date(y, mo, d)
            except ValueError:
                continue
        toks.append(DateTok(m.start(), m.end(), kind, y, mo, d))
    return toks


def _clip_day(y: int, m: int, d: int) -> dt.date:
    return dt.date(y, m, min(d, calendar.monthrange(y, m)[1]))


def resolve_date(tok: DateTok, ref: dt.date, prefer: str = "future",
                 tol_days: int = 60) -> Optional[dt.date]:
    """연도/월이 없는 날짜를 기준일(ref) 주변으로 해석한다.

    prefer='future': 기한류 (ref - tol_days 보다 과거면 다음 해/달로).
    prefer='past'  : 결제/구매일 (ref 보다 30일 넘게 미래면 전년도로).
    """
    if tok.kind == "ymd":
        return dt.date(tok.year, tok.month, tok.day)
    if tok.kind == "md":
        try:
            cand = dt.date(ref.year, tok.month, tok.day)
        except ValueError:
            return None
        if prefer == "future":
            if cand < ref - dt.timedelta(days=tol_days):
                try:
                    cand = dt.date(ref.year + 1, tok.month, tok.day)
                except ValueError:
                    return None
        else:
            if cand > ref + dt.timedelta(days=30):
                try:
                    cand = dt.date(ref.year - 1, tok.month, tok.day)
                except ValueError:
                    return None
        return cand
    # 'd': 일만 있는 경우
    cand = _clip_day(ref.year, ref.month, tok.day)
    if prefer == "future":
        if cand < ref - dt.timedelta(days=min(tol_days, 3)):
            y, m = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
            cand = _clip_day(y, m, tok.day)
    else:
        if cand > ref:
            y, m = (ref.year - 1, 12) if ref.month == 1 else (ref.year, ref.month - 1)
            cand = _clip_day(y, m, tok.day)
    return cand


def _time_from_match(m: "re.Match[str]") -> Optional[Tuple[int, int]]:
    h, mi = int(m.group("h")), int(m.group("mi"))
    ap = m.group("ap")
    if ap == "오후" and h < 12:
        h += 12
    elif ap == "오전" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h, mi


def find_datetime(text: str, ref: dt.date, prefer: str = "past"
                  ) -> Tuple[Optional[dt.datetime], bool]:
    """결제/구매 일시를 찾는다. (datetime, 시각을 알았는지)."""
    toks = [t for t in scan_dates(text) if t.kind in ("ymd", "md")]
    first_date: Optional[dt.date] = None
    for tok in toks:
        d = resolve_date(tok, ref, prefer=prefer)
        if d is None:
            continue
        if first_date is None:
            first_date = d
        tm = _TIME_RE.match(text[tok.end:tok.end + 24].lstrip(" ,T"))
        if tm is None:
            before = text[max(0, tok.start - 14):tok.start]
            tms = list(re.finditer(r"(?:오전|오후)?\s*\d{1,2}:\d{2}(?::\d{2})?[\s,]*$", before))
            tm = _TIME_RE.search(tms[-1].group(0)) if tms else None
        if tm is not None:
            hm = _time_from_match(tm)
            if hm:
                return dt.datetime(d.year, d.month, d.day, hm[0], hm[1]), True
    if first_date is not None:
        return dt.datetime(first_date.year, first_date.month, first_date.day), False
    return None, False


# --------------------------------------------------------------------------
# 청구월
# --------------------------------------------------------------------------
_BM_PATTERNS = [
    (re.compile(r"(?:청구|부과|사용|귀속|고지)\s*(?:년\s*월|월)\s*[:]?\s*(20\d{2})\s*[.\-/년]?\s*(\d{1,2})(?!\d)"), "ym"),
    (re.compile(r"(?:청구|부과|사용|귀속|고지)\s*(?:년\s*월|월)\s*[:]?\s*(20\d{2})(0[1-9]|1[0-2])(?!\d)"), "ym"),
    (re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*월?\s*분"), "ym"),
    (re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*분"), "m"),
    (re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(?:전기|가스|수도|통신|관리비|요금|청구|사용|이용|보험료|건강보험|국민연금|고지|납부)"), "ym"),
    (re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(?:전기|가스|수도|통신|관리비|요금|청구|사용|이용|보험료|건강보험|국민연금)"), "m"),
]


def find_billing_month(text: str, ref: dt.date) -> Optional[str]:
    for rx, kind in _BM_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        if kind == "ym":
            y, mo = int(m.group(1)), int(m.group(2))
        else:
            y, mo = ref.year, int(m.group(1))
            if mo > ref.month + 1:
                y -= 1
        if 1 <= mo <= 12:
            return "%04d-%02d" % (y, mo)
    return None


# --------------------------------------------------------------------------
# 식별번호 (고객번호/납부번호 …)
# --------------------------------------------------------------------------
_CUSTOMER_RE = re.compile(
    r"(?:고객\s*번호|납부\s*자\s*번호|납부\s*번호|전자\s*납부\s*번호|수용가\s*번호|가입자\s*번호|"
    r"계약\s*번호|관리\s*번호|증권\s*번호|납세\s*번호|고지서\s*번호|사용\s*계약\s*번호)"
    r"\s*[:]?\s*([0-9][0-9A-Z-]{4,}[0-9A-Z])"
)


def find_customer_no(text: str) -> str:
    m = _CUSTOMER_RE.search(text)
    return m.group(1) if m else ""


def fmt_md(d: dt.date, today: Optional[dt.date] = None) -> str:
    """'11/25' (다른 해면 '2027/1/10')."""
    if today is not None and d.year != today.year:
        return "%d/%d/%d" % (d.year, d.month, d.day)
    return "%d/%d" % (d.month, d.day)


def won(n: Optional[int]) -> str:
    return "금액 미확인" if n is None else "{:,}원".format(n)
