"""결정 장애 종결 의회. 다른 프로젝트의 데이터를 읽기 전용으로 참고해 의원들이 한마디씩 하고 표결한다.

읽는 곳(전부 선택, 없거나 깨졌으면 조용히 건너뜀 - 어떤 파일도 수정하지 않는다):
  cool_off      data.json       wishes[{name,price,status,cool_until}], purchases[{name,price,satisfaction,usage}]
  steam_buddy   data.json       games{appid:{name,price_paid,minutes,refunded}}
  life_nagger   폴더            status.json{mombot:{state,stage}}, work.json{days:{YYYY-MM-DD:{game(초)}}}
  voice_journal journal.db      goals / entries (sqlite, mode=ro)
  paper_butler  paperbutler.db  payments(ts, amount, kind, status)
"""
import json
import os
import re
import shlex
import sqlite3
import urllib.request
import zlib
from pathlib import Path

from .decide import _chat_url
from .vjournal_import import open_readonly

SOURCE_KEYS = ("cool_off", "steam_buddy", "life_nagger", "voice_journal", "paper_butler")
YES, NO, ABSTAIN = "찬성", "반대", "기권"

_GAME_RE = re.compile(r"게임|스팀|steam|dlc|가챠|갓챠|패키지|얼리\s*액세스", re.I)
_BUY_RE = re.compile(r"살까|살래|사야|사도|사볼까|살지|사버|사버릴|구매|구입|지를까|지르|질러|결제|담을까|살만|사자|사줄까")
_SAVING_RE = re.compile(r"안\s*산다|안\s*사|사지\s*않|사지\s*말|지르지|무지출|충동\s*구매|절약|아끼|지출\s*(?:줄|감)|구매\s*(?:금지|끊|안)")


def default_paths(env=None):
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or Path.home())
    return {
        "cool_off": Path(env.get("COOLOFF_HOME") or home / ".cool-off") / "data.json",
        "steam_buddy": Path(env.get("STEAM_BUDDY_HOME") or home / ".steam-buddy") / "data.json",
        "life_nagger": home / ".life-nagger",
        "voice_journal": Path(env.get("VJOURNAL_DB") or home / ".vjournal" / "journal.db"),
        "paper_butler": Path(env.get("PAPERBUTLER_HOME") or home / ".paperbutler") / "paperbutler.db",
    }


def resolve_paths(config_sources=None, overrides=None, env=None):
    """우선순위: 명령줄 > config.sources > 환경변수/기본 위치."""
    paths = default_paths(env)
    for src in (config_sources or {}, overrides or {}):
        for k in SOURCE_KEYS:
            if src.get(k):
                paths[k] = Path(os.path.expanduser(str(src[k])))
    return paths


# ------------------------------------------------------------- 읽기 (전부 실패 시 None)

def _json(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d if isinstance(d, dict) else None


def read_cool_off(path):
    d = _json(path)
    if d is None:
        return None
    wishes = d.get("wishes") or []
    purchases = d.get("purchases") or []
    skipped = [w for w in wishes if w.get("status") == "skipped"]
    regrets = [p for p in purchases if (p.get("satisfaction") is not None and p["satisfaction"] <= 2)
               or p.get("usage") == "거의 안 씀"]
    return {"saved_total": sum(int(w.get("price", 0)) for w in skipped), "saved_count": len(skipped),
            "regrets": [p.get("name", "?") for p in regrets],
            "cooling": [{"name": w.get("name", "?"), "until": w.get("cool_until", "")}
                        for w in wishes if w.get("status") == "cooling"]}


def read_steam(path):
    d = _json(path)
    games = [g for g in (d or {}).get("games", {}).values() if not g.get("refunded")]
    if not games:
        return None
    unplayed = [g.get("name", "?") for g in games if g.get("minutes", 0) / 60.0 < 0.1]
    hours = sum(g.get("minutes", 0) for g in games) / 60.0
    total = sum(g.get("price_paid", 0) for g in games)
    return {"unplayed": unplayed, "per_hour": (total / hours) if hours >= 0.1 else None,
            "total": total, "hours": hours}


def read_nagger(folder, today):
    folder = Path(folder)
    res = {"stage": 0, "game_hours": None}
    found = False
    try:
        st = _json(folder / "status.json")
        mb = (st or {}).get("mombot") or {}
        res["stage"] = int(mb.get("stage") or 0) if mb.get("state") == "warning" else 0
        found = True
    except (OSError, ValueError, TypeError):
        pass
    try:
        wk = _json(folder / "work.json")
        month = today.strftime("%Y-%m")
        secs = sum(float(v.get("game", 0)) for k, v in (wk or {}).get("days", {}).items()
                   if k.startswith(month))
        res["game_hours"] = secs / 3600.0
        found = True
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return res if found else None


def read_journal(path, today):
    """이번 달 일기/목표 중 '안 산다'류 선언. DB 가 있으면 빈 리스트라도 반환."""
    if not Path(path).is_file():
        return None
    conn = open_readonly(path)
    try:
        month = today.strftime("%Y-%m")
        texts = []
        for r in conn.execute("SELECT COALESCE(raw,title) FROM goals WHERE status='open'"):
            if r[0] and _SAVING_RE.search(r[0]):
                texts.append(r[0].strip())
        for r in conn.execute("SELECT text FROM entries WHERE day LIKE ? ORDER BY ts", (month + "%",)):
            for sent in re.split(r"(?<=[.!?。\n])\s*", r[0] or ""):
                if _SAVING_RE.search(sent):
                    texts.append(sent.strip())
        return {"declarations": list(dict.fromkeys(texts))}
    finally:
        conn.close()


def read_paper(path, today):
    if not Path(path).is_file():
        return None
    conn = open_readonly(path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0), COUNT(*) FROM payments WHERE kind='approve' "
            "AND status='active' AND substr(ts,1,7)=?", (today.strftime("%Y-%m"),)).fetchone()
        return {"month_total": int(row[0]), "count": row[1]}
    finally:
        conn.close()


def collect(paths, today):
    readers = {
        "cool_off": lambda p: read_cool_off(p),
        "steam_buddy": lambda p: read_steam(p),
        "life_nagger": lambda p: read_nagger(p, today),
        "voice_journal": lambda p: read_journal(p, today),
        "paper_butler": lambda p: read_paper(p, today),
    }
    ev = {}
    for k, fn in readers.items():
        try:
            ev[k] = fn(paths[k])
        except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
            ev[k] = None  # 조용히 건너뜀
    return ev


# ------------------------------------------------------------------- 의원

def won(n):
    return "{:,}원".format(int(round(n)))


def _names(items, n=3):
    s = ", ".join(items[:n])
    return s + (" 외 %d개" % (len(items) - n) if len(items) > n else "")


def _norm(s):
    return re.sub(r"\s+", "", s).casefold()


def question_flags(q):
    return {"game": bool(_GAME_RE.search(q)), "buy": bool(_BUY_RE.search(q))}


def member_cool_off(ev, q, flags, cfg):
    cooling, regrets = ev["cooling"], ev["regrets"]
    nq = _norm(q)
    hit = [c for c in cooling if _norm(c["name"]) and _norm(c["name"]) in nq]
    if hit:
        return NO, "'%s'는 지금 쿨링오프 중이에요. 아직 식지도 않은 걸 먹으려 들다니!" % hit[0]["name"]
    if cooling:
        return NO, "쿨링오프 대기줄에 %d개(%s)가 서 있어요. 새치기 금지입니다." % (len(cooling), _names([c["name"] for c in cooling]))
    if regrets:
        return NO, "후회 목록 %d개(%s). 같은 길을 또 걷겠다고요?" % (len(regrets), _names(regrets))
    if ev["saved_total"] > 0:
        return YES, "후회 목록 0개, 지금까지 %d번 참아 %s을 아꼈어요. 이 정도 인내심이면 한 번쯤 허락합니다." % (
            ev["saved_count"], won(ev["saved_total"]))
    return YES, "후회 목록도 대기줄도 깨끗해요. 전과 없음, 통과!"


def member_steam(ev, q, flags, cfg):
    if not flags["game"]:
        return ABSTAIN, "게임 얘기가 아니라서 저는 팝콘이나 먹겠습니다."
    n = len(ev["unplayed"])
    ph = ev["per_hour"]
    cost = " 시간당 비용은 %s." % won(ph) if ph is not None else ""
    if n >= 3:
        return NO, "아직 안 켜본 게임이 %d개(%s)나 있어요. 라이브러리가 아니라 무덤입니다.%s" % (n, _names(ev["unplayed"]), cost)
    if n == 0:
        return YES, "안 켜본 게임 0개! 성실한 게이머군요.%s 사세요, 사." % cost
    return ABSTAIN, "안 켜본 게임이 %d개(%s)예요.%s 판단 보류합니다." % (n, _names(ev["unplayed"]), cost)


def member_nagger(ev, q, flags, cfg):
    if not flags["game"]:
        return ABSTAIN, "게임 얘기가 아니면 저는 엄마봇 모드를 끕니다."
    limit = cfg.get("game_hours_limit", 40)
    h = ev["game_hours"]
    if ev["stage"] > 0:
        return NO, "지금도 새벽 게임으로 엄마봇 경고 %d단계예요. 지금 몇 시니?" % ev["stage"]
    if h is not None and h >= limit:
        return NO, "이번 달 게임을 벌써 %.0f시간 했어요. 엉덩이가 항의 시위 중입니다." % h
    if h is not None and h < limit / 4.0:
        return YES, "이번 달 게임 %.0f시간뿐이에요. 엄마봇도 흐뭇해합니다." % h
    return ABSTAIN, "새벽 경고는 없지만 이번 달 게임 %s시간... 의견 없음." % ("%.0f" % h if h is not None else "?")


def member_journal(ev, q, flags, cfg):
    decl = ev["declarations"]
    if decl:
        return NO, "이번 달 이런 말씀을 하셨죠: \"%s\" 과거의 본인과 싸우실 건가요?" % decl[0]
    return ABSTAIN, "이번 달 '안 산다' 류 선언은 없어요. 말한 적 없으니 채점도 없습니다."


def member_paper(ev, q, flags, cfg):
    budget = cfg.get("monthly_budget", 500000)
    t = ev["month_total"]
    if t > budget:
        return NO, "이번 달 벌써 %s 쓰셨어요(기준 %s). 통장이 울고 있습니다." % (won(t), won(budget))
    if t < budget / 2.0:
        return YES, "이번 달 지출 %s, 기준의 절반도 안 썼어요. 통장이 여유로워요." % won(t)
    return ABSTAIN, "이번 달 지출 %s. 아슬아슬해서 저는 손을 들지 않겠습니다." % won(t)


MEMBERS = [
    ("cool_off", "쿨링오프 의원", member_cool_off),
    ("steam_buddy", "스팀 의원", member_steam),
    ("life_nagger", "엄마봇 의원", member_nagger),
    ("voice_journal", "일기 의원", member_journal),
    ("paper_butler", "가계부 의원", member_paper),
]


def chair_vote(question):
    """동수일 때 의장의 결정: 질문 문자열의 crc32 홀짝 (같은 질문엔 항상 같은 판결)."""
    return YES if zlib.crc32(question.strip().encode("utf-8")) % 2 == 0 else NO


def convene(question, evidence, config=None):
    question = question.strip()
    cfg = config or {}
    flags = question_flags(question)
    members = []
    for key, name, fn in MEMBERS:
        ev = evidence.get(key)
        if ev is None:
            continue
        stance, line = fn(ev, question, flags, cfg)
        members.append({"key": key, "name": name, "stance": stance, "line": line})
    yes = sum(m["stance"] == YES for m in members)
    no = sum(m["stance"] == NO for m in members)
    abstain = len(members) - yes - no
    tie = yes == no
    if tie:
        cv = chair_vote(question)
        if not members:
            line = "참고할 자료가 하나도 없네요. 의장이 혼자 정합니다. 책임도 혼자 집니다."
        else:
            line = "동수입니다! 의장 직권으로 캐스팅보트를 행사합니다. 근거는... 기분입니다."
        chair = {"name": "의장", "stance": cv, "line": line}
        yes += cv == YES
        no += cv == NO
    else:
        chair = {"name": "의장", "stance": ABSTAIN, "line": "표결이 났으니 저는 구경만 하겠습니다. 정숙!"}
    verdict = "가결" if yes > no else "부결"
    return {"question": question, "members": members, "chair": chair, "yes": yes, "no": no,
            "abstain": abstain, "tie_broken": tie, "verdict": verdict, "flags": flags,
            "suggestion": suggest_cool_off(question) if verdict == "부결" and flags["buy"] else None}


# ---------------------------------------------------------------- 제안/출력

_TAIL_RE = re.compile(r"\s*(?:을|를)?\s*(?:살까|살래|사야\s*(?:할까|하나|돼|될까)?|사도\s*(?:될까|돼)?|사볼까|살지|사버릴까|"
                      r"구매할까|구입할까|지를까|질러\s*(?:볼까|말까)?|결제할까|살만할까|사자|사줄까).*$")


def item_from_question(q):
    s = re.sub(r"[?？!！.\s]+$", "", q.strip())
    s = _TAIL_RE.sub("", s).strip()
    s = re.sub(r"^(?:이|그|저)\s+(?=\S)", "", s)
    s = re.sub(r"\s+(?:말고|그냥|진짜|정말)$", "", s).strip()
    return s or q.strip()


def suggest_cool_off(question):
    """실행하지 않고 문자열만 만든다."""
    return "python -m cool_off want %s --price <가격> --why %s" % (
        shlex.quote(item_from_question(question)), shlex.quote("결정 의회 부결"))


def render(result):
    out = ["🏛️  결정 의회 개회! 안건: \"%s\"" % result["question"]]
    if not result["members"]:
        out.append("(출석한 의원이 없어요. 참고할 데이터를 못 찾았습니다.)")
    for m in result["members"]:
        out.append("  [%s] %s (%s)" % (m["name"], m["line"], m["stance"]))
    out.append("  [의장] %s%s" % (result["chair"]["line"],
                                 " → %s 투표" % result["chair"]["stance"] if result["tie_broken"] else ""))
    icon = "🎉" if result["verdict"] == "가결" else "🔨"
    out.append("%s 땅땅땅! 안건은 %s! (찬성 %d : 반대 %d, 기권 %d%s)" % (
        icon, result["verdict"], result["yes"], result["no"], result["abstain"],
        ", 의장 캐스팅보트" if result["tie_broken"] else ""))
    if result["suggestion"]:
        out.append("💡 일단 참았다가 식혀 보세요. 아래 명령을 (직접) 실행하면 쿨링오프에 담겨요:")
        out.append("   " + result["suggestion"])
    return out


# ------------------------------------------------------------ LLM 말투 (선택)

def phrase_with_llm(result, llm_config, opener=None):
    """의원 발언을 로컬 LLM 으로 더 웃기게 다듬는다. 실패하면 원문 그대로 (result 를 바꾸지 않는다)."""
    speakers = result["members"] + ([result["chair"]] if result["tie_broken"] else [])
    if not speakers:
        return False
    opener = opener or urllib.request.urlopen
    lines = ["%d. [%s/%s] %s" % (i + 1, s["name"], s["stance"], s["line"]) for i, s in enumerate(speakers)]
    body = {
        "model": llm_config.get("model", "local-model"),
        "messages": [
            {"role": "system", "content": "당신은 웃기고 능청스러운 한국어 의회 속기사입니다. 사실(숫자, 이름)과 입장은 바꾸지 말고 말투만 다듬습니다."},
            {"role": "user", "content": "아래 발언을 각각 한 문장으로 더 웃기게 고쳐 주세요. "
             "JSON 문자열 배열(%d개)로만 답하세요.\n안건: %s\n%s" % (len(speakers), result["question"], "\n".join(lines))},
        ],
        "temperature": 0.8,
    }
    headers = {"Content-Type": "application/json"}
    if llm_config.get("api_key"):
        headers["Authorization"] = "Bearer " + llm_config["api_key"]
    try:
        req = urllib.request.Request(_chat_url(llm_config.get("endpoint", "")),
                                     data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        resp = opener(req, timeout=llm_config.get("timeout", 8))
        try:
            payload = json.loads(resp.read().decode("utf-8"))
        finally:
            close = getattr(resp, "close", None)
            if close:
                close()
        content = payload["choices"][0]["message"]["content"]
        m = re.search(r"\[.*\]", content, re.S)
        arr = json.loads(m.group(0))
        if (not isinstance(arr, list) or len(arr) != len(speakers)
                or not all(isinstance(x, str) and x.strip() for x in arr)):
            return False
    except Exception:
        return False
    for s, text in zip(speakers, arr):
        s["line"] = text.strip()
    return True
