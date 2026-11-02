"""제목 후보 규칙 기반 점수 (LLM 없음, 결정적). 만점 100 = 길이 40 + 약속 일치 40 + 과장 없음 20."""
import os

from . import promise, similar
from .metadata import find_words


def load_transcript(meta, meta_path, explicit=None):
    base = os.path.dirname(os.path.abspath(meta_path))
    cands = []
    if explicit:
        cands.append(explicit)
    tp = meta.get("transcript")
    if tp:
        cands.append(tp if os.path.isabs(tp) else os.path.join(base, tp))
    name = os.path.basename(meta_path)
    stem = name[:-len(".meta.json")] if name.endswith(".meta.json") else os.path.splitext(name)[0]
    for e in (".txt", ".srt", ".ass", ".ssa"):
        cands.append(os.path.join(base, stem + e))
    for c in cands:
        if os.path.isfile(c):
            return similar.read_transcript(c).strip()
    return ""


def score_title(title, transcript, cfg, claims=None):
    t = cfg["titles"]
    m = cfg["metadata"]
    n = len(title.strip())
    notes = []
    # 길이 (40)
    if n > m["title_max_chars"]:
        length = 0
        notes.append("%d자: 유튜브 한도 초과" % n)
    elif n > m["title_visible_chars"]:
        length = max(0.0, 40 - 2.0 * (n - m["title_visible_chars"]))
        notes.append("%d자: 피드에서 잘림" % n)
    elif n < t["hard_min_chars"]:
        length = 10
        notes.append("%d자: 너무 짧음" % n)
    elif n < t["ideal_min_chars"]:
        length = 10 + 30.0 * (n - t["hard_min_chars"]) / (t["ideal_min_chars"] - t["hard_min_chars"])
        notes.append("%d자: 다소 짧음" % n)
    else:
        length = 40
    # 약속 일치 (40)
    if transcript:
        s = promise.score_promise(title, None, claims, transcript, cfg)
        base = 40 * s["score"] if s["score"] is not None else 20
        if s["score"] is not None and s["missing"]:
            notes.append("대본에 없음: " + ", ".join(s["missing"][:4]))
        pen = 10 * (len(s["numbers_missing"]) + len(s["words_missing"]) + len(s["names_missing"]))
        if s["numbers_missing"]:
            notes.append("숫자 %s 대본에 없음" % ", ".join(str(x) for x in s["numbers_missing"]))
        if s["words_missing"]:
            notes.append("'%s' 약속했으나 대본에 없음" % "/".join(s["words_missing"]))
        if s["names_missing"]:
            notes.append("이름 '%s' 대본에 없음" % "', '".join(s["names_missing"]))
        match = max(0.0, base - pen)
    else:
        match = 20
        notes.append("대본 없음: 약속 일치 중립 처리")
    # 과장 금칙어 (20)
    banned = find_words(title, m["banned_words"])
    hyper = find_words(title, list(m["clickbait_words"]) + list(t["exaggeration_words"]))
    hyper = [h for h in hyper if h not in banned]
    if banned:
        ex = 0
        notes.append("금지 단어: " + ", ".join(banned))
    else:
        ex = max(0, 20 - 10 * len(hyper))
    if hyper:
        notes.append("과장 표현: " + ", ".join(hyper))
    total = int(round(length + match + ex))
    return {"title": title, "score": total, "length": n, "parts": {"길이": int(round(length)),
            "약속": int(round(match)), "과장": ex}, "notes": notes}


def rank(meta, transcript, cfg):
    cands = [str(c).strip() for c in (meta.get("title_candidates") or []) if str(c).strip()]
    if not cands and (meta.get("title") or "").strip():
        cands = [meta["title"].strip()]
    rows = [score_title(c, transcript, cfg, meta.get("claims")) for c in cands]
    order = sorted(range(len(rows)), key=lambda i: (-rows[i]["score"], i))
    return [rows[i] for i in order]


def format_rank(rows):
    if not rows:
        return "제목 후보가 없습니다 (meta.json의 title_candidates)"
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append("%d. [%d점] %s  (길이 %d/40, 약속 %d/40, 과장 %d/20)"
                     % (i, r["score"], r["title"], r["parts"]["길이"], r["parts"]["약속"], r["parts"]["과장"]))
        for n in r["notes"]:
            lines.append("     - " + n)
    return "\n".join(lines)
