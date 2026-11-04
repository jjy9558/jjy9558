"""규칙 기반 한국어 의도 파서. LLM 없이 핵심 명령을 처리한다.

parse("자비스, 오늘 일정이랑 작업 상태 알려줘")
  -> Parsed(wake=True, text="오늘 일정이랑 작업 상태 알려줘",
            intents=[Intent("schedule"), Intent("status")])

띄어쓰기는 사람마다 달라서 비교는 '공백/문장부호를 지운 소문자 문자열'(compact)로 한다.
한 문장에 여러 의도가 있으면 문장에 나온 순서대로 모두 돌려준다.
다만 claude/ask/approve/reject 는 문장 전체가 하나의 명령이라 단독(exclusive)으로 처리한다.
"""
import re
from collections import namedtuple

Intent = namedtuple("Intent", "name args")
Parsed = namedtuple("Parsed", "wake text intents")

DEFAULT_WAKE_WORDS = ("자비스", "쟈비스", "jarvis")

PC_WORDS = r"(?:컴퓨터|컴|피씨|pc|본컴|메인컴|메인피씨|메인pc|메인|데스크톱|데스크탑|5080|큰컴)"
ANY_DEVICE = r"(?:%s|미니피씨|미니pc|미니컴|기기|장치|디바이스|폰|s21|노트북)" % PC_WORDS[3:-1]


def compact(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())


def strip_wake(text, wake_words=DEFAULT_WAKE_WORDS):
    words = "|".join(re.escape(w) for w in sorted(wake_words, key=len, reverse=True))
    m = re.match(r"^\s*(?:(?:헤이|hey|오케이|ok|야)\s*,?\s*)?(?:%s)(?:야|아|님|씨)?[\s,.!?~:]*"
                 % words, text or "", re.I)
    if not m:
        return False, (text or "").strip()
    return True, text[m.end():].strip()


def _num(s):
    m = re.search(r"#?(\d{1,9})\s*번?", s)
    return int(m.group(1)) if m else None


_CLAUDE_HEAD = re.compile(r"^\s*(?:클로드\s*코드|클로드|claude(?:\s*code)?)\s*(?:한테|에게|더러|보고|야|아|,)?\s*", re.I)
_CLAUDE_TAIL = re.compile(
    r"(?:(?<=라고)\s*(?:좀\s*)?(?:해\s*줘|해|전해\s*줘)"
    r"|\s*(?:좀\s*)?(?:시켜\s*줘|시켜\s*봐|시켜|부탁해\s*줘|부탁해|부탁할게|맡겨\s*줘|맡겨))\s*[.!?~]*\s*$")
_ASK = re.compile(
    r"^\s*(?:(?:로컬\s*)?(?:모델|llm|ai|에이아이|5080|gpt|큰\s*컴)\s*(?:한테|에게)\s*)?(.+?)\s*(?:좀\s*)?(?:물어\s*봐\s*(?:줘|줄래)?|물어\s*봐라|질문해\s*줘)\s*[.!?~]*\s*$",
    re.I)
_FILE = re.compile(r"([\w\-.]+\.(?:pdf|txt|md))", re.I)

_WAKE_PC = re.compile(PC_WORDS + r"(?:좀|를|을|도)?(?:켜(?!져|졌|진|있|지|ㅕ)|깨워|부팅|키어|켜라|on)")
_DEVICES = re.compile(
    ANY_DEVICE + r"(?:가|는|이|은|들|들은|쪽)?(?:지금)?(?:켜져|꺼져|켜졌|꺼졌|켜있|꺼있|살아|죽었|온라인|오프라인|상태|어때|돌아|접속|목록|연결)")
_STATUS = re.compile(r"(?:작업|잡|job|큐|queue|할일|일감)(?:들|이|은|는|에|가|이랑)?(?:지금)?(?:상태|현황|진행|목록|몇|있|어때|알려|뭐|얼마|남)"
                     r"|진행상황|진행중인|뭐하고있|뭐하는중|돌아가는거|대기중인|승인대기")
_SCHEDULE = re.compile(r"(?:일정|스케줄|스케쥴|약속|캘린더|calendar)")
_SUMMARY = re.compile(r"요약|정리해줘|핵심만")
_HELP = re.compile(r"뭐할수있|도움말|명령어|할수있는거|사용법|^help$")
_TIME = re.compile(r"몇시|지금시간|시간알려")
_APPROVE = re.compile(r"(?:승인|허락|허가|오케이해|ok해|진행시켜|진행해)(?:해|할게|하자|좀|해줘|해라|$)")
_REJECT = re.compile(r"(?:거절|반려|취소|캔슬|그만둬|하지마)(?:해|할게|하자|좀|해줘|해라|시켜|$)")
_RUN_VERB = re.compile(r"(?:실행|돌려|시작|해줘|해라|해$|켜줘|시켜)")


_HPATCH = re.compile(r"한글패치|한글화|한패치")
_HPATCH_VERB = re.compile(r"돌려|이어|계속|시작|실행|번역|적용|해줘|해라|해$|시켜|진행")
_WIKI = re.compile(r"위키|wiki", re.I)
_WIKI_TAIL = re.compile(
    r"\s*(?:좀\s*)?(?:내?\s*)?(?:위키|wiki)(?:에서|에|를|서|좀)?\s*(?:좀\s*)?"
    r"(?:찾아\s*(?:봐\s*줘|봐줘|봐|줘)?|검색\s*(?:해\s*(?:봐|줘)?)?|알아\s*봐\s*줘|알려\s*줘|물어\s*봐\s*줘?|봐\s*줘)?\s*[.!?~]*\s*$", re.I)
_WIKI_HEAD = re.compile(r"^\s*(?:내?\s*)?(?:위키|wiki)(?:에서|에|를|서)?\s*", re.I)
_WIKI_VERB_TAIL = re.compile(r"\s*(?:좀\s*)?(?:찾아\s*(?:봐\s*줘|봐줘|봐|줘)?|검색\s*(?:해\s*(?:봐|줘)?)?|알려\s*줘)\s*[.!?~]*\s*$")


def _game_names(games):
    """{"key": ["별칭", ...]} 또는 {"key": {"names": [...]}} -> [(key, [names])]"""
    out = []
    for key, v in (games or {}).items():
        names = v.get("names", []) if isinstance(v, dict) else (v if isinstance(v, (list, tuple)) else [])
        out.append((key, [key] + [n for n in names if isinstance(n, str) and n.strip()]))
    return out


def match_game(rest, games):
    """문장 속 게임 별칭 -> (key, 별칭 부분을 뺀 나머지 문장). 못 찾으면 (None, rest)."""
    best = None
    for key, names in _game_names(games):
        for n in names:
            m = re.search(re.escape(n.strip()), rest, re.I)
            if m and (best is None or len(n) > best[0]):
                best = (len(n), key, m)
    if not best:
        return None, rest
    m = best[2]
    return best[1], (rest[:m.start()] + " " + rest[m.end():]).strip()


def _find(regex, c):
    m = regex.search(c)
    return m.start() if m else None


def parse(text, wake_words=DEFAULT_WAKE_WORDS, aliases=None, games=None):
    wake, rest = strip_wake(text, wake_words)
    c = compact(rest)
    if not c:
        return Parsed(wake, rest, [])

    # ---- 단독 의도 ----
    if _CLAUDE_HEAD.match(rest) and (re.search(r"(시켜|부탁|맡겨|라고|한테|에게)", rest) or len(c) > 12):
        prompt = _CLAUDE_TAIL.sub("", _CLAUDE_HEAD.sub("", rest, count=1)).strip(" ,.")
        return Parsed(wake, rest, [Intent("claude", {"prompt": prompt})])
    if _HPATCH.search(c) and _HPATCH_VERB.search(c):
        key, left = match_game(rest, games)
        return Parsed(wake, rest, [Intent("hpatch", {"game": key, "apply": "적용" in c,
                                                     "game_text": _HPATCH.sub("", left).strip(" ,.")})])
    if _WIKI.search(c) and not _CLAUDE_HEAD.match(rest):
        key, left = match_game(rest, games)
        q = _WIKI_TAIL.sub("", left)
        if q == left:  # '위키에서 ~ 찾아줘' 꼴
            q = _WIKI_VERB_TAIL.sub("", _WIKI_HEAD.sub("", left, count=1))
        q = _WIKI_HEAD.sub("", q, count=1).strip(" ,.")
        return Parsed(wake, rest, [Intent("wiki", {"game": key, "question": q})])
    m = _ASK.match(rest)
    if m:
        return Parsed(wake, rest, [Intent("ask", {"question": m.group(1).strip(" ,.")})])
    if "대기" not in c and _APPROVE.search(c):
        return Parsed(wake, rest, [Intent("approve", {"id": _num(rest)})])
    if _REJECT.search(c):
        return Parsed(wake, rest, [Intent("reject", {"id": _num(rest)})])

    # ---- 여러 개 가능한 의도 ----
    found = []

    def add(pos, intent):
        if pos is not None and intent.name not in [i.name for _, i in found]:
            found.append((pos, intent))

    pos_wake = _find(_WAKE_PC, c)
    add(pos_wake, Intent("wake_pc", {}))
    pos_dev = _find(_DEVICES, c)
    add(pos_dev, Intent("devices", {}))
    add(_find(_STATUS, c), Intent("status", {}))
    add(_find(_SCHEDULE, c), Intent("schedule", {}))
    pos_sum = _find(_SUMMARY, c)
    if pos_sum is not None:
        fm = _FILE.search(rest)
        kind = "pdf" if re.search(r"pdf|피디에프", c) else ("file" if re.search(r"파일|문서|이거|방금", c) else "")
        add(pos_sum, Intent("summarize", {"file": fm.group(1) if fm else "@latest", "kind": kind}))
    add(_find(_HELP, c), Intent("help", {}))
    add(_find(_TIME, c), Intent("time", {}))
    for alias, spec in (aliases or {}).items():
        a = compact(alias)
        if a and a in c and _RUN_VERB.search(c[c.index(a) + len(a):]):
            add(c.index(a), Intent("run", {"alias": alias, "spec": spec}))
            break
    # '상태 알려줘'처럼 대상이 없으면 작업 상태로 본다
    if not found and re.search(r"상태|현황|어때", c):
        add(0, Intent("status", {}))
    found.sort(key=lambda p: p[0])
    return Parsed(wake, rest, [i for _, i in found])
