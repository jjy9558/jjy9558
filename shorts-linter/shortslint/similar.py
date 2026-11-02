"""과거 대본과의 유사도(자카드 n-gram) - 재활용 콘텐츠 위험 점검. 결정적."""
import os
import re

from . import subtitles

CAT = "재활용"
TEXT_EXT = (".txt", ".srt", ".ass", ".ssa")


def normalize(text):
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def char_grams(text, n=3):
    t = normalize(text)
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def word_grams(text, n=2):
    ws = re.findall(r"[0-9a-z가-힣]+", (text or "").lower())
    if len(ws) < n:
        return {" ".join(ws)} if ws else set()
    return {" ".join(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def similarity(t1, t2, char_n=3, word_n=2, use_words=False):
    s = jaccard(char_grams(t1, char_n), char_grams(t2, char_n))
    if use_words:
        s = (s + jaccard(word_grams(t1, word_n), word_grams(t2, word_n))) / 2.0
    return s


def read_transcript(path):
    if path.lower().endswith((".srt", ".ass", ".ssa")):
        return subtitles.cues_text(subtitles.load_cues(path))
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def load_dir(folder):
    """-> {이름(확장자 제외): 대본}. 같은 이름이면 .txt를 우선하고, 없으면 자막."""
    found = {}
    for n in sorted(os.listdir(folder)):
        p = os.path.join(folder, n)
        stem, ext = os.path.splitext(n)
        if ext.lower() not in TEXT_EXT or not os.path.isfile(p):
            continue
        if stem.endswith(".meta"):
            continue
        if stem in found and not (ext.lower() == ".txt"):
            continue
        txt = read_transcript(p)
        if txt.strip():
            found[stem] = txt
    return found


def nearest(name, text, corpus, cfg, use_words=False, skip_self=True):
    sc = cfg["similar"]
    rows = []
    for other, t in corpus.items():
        if skip_self and other == name:
            continue
        rows.append((other, similarity(text, t, sc["char_n"], sc["word_n"], use_words)))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:sc["top"]]


def analyze(folder, cfg, history=None, use_words=False, threshold=None):
    """-> [{name, nearest:[(name,score)], risk:bool}] (이름순)."""
    corpus = load_dir(folder)
    hist = load_dir(history) if history else {}
    th = cfg["similar"]["threshold"] if threshold is None else threshold
    out = []
    for name in sorted(corpus):
        pool = dict(hist)
        for k, v in corpus.items():
            if k != name:
                pool.setdefault(k, v)
        near = nearest(name, corpus[name], pool, cfg, use_words, skip_self=False)
        risk = bool(near) and near[0][1] >= th
        out.append({"name": name, "nearest": near, "risk": risk})
    return out, th


def format_rows(rows, th):
    lines = []
    for r in rows:
        if not r["nearest"]:
            lines.append("⏭️ %s: 비교할 다른 대본 없음" % r["name"])
            continue
        top = r["nearest"][0]
        if r["risk"]:
            lines.append("⚠️ %s: 재활용 콘텐츠 위험 - '%s'와(과) %.0f%% 유사 (임계 %.0f%%)"
                         % (r["name"], top[0], 100 * top[1], 100 * th))
        else:
            lines.append("✅ %s: 가장 닮은 편 '%s' %.0f%%" % (r["name"], top[0], 100 * top[1]))
        for i, (n, s) in enumerate(r["nearest"], 1):
            lines.append("    %d. %s  %.1f%%" % (i, n, 100 * s))
    return "\n".join(lines)
