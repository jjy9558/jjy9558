"""훅 문장 사전: 사용자 편집 가능한 JSON 템플릿 모음 + 사용 기록(과다 사용 경고). 결정적."""
import copy
import json
import os
import re
import sys

from . import promise

BOOK_NAME = "hooks.json"

# 일반적인 한국어 훅 문장 틀 (특정 크리에이터의 문구를 옮긴 것이 아님). {슬롯}이나 {슬롯:이/가} 형태.
# 슬롯: 인물, 사건, 숫자, 키워드
_DEFAULTS = [
    ("{인물:이/가} {사건}한 이유", "질문,궁금증"),
    ("{인물}에게 받은 {사건}, 그날 이후 모든 게 달라졌습니다", "반전,갈등"),
    ("{인물}에게 숨겨온 {숫자}의 비밀", "숫자,비밀"),
    ("\"{사건}\" - {인물}의 마지막 한마디", "인용"),
    ("{인물:은/는} 왜 {키워드}만은 끝까지 숨겼을까요?", "질문,궁금증"),
    ("{숫자}, 이게 {인물:이/가} 선택한 결말입니다", "숫자,결말"),
    ("{인물}과 크게 다툰 날, {키워드}에서 벌어진 일", "갈등"),
    ("아무도 {키워드}의 진짜 이유를 몰랐습니다", "궁금증"),
    ("{숫자:으로/로} 드러난 {인물}의 진짜 얼굴", "숫자,반전"),
    ("이걸 보고도 {인물:을/를} 믿을 수 있을까요?", "질문,갈등"),
    ("\"{키워드} 때문이었어.\" 그 한마디에 얼어붙었습니다", "인용,반전"),
    ("{인물:이/가} 거짓말한 순간, {숫자}의 믿음이 모두 무너졌다", "갈등,숫자"),
    ("{키워드}, 끝까지 보면 뜻밖의 반전이 있습니다", "반전,결말"),
    ("{숫자}만 기억하세요, {키워드}의 진실이 보입니다", "숫자"),
    ("나만 {키워드:이/가} 이상했던 걸까요?", "공감,질문"),
    ("{인물}에게 들은 {키워드} 이야기, 직접 판단해 보세요", "공감"),
    ("처음엔 웃었는데 {키워드:을/를} 알고 나서 말문이 막혔습니다", "반전,공감"),
    ("{숫자:을/를} 두고 {인물:은/는} 이미 알고 있었습니다", "숫자,비밀"),
    ("하지 말라던 {키워드}, 결국 해버렸습니다", "갈등"),
    ("{인물}에게 단 한 번도 말하지 못한 {키워드}", "공감,비밀"),
    ("{키워드}만 아니었다면 이런 일은 없었을 겁니다", "공감"),
    ("결말부터 말할게요, {인물:은/는} {키워드:을/를} 택했습니다", "결말"),
    ("{숫자}가 말해주는 {키워드}의 불편한 진실", "숫자"),
    ("{인물:이/가} 갑자기 연락을 끊은 진짜 이유", "질문,궁금증"),
    ("\"{사건}\" 이 말을 듣는 순간 숨이 멎었습니다", "인용"),
    ("이게 정상인가요? {인물}의 {키워드}", "질문"),
    ("{숫자:으로/로} 뒤집힌 {인물}과의 관계", "숫자,반전"),
    ("{키워드} 앞에서 {인물:이/가} 보인 뜻밖의 반응", "반전"),
    ("모두가 {키워드}라고 믿었지만 사실은 달랐습니다", "반전"),
    ("당신이라면 {인물:을/를} 용서할 수 있나요?", "질문,공감"),
]

PERSONS = ["시어머니", "시아버지", "며느리", "사위", "장모", "남편", "아내", "엄마", "아빠", "어머니",
           "아버지", "아들", "딸", "형", "누나", "언니", "오빠", "동생", "친구", "선배", "후배", "상사",
           "사장", "팀장", "동료", "이웃", "할머니", "할아버지", "선생님", "손님", "직원", "전남친",
           "전여친", "남친", "여친"]

SLOT_RE = re.compile(r"\{([^{}:]+)(?::([^{}/]*)/([^{}]*))?\}")
NUM_RE = re.compile(r"\d+\s?(?:가지|번|명|년|개|시간|분|초|일|달|원|만원|배|살|세)?")


def default_templates():
    return [{"id": "t%02d" % (i + 1), "text": t, "tags": tg.split(",")}
            for i, (t, tg) in enumerate(_DEFAULTS)]


def data_dir():
    d = os.environ.get("SHORTSLINT_DATA_DIR")
    if d:
        return d
    if sys.platform.startswith("win") and os.environ.get("APPDATA"):
        return os.path.join(os.environ["APPDATA"], "shortslint")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "shortslint")


def default_path():
    return os.path.join(data_dir(), BOOK_NAME)


def load(path=None):
    """파일이 없으면 기본 30개를 담은 사전을 돌려준다 (저장 전까지 파일은 만들지 않음)."""
    path = path or default_path()
    if not os.path.isfile(path):
        return {"version": 1, "templates": default_templates(), "usage": []}
    with open(path, encoding="utf-8-sig") as f:
        try:
            book = json.load(f)
        except ValueError as e:
            raise ValueError("훅 사전 JSON 오류 (%s): %s" % (path, e))
    if not isinstance(book, dict) or not isinstance(book.get("templates"), list):
        raise ValueError("훅 사전 형식 오류: templates 목록이 필요합니다 (%s)" % path)
    book.setdefault("version", 1)
    book.setdefault("usage", [])
    return book


def save(book, path=None):
    path = path or default_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------------------------------------------------------------- CRUD
def parse_tags(s):
    if isinstance(s, (list, tuple)):
        return [str(t).strip() for t in s if str(t).strip()]
    return [t.strip() for t in re.split(r"[,\s]+", s or "") if t.strip()]


def find(book, tid):
    for t in book["templates"]:
        if t["id"] == tid:
            return t
    return None


def next_id(book):
    nums = [int(m.group(1)) for t in book["templates"] for m in [re.fullmatch(r"t(\d+)", t["id"])] if m]
    return "t%02d" % (max(nums) + 1 if nums else 1)


def add(book, text, tags=None, tid=None):
    text = (text or "").strip()
    if not text:
        raise ValueError("템플릿 문장이 비어 있습니다 (--text)")
    if any(t["text"] == text for t in book["templates"]):
        raise ValueError("이미 같은 템플릿이 있습니다")
    tid = tid or next_id(book)
    if find(book, tid):
        raise ValueError("이미 있는 id입니다: %s" % tid)
    book["templates"].append({"id": tid, "text": text, "tags": parse_tags(tags)})
    return tid


def edit(book, tid, text=None, tags=None):
    t = find(book, tid)
    if not t:
        raise ValueError("없는 id입니다: %s" % tid)
    if text is not None and text.strip():
        t["text"] = text.strip()
    if tags is not None:
        t["tags"] = parse_tags(tags)
    return t


def remove(book, tid):
    t = find(book, tid)
    if not t:
        raise ValueError("없는 id입니다: %s" % tid)
    book["templates"].remove(t)
    book["usage"] = [u for u in book["usage"] if u.get("id") != tid]


def list_templates(book, tag=None):
    return [t for t in book["templates"] if not tag or tag in t.get("tags", [])]


def format_list(rows):
    if not rows:
        return "해당하는 템플릿이 없습니다"
    return "\n".join("%s  %s  [%s]" % (t["id"], t["text"], ",".join(t.get("tags", []))) for t in rows)


# ---------------------------------------------------------------- 사용 기록 / 과다 사용
def use(book, tid, video=None):
    """사용 기록 추가. 같은 영상에 같은 템플릿은 한 번만 센다. 추가되면 True."""
    if not find(book, tid):
        raise ValueError("없는 id입니다: %s" % tid)
    video = (video or "").strip()
    if video and any(u.get("id") == tid and u.get("video") == video for u in book["usage"]):
        return False
    book["usage"].append({"id": tid, "video": video})
    return True


def overused(book, window=5, max_uses=1):
    """최근 window건 사용 기록에서 max_uses회를 넘긴 템플릿 -> {id: 횟수}."""
    recent = book["usage"][-window:] if window > 0 else []
    counts = {}
    for u in recent:
        counts[u.get("id")] = counts.get(u.get("id"), 0) + 1
    return {k: v for k, v in sorted(counts.items()) if v > max_uses}


def format_overuse(over, book, window):
    lines = []
    for tid, n in over.items():
        t = find(book, tid)
        lines.append("⚠️ %s 템플릿을 최근 %d편 중 %d번 사용: %s (비슷한 도입 반복은 재활용 콘텐츠처럼 보일 수 있어요)"
                     % (tid, window, n, t["text"] if t else "(삭제됨)"))
    return lines


# ---------------------------------------------------------------- 채우기
def josa(word, pair):
    """pair 예: '이/가', '은/는', '을/를', '으로/로'. 받침 유무로 선택."""
    a, b = pair.split("/", 1)
    ch = word.rstrip()[-1:] if word.strip() else ""
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
    elif ch.isdigit():
        jong = 1 if ch in "013678" else 0
    else:
        jong = 0
    if a.startswith("으"):          # 으로/로: ㄹ 받침은 '로'
        return a if jong not in (0, 8) else b
    return a if jong else b


def extract_slots(meta):
    title = re.sub(r"#\S+", " ", str(meta.get("title") or "")).strip()
    claims = [str(c).strip() for c in (meta.get("claims") or []) if str(c).strip()]
    blob = " ".join([title] + claims)
    slots = {}
    best = None
    for p in PERSONS:
        i = blob.find(p)
        if i >= 0 and (best is None or (i, -len(p)) < best[0]):
            best = ((i, -len(p)), p)
    if best:
        slots["인물"] = best[1]
    m = NUM_RE.search(blob)
    if m:
        slots["숫자"] = m.group(0).strip()
    persons = set(PERSONS)
    kws = [k for k in promise.keywords(title) if k not in persons and not NUM_RE.fullmatch(k)]
    if kws:
        slots["키워드"] = kws[0]
    if kws:
        slots["사건"] = kws[-1]
    elif claims:
        slots["사건"] = claims[0]
    return slots


def slots_of(text):
    return [m.group(1) for m in SLOT_RE.finditer(text)]


def fill(text, slots):
    """모든 슬롯 값이 있으면 채운 문장, 아니면 None."""
    if any(s not in slots for s in slots_of(text)):
        return None

    def rep(m):
        v = slots[m.group(1)]
        if m.group(2) is not None:
            return v + josa(v, "%s/%s" % (m.group(2), m.group(3)))
        return v
    return SLOT_RE.sub(rep, text)


def suggest(book, meta, n=3, window=5, max_uses=1):
    """-> [{id, text, filled, overused}]. 과다 사용 템플릿은 뒤로 미루고 나머지는 사전 순서."""
    slots = extract_slots(meta)
    over = overused(book, window, max_uses)
    rows = []
    for t in book["templates"]:
        f = fill(t["text"], slots)
        if f is not None:
            rows.append({"id": t["id"], "text": t["text"], "filled": f, "overused": t["id"] in over})
    rows.sort(key=lambda r: r["overused"])   # 안정 정렬 -> 결정적
    return rows[:n]


def format_suggestions(rows):
    if not rows:
        return "채울 수 있는 템플릿이 없습니다 (title/claims에 인물·숫자·키워드가 필요)"
    return "\n".join("%d. %s  (%s)%s" % (i, r["filled"], r["id"], "  ⚠️ 최근 자주 사용" if r["overused"] else "")
                     for i, r in enumerate(rows, 1))


def fallback_suggestions(meta, n=3):
    """슬롯을 못 채울 때의 일반 문장 (제목 기반 템플릿 없음)."""
    title = re.sub(r"#\S+", " ", str(meta.get("title") or "")).strip()
    base = [
        "결말부터 말할게요. 끝까지 보면 이유를 알게 됩니다",
        "이 이야기, 듣고 나면 생각이 달라질 겁니다",
        "\"그 말\" 한마디가 모든 걸 바꿨습니다",
    ]
    if title:
        base.insert(0, "%s - 결론부터 말씀드릴게요" % title)
    return base[:n]
