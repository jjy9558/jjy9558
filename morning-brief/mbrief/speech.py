"""낭독용 문장 다듬기: 날짜, 시각, 금액, D-day 를 TTS 가 읽기 좋은 말로 바꾼다."""
from __future__ import annotations

import datetime as dt
import re

WEEKDAYS = "월화수목금토일"


def weekday_ko(d: dt.date) -> str:
    return WEEKDAYS[d.weekday()] + "요일"


def ko_date(d: dt.date, today: dt.date | None = None) -> str:
    """오늘/내일/모레/어제, 그 밖에는 '11월 5일 화요일'(해가 다르면 연도 포함)."""
    if today is not None:
        n = (d - today).days
        special = {0: "오늘", 1: "내일", 2: "모레", -1: "어제"}
        if n in special:
            return special[n]
    year = "%d년 " % d.year if today is not None and d.year != today.year else ""
    return "%s%d월 %d일 %s" % (year, d.month, d.day, weekday_ko(d))


def when_phrase(d: dt.date, today: dt.date) -> str:
    """마감/예정일을 말로: 오늘, 내일, 모레, 3~7일은 'N일 뒤', 그 밖에는 '11월 12일 목요일', 지난 날은 'N일 지남'."""
    n = (d - today).days
    if n < -1:
        return "%d일 지남" % -n
    if 3 <= n <= 7:
        return "%d일 뒤" % n
    return ko_date(d, today)


def ko_time(hour: int, minute: int = 0) -> str:
    """10:00 -> '오전 10시', 15:30 -> '오후 3시 30분', 02:10 -> '새벽 2시 10분'."""
    if hour == 12 and minute == 0:
        return "정오"
    if hour == 0 and minute == 0:
        return "자정"
    if hour < 6:
        part, h = "새벽", hour or 12
    elif hour < 12:
        part, h = "오전", hour
    elif hour < 18:
        part, h = "오후", hour - 12 or 12
    elif hour < 21:
        part, h = "저녁", hour - 12
    else:
        part, h = "밤", hour - 12
    out = "%s %d시" % (part, h)
    if minute:
        out += " %d분" % minute
    return out


def ko_won(amount: int) -> str:
    """324000 -> '32만 4천원', 45200 -> '4만 5천 2백원'."""
    amount = int(amount)
    if amount <= 0:
        return "0원"
    eok, rest = divmod(amount, 100_000_000)
    man, rest = divmod(rest, 10_000)
    cheon, rest = divmod(rest, 1000)
    baek, rest = divmod(rest, 100)
    parts = []
    if eok:
        parts.append("%d억" % eok)
    if man:
        parts.append("%d만" % man)
    if cheon:
        parts.append("%d천" % cheon)
    if baek:
        parts.append("%d백" % baek)
    if rest:
        parts.append("%d" % rest)
    return " ".join(parts) + "원"


def won_text(amount: int | None) -> str:
    """화면용 금액: 45,200원."""
    return "금액 미확인" if amount is None else "{:,}원".format(int(amount))


def shorten(text: str, limit: int) -> str:
    """limit 글자 이하로. 공백 경계에서 자르고 말줄임표를 붙인다."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:max(limit, 0)]
    cut = text[:limit - 1]
    sp = cut.rfind(" ")
    if sp >= (limit - 1) // 2:
        cut = cut[:sp]
    return cut.rstrip(" ,.;:") + "…"


_URL = re.compile(r"https?://\S+")
_ISO_DT = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?")
_ISO_D = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_HHMM = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_DDAY = re.compile(r"\bD-(?:DAY|0)\b|\bD-(\d+)\b|\bD\+(\d+)\b", re.I)
_WON_COMMA = re.compile(r"(\d{1,3}(?:,\d{3})+)\s*원")
_WON_PLAIN = re.compile(r"(?<![\d,])(\d{4,})\s*원")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_MD = re.compile(r"[*_`#>\[\]]|\(\s*\)")


def _date_words(y: str, m: str, d: str) -> str:
    return "%d월 %d일" % (int(m), int(d))


def speechify(text: str) -> str:
    """어떤 문장이든 낭독용으로 정리(멱등). 숫자·날짜·시각·금액을 읽기 좋게 바꾼다."""
    s = str(text)
    s = _URL.sub("링크", s)
    s = _ISO_DT.sub(lambda m: "%s %s" % (_date_words(*m.group(1, 2, 3)),
                                          ko_time(int(m.group(4)), int(m.group(5)))), s)
    s = _ISO_D.sub(lambda m: _date_words(*m.group(1, 2, 3)), s)
    s = _HHMM.sub(lambda m: ko_time(int(m.group(1)), int(m.group(2))), s)

    def dday(m: re.Match) -> str:
        if m.group(1):
            return "%d일 뒤" % int(m.group(1))
        if m.group(2):
            return "%d일 지남" % int(m.group(2))
        return "오늘"
    s = _DDAY.sub(dday, s)
    s = _WON_COMMA.sub(lambda m: ko_won(int(m.group(1).replace(",", ""))), s)
    s = _WON_PLAIN.sub(lambda m: ko_won(int(m.group(1))), s)
    s = _PERCENT.sub(lambda m: m.group(1) + " 퍼센트", s)
    s = s.replace("~", " 에서 ").replace("·", " ").replace("→", " 에서 ")
    s = _MD.sub("", s)
    s = re.sub(r"\s+([,.!?])", r"\1", s)
    return " ".join(s.split())
