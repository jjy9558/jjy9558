"""상태 -> 압축 컨텍스트. 원시 채팅 로그 대신 DB에서 조립한다."""
import re

_TOK = re.compile(r"[0-9A-Za-z가-힣]+")


def bigrams(text):
    """한국어 조사 변화에 강하도록 단어별 문자 bigram 집합 (1글자 단어는 그대로)."""
    out = set()
    for w in _TOK.findall(text.lower()):
        if len(w) == 1:
            out.add(w)
        else:
            out.update(w[i:i + 2] for i in range(len(w) - 1))
    return out


def retrieve_facts(facts, query, k=5):
    """키워드(bigram) 겹침 점수 상위 k개. 동점이면 최근 사실 우선. 겹침 0은 제외."""
    q = bigrams(query)
    scored = []
    for f in facts:
        score = len(q & bigrams(f["text"]))
        if score > 0:
            scored.append((score, f["id"], f))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [f for _, _, f in scored[:k]]


def present_npcs(world, user_text=""):
    here = world.npcs(world.location)
    names = {n["name"] for n in here}
    for n in world.npcs():
        if n["name"] not in names and n["name"] in user_text:
            here.append(n)
    return here


def build_context(world, user_text="", max_facts=5):
    lines = []
    lines.append("## 세계관: %s" % world.get("name"))
    for label, key in (("배경", "setting"), ("분위기", "tone"), ("규칙", "rules")):
        v = world.get(key)
        if v:
            lines.append("- %s: %s" % (label, v))
    loc = world.location
    ldesc = next((l["description"] for l in world.locations() if l["name"] == loc), "")
    lines.append("\n## 현재 상태")
    lines.append("- 플레이어: %s / HP %d/%d / 턴 %d" % (world.get("player_name"), world.hp, world.max_hp, world.turn))
    lines.append("- 현재 위치: %s%s" % (loc, " - " + ldesc if ldesc else ""))
    links = next((l["links"] for l in world.locations() if l["name"] == loc), [])
    if links:
        lines.append("- 연결된 장소: " + ", ".join(links))
    inv = world.items()
    lines.append("- 소지품: " + (", ".join("%s x%d" % (i["name"], i["qty"]) for i in inv) or "없음"))
    npcs = present_npcs(world, user_text)
    if npcs:
        lines.append("\n## 이 장면의 NPC")
        for n in npcs:
            lines.append("- %s (특징: %s / 플레이어와의 관계: %s / 마지막 목격: 턴 %d)" % (
                n["name"], n["traits"] or "-", n["relation"] or "-", n["last_seen"]))
    qs = world.quests("open")
    if qs:
        lines.append("\n## 진행 중 퀘스트")
        for q in qs:
            lines.append("- %s%s" % (q["title"], ": " + q["detail"] if q["detail"] else ""))
    query = " ".join([user_text, loc] + [n["name"] for n in npcs])
    rel = retrieve_facts(world.facts(), query, max_facts)
    if rel:
        lines.append("\n## 관련 기록")
        lines += ["- %s" % f["text"] for f in rel]
    s = world.latest_summary()
    if s:
        lines.append("\n## 지금까지의 요약\n" + s["text"])
    return "\n".join(lines)
