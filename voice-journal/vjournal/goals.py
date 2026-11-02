# -*- coding: utf-8 -*-
"""선언 감지(한국어 패턴) 와 진행 감지. 순수 함수만 둔다."""
import calendar
import math
import re
from collections import namedtuple
from datetime import date, timedelta

from .parse import COUNT_UNITS, NUM_RE, find_number, jong, split_clauses, split_sentences

Declaration = namedtuple("Declaration", "title keywords period target unit deadline start raw")
Progress = namedtuple("Progress", "goal_id amount note kind")  # kind: progress | drop

WEEKDAYS = "월화수목금토일"


def _end_of_week(d):
    return d + timedelta(days=6 - d.weekday())


def _monday(d):
    return d - timedelta(days=d.weekday())


def _end_of_month(d):
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _safe_date(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


# --- 범위(기한/주기) 파싱 ---------------------------------------------------

_DEADLINE_WORDS = r"(?:까지|안에|전에|까진|내로|이내)"


class _Scope(object):
    def __init__(self):
        self.period = None      # day | week | None
        self.deadline = None
        self.start = None
        self.found = False
        self.weak = False       # 기한 키워드가 약한(오늘/내일 등 까지 없는) 경우는 아예 매칭하지 않는다


def parse_scope(sentence, today):
    """문장에서 기한/주기 표현을 찾아 (scope, 표현을 지운 문장)을 돌려준다."""
    s = sentence
    sc = _Scope()
    week_off = None  # 이번 주=0, 다음 주=1

    def take(pattern, flags=0):
        nonlocal s
        m = re.search(pattern, s, flags)
        if m:
            s = s[:m.start()] + " " + s[m.end():]
            sc.found = True
        return m

    # 시작일: ~부터
    m = take(r"(오늘|내일|모레)\s*부터")
    if m:
        sc.start = today + timedelta(days=("오늘", "내일", "모레").index(m.group(1)))
    m = take(r"(?:다음|담)\s*주\s*부터")
    if m:
        sc.start = _monday(today) + timedelta(days=7)
    m = take(r"([월화수목금토일])요일\s*부터")
    if m:
        wd = WEEKDAYS.index(m.group(1))
        sc.start = today + timedelta(days=(wd - today.weekday()) % 7 or 7)

    # 반복
    if take(r"매일|매일매일|하루에|날마다|하루도\s*빠짐없이"):
        sc.period = "day"
    elif take(r"매주|(?<![가-힣])주\s*(?=\d+\s*(?:회|번))|일주일에\s*(?=\d+\s*(?:회|번))"):
        sc.period = "week"

    # 주 단위 접두
    m = take(r"(이번|다음|담)\s*주(?=\s*[월화수목금토일]요일)")
    if m:
        week_off = 0 if m.group(1) == "이번" else 1

    # 상대 기한
    m = take(r"(\d+)\s*일\s*(?:안에|내|이내)(?!\S*까지\d)")
    if m:
        sc.deadline = today + timedelta(days=int(m.group(1)))
    if sc.deadline is None:
        m = take(r"(\d+)\s*주(?:일)?\s*(?:안에|내|이내)")
        if m:
            sc.deadline = today + timedelta(days=7 * int(m.group(1)))

    # 절대 날짜
    if sc.deadline is None:
        m = take(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*" + _DEADLINE_WORDS)
        if m:
            d = _safe_date(today.year, int(m.group(1)), int(m.group(2)))
            if d is not None and (today - d).days > 30:
                d = _safe_date(today.year + 1, int(m.group(1)), int(m.group(2)))
            sc.deadline = d
    if sc.deadline is None:
        m = take(r"(\d{1,2})\s*일\s*(?:까지|까진)")
        if m:
            day = int(m.group(1))
            d = _safe_date(today.year, today.month, day)
            if d is None or d < today:
                ny, nm = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)
                d = _safe_date(ny, nm, day)
            sc.deadline = d

    # 요일 (까지/안에/전에 필수)
    if sc.deadline is None:
        m = take(r"([월화수목금토일])요일\s*" + _DEADLINE_WORDS)
        if m:
            wd = WEEKDAYS.index(m.group(1))
            if week_off is None:
                sc.deadline = today + timedelta(days=(wd - today.weekday()) % 7)
            else:
                sc.deadline = _monday(today) + timedelta(days=7 * week_off + wd)

    # 주말
    if sc.deadline is None:
        m = take(r"(이번\s*|다음\s*)?주말\s*(?:" + _DEADLINE_WORDS + r"|에)?")
        if m:
            base = today + timedelta(days=7) if (m.group(1) or "").startswith("다음") else today
            sc.deadline = _end_of_week(base)

    # 이번 주 / 다음 주 / 이번 달 / 다음 달
    if sc.deadline is None:
        m = take(r"(이번|다음|담)\s*주(?:\s*(?:안에|내|중|까지|에|동안))?")
        if m:
            base = today if m.group(1) == "이번" else today + timedelta(days=7)
            sc.deadline = _end_of_week(base)
    if sc.deadline is None:
        m = take(r"(이번|다음|담)\s*달(?:\s*(?:안에|내|중|까지|에|동안))?")
        if m:
            base = today
            if m.group(1) != "이번":
                base = _end_of_month(today) + timedelta(days=1)
            sc.deadline = _end_of_month(base)

    # 오늘/내일/모레 (까지 필수)
    if sc.deadline is None:
        m = take(r"(오늘|내일|모레|글피)\s*(?:까지|안에|중으로|중|까진)")
        if m:
            sc.deadline = today + timedelta(days=("오늘", "내일", "모레", "글피").index(m.group(1)))

    if sc.period == "day" and sc.deadline is None and sc.start is None:
        pass
    s = re.sub(r"\s+", " ", s).strip(" ,.:;~-")
    return sc, s


# --- 선언(약속) 어미 --------------------------------------------------------

_NEG_RE = re.compile(r"(?:^|\s)(?:안|못)\s|않|말자|그만|포기|접(?:었|기로|자)|미루|취소|중단")
_QUESTION_RE = re.compile(r"[?？]\s*$")
_GOAL_WORD = re.compile(r"목표|다짐|결심|선언")


def _strip_end(s):
    t = re.sub(r"[\s.!~ㅋㅎ…^)]+$", "", s)
    if t.endswith("요") and len(t) > 2:
        t = t[:-1]
    return t


def is_commitment(sentence):
    t = _strip_end(sentence)
    if len(t) < 2 or _QUESTION_RE.search(sentence):
        return False
    if _GOAL_WORD.search(t):
        return True
    if re.search(r"겠(?:다|어|음|습니다|네)$", t):
        return True
    if re.search(r"기로\s*(?:했|한다|함|결심|마음|정했|해)", t):
        return True
    if re.search(r"(?:야지|야겠다|야겠어|야겠음|야겠|자)$", t) and len(t) >= 3:
        return True
    if t.endswith("다") and len(t) >= 3:
        prev = t[-2]
        if prev == "는" or jong(prev) == 4:   # 한다/쓴다/낸다/는다
            return True
        if t.endswith("습니다") or t.endswith("ㅂ니다"):
            return False
        if jong(prev) == 17 and t.endswith("니다") is False:
            return False
    if t.endswith("니다") and len(t) >= 3 and jong(t[-3]) == 17 and not t.endswith("습니다") \
            and not t.endswith("입니다"):
        return True   # 합니다/씁니다/끝냅니다
    m = re.search(r"([가-힣])\s*(?:거(?:야|다|임|에|예)|겁니다|것이다|것이야|예정|계획)(?:이다|이야|입니다)?$", t)
    if m and jong(m.group(1)) == 8:
        return True
    m = re.search(r"([가-힣])(?:게|래)$", t)
    if m and jong(m.group(1)) == 8 and len(t) >= 3:
        return True
    return False


_PAST_HINT = re.compile(r"(?:었|았|했|썼|갔|봤|왔|냈|줬|였|ㅆ)[다어음네지고죠는]")


def _has_past(s):
    t = _strip_end(s)
    tail = t[-6:]
    for i, ch in enumerate(tail):
        if jong(ch) == 20:   # ㅆ 받침: 했/썼/었/았 ...
            return True
    return False


# --- 키워드 ------------------------------------------------------------------

_STOP = {"오늘", "내일", "이번", "다음", "일", "것", "거", "좀", "꼭", "진짜", "반드시", "무조건", "다시", "더"}
_PART_LONG = ("에서는", "에서", "으로", "까지", "부터", "이랑", "에는")
_PART_ONE = ("을", "를", "은", "는", "이", "가", "도", "에", "로", "의", "과", "와", "만")


def _strip_particle(tok):
    for p in _PART_LONG:
        if tok.endswith(p) and len(tok) - len(p) >= 2:
            return tok[:-len(p)]
    for p in _PART_ONE:
        if tok.endswith(p) and len(tok) - len(p) >= 2:
            return tok[:-len(p)]
    if len(tok) == 2 and tok[-1] in "을를":
        return tok[:-1]
    return tok


_VERBISH = re.compile(r"(?:다|요|어|야|지|게|래|자|함|임|기로|거야|거다|예정)$")


_TAIL_FUT = re.compile(r"\s+\S+\s*(?:거(?:야|다|임|에요|예요)|겁니다|것이다|예정|계획)\S*$")
_VERB_SUFFIX = re.compile(r"(?:하겠다|하겠어|할게|할래|한다|합니다|하자|해야지|하기로|하기|하는|하고)$")


def extract_keywords(title):
    title = _TAIL_FUT.sub("", title)
    title = NUM_RE.sub(" ", title)
    toks = title.split()
    if toks:
        m = _VERB_SUFFIX.search(toks[-1])
        if m and len(toks[-1]) - len(m.group(0)) >= 2:
            toks[-1] = toks[-1][:m.start()]
    kws = []
    for i, tok in enumerate(toks):
        if re.search(r"\d", tok) or find_number(tok):
            continue
        if i == len(toks) - 1 and len(toks) > 1 and _VERBISH.search(tok):
            continue
        tok = re.sub(r"^[\"'(\[]+|[\"')\].,!?:;]+$", "", tok)
        tok = _strip_particle(tok)
        if not tok or tok in _STOP or tok in ("목표", "목표는", "다짐"):
            continue
        if _VERBISH.search(tok) and len(toks) > 1 and i != 0:
            continue
        kws.append(tok)
    if not kws and toks:
        kws = [_strip_particle(toks[0])]
    return kws


def keywords_match(keywords, sentence):
    if not keywords:
        return False
    hit = sum(1 for k in keywords if k.lower() in sentence.lower())
    need = 1 if len(keywords) <= 2 else int(math.ceil(len(keywords) / 2.0))
    return hit >= need


# --- 선언 감지 -----------------------------------------------------------------

def detect_declarations(text, today):
    """텍스트에서 선언을 찾아 Declaration 리스트로. today 는 date."""
    decls = []
    for sent in split_sentences(text):
        for clause in split_clauses(sent):
            d = _detect_one(clause, today)
            if d is not None:
                decls.append(d)
    return decls


def declaration_sentences(text, today):
    """선언으로 판정된 절(원문) 집합 - 진행 감지에서 제외하려고 사용."""
    out = set()
    for sent in split_sentences(text):
        for clause in split_clauses(sent):
            if _detect_one(clause, today) is not None:
                out.add(clause)
    return out


def _detect_one(clause, today):
    raw = re.sub(r"\s+", " ", re.sub(r"(?<![\w&/])#[\w가-힣]+", " ", clause)).strip()
    if not raw or _QUESTION_RE.search(raw) or raw.startswith("#"):
        return None
    sc, rest = parse_scope(raw, today)
    has_goal_word = bool(_GOAL_WORD.search(rest))
    if not sc.found and not has_goal_word:
        return None
    if _NEG_RE.search(rest):
        return None
    committed = is_commitment(rest)
    num = find_number(rest)
    implicit = (sc.period in ("day", "week")) and num is not None and not _has_past(rest)
    if not committed and not implicit:
        return None
    if committed is False and _has_past(rest):
        return None
    if sc.found and not committed and not implicit:
        return None
    title = re.sub(r"^(?:목표|다짐|결심|선언)\s*(?:는|은|:)?\s*", "", rest).strip()
    title = re.sub(r"^[은는이가도에을를\s]+(?=[가-힣A-Za-z0-9])", "", title) if sc.found else title
    if len(title) < 2:
        return None
    period = sc.period or "once"
    target, unit = (num if num else (None, None))
    return Declaration(title=title, keywords=extract_keywords(title), period=period,
                       target=target, unit=unit, deadline=sc.deadline, start=sc.start, raw=raw)


# --- 진행 감지 -----------------------------------------------------------------

_DONE_WORDS = re.compile(r"완료|끝냄|마침|달성|성공|발행|제출|완성|해냈|올렸|끝났")
_NOT_DONE = re.compile(
    r"(?:^|\s)(?:못|안)\s*[가-힣]|하지\s*못|않았|미뤘|미룸|미뤄|려고\s*했|하려다|할\s*뻔|싶었|지만|는데\s*$|았는데|었는데|했는데")
_DROP_RE = re.compile(r"포기|접었|접기로|접고|그만\s*(?:두|뒀|둘)|그만하|안\s*하기로|하지\s*않기로|취소|중단|내려놓")


def detect_progress(text, goals, today, exclude=()):
    """text 의 각 문장을 열린 목표와 대조해 Progress 리스트를 돌려준다.

    goals: dict(id, keywords(list), period, target, unit, ...) 의 iterable.
    exclude: 선언 문장(진행으로 세지 않음).
    """
    results = []
    used = set()
    for sent in split_sentences(text):
        for clause in split_clauses(sent):
            if clause in exclude or _QUESTION_RE.search(clause):
                continue
            body = re.sub(r"\s+", " ", re.sub(r"(?<![\w&/])#[\w가-힣]+", " ", clause)).strip()
            for g in goals:
                if not keywords_match(g["keywords"], clause):
                    continue
                if _DROP_RE.search(clause):
                    if ("drop", g["id"]) not in used:
                        used.add(("drop", g["id"]))
                        results.append(Progress(g["id"], 0, clause, "drop"))
                    continue
                if _NOT_DONE.search(body):
                    continue
                if not (_has_past(body) or _DONE_WORDS.search(body)):
                    continue
                num = find_number(body)
                target = g.get("target")
                unit = g.get("unit")
                if num is not None and (unit is None or num[1] == unit or target is None):
                    amount = num[0]
                elif num is not None:
                    amount = num[0]
                elif target is not None and target > 1 and (unit not in COUNT_UNITS):
                    amount = target
                elif g["period"] == "day" and target:
                    amount = target
                else:
                    amount = 1
                results.append(Progress(g["id"], amount, clause, "progress"))
    return results
