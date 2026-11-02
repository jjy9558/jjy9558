"""질문 해석("리모컨 어디 뒀지?") → 검색 → 순위 → 한국어 답변."""
import datetime
import math
import re

from . import synonyms
from .vlm import VlmError

# ------------------------------------------------------------------ 질문 해석

_WS_PUNCT = re.compile(r"[?!.,~…;:\"'`()\[\]{}<>]+")
_VERB_PREFIX = (
    "어디", "어딨", "어딘", "어데", "언제", "뭐", "무엇", "마지막", "최근", "혹시", "제발", "그럼", "근데", "저기", "있잖",
    "뒀", "두었", "두웠", "둔", "두고", "두셨", "놨", "놓", "놔", "넣", "올려", "올렸", "갖다", "가져다", "있", "갔",
    "찾", "알려", "보여", "보이", "보았", "말해", "기억", "떨어뜨", "흘렸", "잃어", "잊어", "숨겼", "치웠", "치운",
    "해줘", "줘", "좀", "위치", "모르", "몰라", "궁금", "생각", "없",
)
_FILLER = {"에", "엔", "에는", "에도", "은", "는", "도", "의", "게", "거", "건", "것", "본", "봤", "봤어", "봤지", "본게", "본거", "본건", "봤던", "봤더라", "본적", "보면", "봐"}
_PRONOUN = {"내", "나", "내가", "제가", "저", "우리", "우리집", "나의", "제", "난", "넌", "네", "저의", "내꺼", "내거"}
_EN_STOP = {"where", "wheres", "is", "are", "was", "my", "the", "did", "i", "put", "leave", "left", "last", "seen", "see",
            "saw", "find", "a", "an", "do", "you", "know", "have", "can", "me", "tell", "show", "when", "it", "now",
            "located", "location", "of", "to", "at", "in", "on", "place", "placed", "dropped", "drop", "please", "lost"}
_PARTICLES = sorted(["이랑", "하고", "에서", "으로", "부터", "까지", "에게", "한테", "이나", "이란", "은", "는", "이", "가", "을", "를",
                     "에", "도", "만", "의", "랑", "와", "과", "로", "나", "란"], key=len, reverse=True)
_TIME_WORDS = {"오늘", "어제", "엊그제", "그저께", "그제", "아까", "방금", "지난주", "이번주", "지난밤", "어젯밤", "오늘아침",
               "오전", "오후", "아침", "저녁", "밤", "새벽", "낮", "today", "yesterday"}


def _strip_particle(tok):
    if synonyms.norm(tok) in synonyms.vocabulary():
        return tok
    for p in _PARTICLES:
        if tok.endswith(p) and len(tok) > len(p):
            rest = tok[:-len(p)]
            if len(p) >= 2 or len(tok) >= 3 or synonyms.norm(rest) in synonyms.vocabulary():
                return rest
    return tok


class Parsed:
    def __init__(self, target, window, window_label, kind):
        self.target = target          # 찾는 물건 (정규화 전 원형, 없으면 "")
        self.window = window          # (start_ts, end_ts) 또는 None
        self.window_label = window_label
        self.kind = kind              # "where" | "when"

    def __repr__(self):
        return "Parsed(%r, %r, %r)" % (self.target, self.window_label, self.kind)


def _midnight(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_time_hint(text, now):
    """(window, label, 남은 텍스트). 인식 못 하면 (None, "", text)."""
    dt = datetime.datetime.fromtimestamp(now)
    today = _midnight(dt)
    day = datetime.timedelta(days=1)
    label, start, end = "", None, None
    m = re.search(r"(\d+)\s*일\s*전", text)
    if m:
        n = int(m.group(1))
        start, end, label = today - n * day, today - (n - 1) * day, "%d일 전" % n
        text = text.replace(m.group(0), " ")
    else:
        m = re.search(r"(\d+)\s*시간\s*전", text)
        if m:
            n = int(m.group(1))
            start, end, label = dt - datetime.timedelta(hours=n + 1), dt, "%d시간 전" % n
            text = text.replace(m.group(0), " ")
    if start is None:
        for words, s, e, lab in (
            (("엊그제", "그저께", "그제"), today - 2 * day, today - day, "그저께"),
            (("어젯밤", "지난밤"), today - day + datetime.timedelta(hours=18), today + datetime.timedelta(hours=6), "어젯밤"),
            (("어제",), today - day, today, "어제"),
            (("지난주",), today - datetime.timedelta(days=today.weekday() + 7), today - datetime.timedelta(days=today.weekday()), "지난주"),
            (("이번주",), today - datetime.timedelta(days=today.weekday()), dt, "이번 주"),
            (("방금", "아까"), dt - datetime.timedelta(hours=3), dt, "아까"),
            (("오늘",), today, today + day, "오늘"),
        ):
            for w in words:
                if w in text:
                    start, end, label = s, e, lab
                    text = text.replace(w, " ")
                    break
            if start is not None:
                break
    # 시간대 힌트 (날짜와 결합)
    part = None
    for w, (h1, h2) in (("오전", (0, 12)), ("오후", (12, 24)), ("아침", (5, 11)), ("저녁", (17, 22)), ("새벽", (0, 6)), ("낮", (11, 17))):
        if re.search(r"(?<![가-힣])%s" % w, text):
            part = (w, h1, h2)
            text = re.sub(r"(?<![가-힣])%s" % w, " ", text, count=1)
            break
    if part:
        base = _midnight(datetime.datetime.fromtimestamp(start.timestamp() if start else now))
        if start is None:
            base = today
            label = "오늘"
        start = base + datetime.timedelta(hours=part[1])
        end = base + datetime.timedelta(hours=part[2])
        label = (label + " " + part[0]).strip()
    elif re.search(r"(?<![가-힣])밤", text):
        text = re.sub(r"(?<![가-힣])밤", " ", text, count=1)
    if start is None:
        return None, "", text
    return (start.timestamp(), end.timestamp()), label, text


def parse_query(q, now):
    raw = (q or "").strip()
    text = _WS_PUNCT.sub(" ", raw.lower().replace("'s", "s"))
    window, label, text = parse_time_hint(text, now)
    kind = "when" if re.search(r"언제|when", text) else "where"
    toks = []
    for tok in text.split():
        if tok in _PRONOUN or tok in _EN_STOP or tok in _FILLER:
            continue
        if tok.startswith(_VERB_PREFIX) and synonyms.norm(tok) not in synonyms.vocabulary():
            continue
        tok = _strip_particle(tok)
        if tok and tok not in _PRONOUN and tok not in _EN_STOP and tok not in _TIME_WORDS:
            toks.append(tok)
    return Parsed(" ".join(toks).strip(), window, label, kind)


# ------------------------------------------------------------------ 검색/순위

def score(quality, conf, age_s, half_life_h=48.0):
    """quality(일치도 0.4~1) × (0.4+0.6×신뢰도) × 반감기 감쇠. 높을수록 좋음."""
    age_h = max(0.0, age_s) / 3600.0
    return quality * (0.4 + 0.6 * conf) * math.pow(0.5, age_h / half_life_h)


def find(store, parsed, now, limit=3):
    """[(점수, 행, 품질)] 내림차순. 시간 창이 있으면 창 안을 우선하고 없으면 전체로 대체(fallback 플래그)."""
    target = parsed.target
    if not target:
        return [], False
    terms = synonyms.expand(target)
    nm = store.search(terms, "nm")
    wh = store.search(terms, "wh") - nm
    rows = store.sightings(nm | wh)
    tcanon, tnorm = synonyms.canonical(target), synonyms.norm(target)
    scored = []
    for r in rows:
        if tcanon and synonyms.canonical(r["name"]) == tcanon or synonyms.norm(r["name"]) == tnorm:
            qual = 1.0
        elif r["id"] in nm:
            qual = 0.7
        else:
            qual = 0.4
        scored.append((score(qual, r["conf"], now - r["ts"]), r, qual))
    fallback = False
    if parsed.window:
        inside = [s for s in scored if parsed.window[0] <= s[1]["ts"] < parsed.window[1]]
        if inside:
            scored = inside
        elif scored:
            fallback = True
    scored.sort(key=lambda s: (s[0], s[1]["ts"]), reverse=True)
    out, seen = [], set()
    for s in scored:                      # 같은 카메라·같은 위치 설명은 가장 좋은 것 하나만
        key = (s[1]["camera"], synonyms.norm(s[1]["where_text"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out, fallback


# ------------------------------------------------------------------ 표현

def fmt_time(ts, now):
    t, n = datetime.datetime.fromtimestamp(ts), datetime.datetime.fromtimestamp(now)
    d = (_midnight(n) - _midnight(t)).days
    hm = "%02d:%02d" % (t.hour, t.minute)
    if d == 0:
        return "오늘 " + hm
    if d == 1:
        return "어제 " + hm
    return "%d월 %d일 %s" % (t.month, t.day, hm)


def fmt_age(age_s):
    if age_s < 3600:
        return "%d분 전" % max(1, int(age_s // 60))
    if age_s < 86400:
        return "%d시간 전" % int(age_s // 3600)
    return "%d일 전" % int(age_s // 86400)


def _obj_josa(word):
    """을/를"""
    ch = word.strip()[-1:] if word.strip() else ""
    if "가" <= ch <= "힣":
        return "을" if (ord(ch) - 0xAC00) % 28 else "를"
    return "을"


def sighting_dict(row, now, qual=1.0, score_=0.0):
    cam = row["camera"]
    return {"name": row["name"], "where": row["where_text"], "confidence": row["conf"], "ts": row["ts"],
            "when": fmt_time(row["ts"], now), "age": fmt_age(now - row["ts"]), "camera": cam,
            "photo": row["path"], "frame_id": row["fid"], "match": qual, "score": round(score_, 4)}


def render(parsed, hits, fallback, now, stale_hours=24):
    t = parsed.target
    if not t:
        return "어떤 물건을 찾으시는지 모르겠어요. 예: \"리모컨 어디 뒀지?\""
    if not hits:
        return "%s%s 본 기록이 없어요." % (t, _obj_josa(t))
    lines = []
    for i, (sc, r, q) in enumerate(hits):
        cam = (r["camera"] + " 카메라") if r["camera"] else "카메라"
        where = (r["where_text"] + "에 있었어요") if r["where_text"] else "보였어요"
        low = " (확신이 낮아요)" if r["conf"] < 0.4 else ""
        photo = r["path"] if r["path"] else "사진은 보관 기간이 지나 삭제됐어요"
        head = "" if i == 0 else "그 전에는 "
        name = "" if q == 1.0 else "[%s] " % r["name"]
        lines.append("%s%s%s %s에서 %s%s\n  사진: %s" % (head, name, fmt_time(r["ts"], now), cam, where, low, photo))
    first = hits[0][1]
    age = now - first["ts"]
    pre = ""
    if parsed.window and fallback:
        pre = "%s에는 못 봤고, 가장 최근 기록이에요.\n" % parsed.window_label
    if age >= stale_hours * 3600:
        pre += "마지막으로 본 게 %s이에요 (오래된 기록이라 지금은 다를 수 있어요).\n" % fmt_age(age)
    return (pre + "\n".join(lines)).strip()


def facts_text(hits, now):
    return "\n".join("- %s: %s %s 카메라, %s (%s)" % (r["name"], fmt_time(r["ts"], now), r["camera"] or "-", r["where_text"] or "-", fmt_age(now - r["ts"]))
                     for _, r, _ in hits)


def answer(app, question, use_llm=None):
    now = app.clock()
    parsed = parse_query(question, now)
    hits, fallback = find(app.store, parsed, now)
    text = render(parsed, hits, fallback, now, app.cfg.get("stale_hours", 24))
    use_llm = app.cfg.get("llm_phrase") if use_llm is None else use_llm
    phrased = None
    if use_llm and hits:
        try:
            with app.vlm_lock:
                phrased = app.vlm.phrase(question, facts_text(hits, now))
        except VlmError:
            phrased = None
    return {"question": question, "target": parsed.target, "kind": parsed.kind, "window": parsed.window_label or None,
            "found": bool(hits), "answer": phrased or text, "details": text,
            "sightings": [sighting_dict(r, now, q, s) for s, r, q in hits]}
