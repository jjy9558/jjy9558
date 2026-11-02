# -*- coding: utf-8 -*-
"""한글 문자 bigram 기반 BM25 검색 + '왜 접었지?' 이유 문장 추출."""
import math
import re
import unicodedata
from collections import Counter

from .parse import split_sentences

_WORD = re.compile(r"[가-힣]+|[a-z0-9]+")


def _norm(text):
    return unicodedata.normalize("NFC", text or "").lower()


def tokenize(text):
    t = _norm(text)
    toks = []
    for m in _WORD.finditer(t):
        w = m.group()
        if "가" <= w[0] <= "힣":
            if len(w) == 1:
                before = t[m.start() - 1] if m.start() > 0 else " "
                after = t[m.end()] if m.end() < len(t) else " "
                if before.isalnum() or after.isalnum():
                    continue   # 영단어 뒤에 붙은 조사 한 글자는 잡음
                toks.append(w)
            else:
                toks.extend(w[i:i + 2] for i in range(len(w) - 1))
        else:
            toks.append(w)
    return toks


def _squash(text):
    return re.sub(r"\s+", "", _norm(text))


class Index(object):
    def __init__(self, docs, k1=1.5, b=0.75):
        """docs: [(doc_id, text)]"""
        self.k1, self.b = k1, b
        self.ids, self.tfs, self.lens, self.raw = [], [], [], []
        df = Counter()
        for did, text in docs:
            toks = tokenize(text)
            tf = Counter(toks)
            self.ids.append(did)
            self.tfs.append(tf)
            self.lens.append(len(toks))
            self.raw.append(_squash(text))
            df.update(tf.keys())
        self.n = len(self.ids)
        self.avg = (sum(self.lens) / float(self.n)) if self.n else 0.0
        self.df = df

    def idf(self, term):
        df = self.df.get(term, 0)
        return math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))

    def search(self, query, limit=20, min_coverage=0.6):
        q = list(dict.fromkeys(tokenize(query)))
        if not q or not self.n:
            return []
        qsq = _squash(query)
        out = []
        for i in range(self.n):
            tf = self.tfs[i]
            matched = [t for t in q if t in tf]
            if not matched or len(matched) / float(len(q)) < min_coverage:
                continue
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * (self.lens[i] / self.avg if self.avg else 1))
            for t in matched:
                f = tf[t]
                score += self.idf(t) * f * (self.k1 + 1) / (f + norm)
            if len(qsq) >= 2 and qsq in self.raw[i]:
                score += 1.0   # 구문이 그대로 들어있으면 가산
            out.append((score, self.ids[i]))
        out.sort(key=lambda x: (-x[0], x[1]))
        return out[:limit]


def search_entries(entries, query, limit=20):
    """entries: journal.entries() 결과(dict). [(score, entry)] 반환."""
    docs = [(e["id"], e["text"] + " " + " ".join("#" + t for t in e["tags"])) for e in entries]
    idx = Index(docs)
    by_id = {e["id"]: e for e in entries}
    return [(s, by_id[i]) for s, i in idx.search(query, limit)]


REASON_MARKERS = [
    ("때문에", r"때문에|때문이|때문"),
    ("그래서", r"그래서|그러니까|그러다 보니"),
    ("포기", r"포기"),
    ("접었", r"접었|접기로|접고|접는"),
    ("안 하기로", r"안\s*하기로|하지\s*않기로|안\s*하게\s*됐|그만\s*(?:두|뒀|하기로|할)"),
    ("미루", r"미루|미뤘|미뤄|미뤄서"),
    ("지쳐", r"지쳐|귀찮|질려|번아웃|엄두가?\s*안|시간이?\s*없|바빠서"),
]
_MARKER_RES = [(name, re.compile(rx)) for name, rx in REASON_MARKERS]


def find_markers(sentence):
    return [name for name, rx in _MARKER_RES if rx.search(sentence)]


def why(journal_or_entries, topic, goals=()):
    """topic 을 언급한 일기에서 이유 표지가 있는 문장을 시간순으로.

    반환: [{ts, entry_id, sentence, markers, mentions_topic}] (+목표 포기 항목)
    """
    entries = journal_or_entries
    topic_sq = _squash(topic)
    hit = [e for e in entries if topic_sq and topic_sq in _squash(e["text"] + " " + " ".join(e["tags"]))]
    if not hit:
        hit = [e for _, e in search_entries(entries, topic, limit=50)]
    hit.sort(key=lambda e: (e["ts"], e["id"]))
    out = []
    for e in hit:
        for sent in split_sentences(e["text"]):
            mk = find_markers(sent)
            if mk:
                out.append({"ts": e["ts"], "entry_id": e["id"], "sentence": sent, "markers": mk,
                            "mentions_topic": topic_sq in _squash(sent)})
    # 목표 중 이 주제로 접은 것
    from .goals import keywords_match
    for g in goals:
        if g["status"] == "dropped" and (topic_sq in _squash(g["title"]) or keywords_match(g["keywords"], topic)):
            out.append({"ts": (g["dropped_day"] or g["created_day"]) + " 00:00:00", "entry_id": None,
                        "sentence": "[목표 접음] %s" % g["title"], "markers": ["접었"],
                        "mentions_topic": True})
    out.sort(key=lambda x: (x["ts"], x["entry_id"] or 0))
    return out
