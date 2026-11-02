# -*- coding: utf-8 -*-
"""텍스트 파싱 유틸: 해시태그, 기분, 문장 분리, 한글 숫자."""
import re

TAG_RE = re.compile(r"(?<![\w&/#])#([\w가-힣]+)", re.U)

FLAG_TAGS = {"중요", "flag", "기억", "잊지마"}


def parse_tags(text):
    """#해시태그 -> 소문자 태그 리스트(중복 제거, 순서 유지, 숫자만인 태그 제외)."""
    seen, out = set(), []
    for m in TAG_RE.finditer(text or ""):
        t = m.group(1).lower().strip("_")
        if not t or t.isdigit() or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def validate_mood(mood):
    if mood is None or mood == "":
        return None
    try:
        m = int(mood)
    except (TypeError, ValueError):
        raise ValueError("mood 는 1~5 정수여야 합니다: %r" % (mood,))
    if isinstance(mood, float) and mood != m:
        raise ValueError("mood 는 1~5 정수여야 합니다: %r" % (mood,))
    if not 1 <= m <= 5:
        raise ValueError("mood 는 1~5 정수여야 합니다: %r" % (mood,))
    return m


_SENT_SPLIT = re.compile(r"(?<=[.!?。…])\s+|[\r\n]+")
_CLAUSE_SPLIT = re.compile(r"\s*(?:;|,\s|\s그리고\s|\s그런데\s)\s*")


def split_sentences(text):
    out = []
    for s in _SENT_SPLIT.split(text or ""):
        s = s.strip()
        if s:
            out.append(s)
    return out


def split_clauses(sentence):
    return [c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c.strip()]


KOR_NUM = {
    "한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "석": 3, "네": 4, "넷": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}
UNITS = ("페이지|시간|문제|문장|단어|챕터|킬로|세트|km|kg|개|번|회|편|권|쪽|분|장|줄|잔|명|건|곡|곳")
COUNT_UNITS = {"개", "번", "회", "편", "권", "장", "건", "곡", "곳", "명", "잔", "줄", "문제", "문장", "단어", "챕터", "세트", "페이지", "쪽"}
NUM_RE = re.compile(
    r"(\d+(?:\.\d+)?|하나|둘|셋|넷|다섯|여섯|일곱|여덟|아홉|한|두|세|석|네|열)\s*(" + UNITS + r")"
)


def find_number(text):
    """첫 (값, 단위) 또는 None."""
    m = NUM_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1)
    if raw in KOR_NUM:
        val = float(KOR_NUM[raw])
    else:
        val = float(raw)
    if val == int(val):
        val = int(val)
    return val, m.group(2)


def jong(ch):
    """한글 음절의 종성 인덱스(없으면 0), 한글이 아니면 -1."""
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28
    return -1
