"""댓글 군집: 읽기 → 정규화 → 한국어 글자 n-gram TF-IDF → 결정적 군집 → 대표 n-gram 이름 → 주제 TOP5.

- 입력: YouTube Data API ``commentThreads`` JSON, Takeout comments.csv(``Comment Text`` 열의
  ``takeoutSegments`` JSON 포함), 문자열 목록 JSON, 열 이름이 있는 CSV, 한 줄에 한 댓글인 붙여넣기 텍스트.
- 작성자 이름·채널 ID 같은 열/키는 **읽지 않는다**. 본문의 @멘션도 지운다. 본문 속 전화/이메일 등은 마스킹한다.
- 군집은 구형(spherical) k-means. 초기화와 반복 순서가 ``seed``로 고정되어 같은 입력이면 같은 결과다.
- 이름은 군집 중심의 대표 n-gram. 로컬 LLM이 있으면(옵션) 사람이 읽기 좋은 이름으로 다듬고, 실패하면 n-gram 이름을 쓴다.
"""
import csv
import html
import io
import json
import math
import random
import re
import unicodedata
from pathlib import Path

from ._jjkit import redact

MAX_COMMENTS = 20000
MAX_COMMENT_CHARS = 1000

# 본문 후보 키(앞쪽이 우선). 작성자/채널/ID 계열 키는 일부러 넣지 않는다.
TEXT_KEYS = ("textoriginal", "textdisplay", "text", "comment", "commenttext", "content", "body", "message",
             "댓글", "댓글내용", "내용", "본문")
TEXT_COLS = TEXT_KEYS


def _key(k) -> str:
    return re.sub(r"[\s_\-]+", "", str(k)).lower()


# --------------------------------------------------------------------------
# 읽기
# --------------------------------------------------------------------------
def _segments_text(s: str) -> str:
    """Takeout의 ``{"takeoutSegments":[{"text":"..."}]}`` 문자열이면 본문만 꺼낸다."""
    t = s.strip()
    if t.startswith("{") and "takeoutSegments" in t:
        try:
            obj = json.loads(t)
            segs = obj.get("takeoutSegments") or []
            return "".join(str(x.get("text", "")) for x in segs if isinstance(x, dict))
        except ValueError:
            return s
    return s


def _walk(obj, out: list[str], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, list):
        for x in obj:
            _walk(x, out, depth + 1)
    elif isinstance(obj, dict):
        keys = {_key(k): k for k in obj}
        used = None
        for tk in TEXT_KEYS:
            if tk in keys and isinstance(obj[keys[tk]], str):
                used = keys[tk]
                out.append(_segments_text(obj[used]))
                break
        if used is None and "takeoutsegments" in keys:
            segs = obj[keys["takeoutsegments"]]
            if isinstance(segs, list):
                out.append("".join(str(x.get("text", "")) for x in segs if isinstance(x, dict)))
            used = keys["takeoutsegments"]
        for k, v in obj.items():
            if k != used and isinstance(v, (dict, list)):
                _walk(v, out, depth + 1)  # snippet/replies 등 안쪽 구조


def _from_csv(text: str) -> list[str]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return []
    head = [_key(c) for c in rows[0]]
    idx = next((head.index(c) for c in TEXT_COLS if c in head), None)
    body = rows[1:] if idx is not None else rows
    if idx is None:
        ncol = max(len(r) for r in rows)
        if ncol == 1:
            idx = 0
        else:  # 이름으로 못 찾으면 평균 글자 수가 가장 긴 열을 본문으로 본다
            idx = max(range(ncol), key=lambda j: sum(len(r[j]) for r in rows if j < len(r)))
    return [_segments_text(r[idx]) for r in body if idx < len(r)]


def parse_comments(text: str) -> list[str]:
    """문자열(파일 내용 또는 붙여넣기)에서 댓글 본문 목록을 꺼낸다. 정규화 전의 원문."""
    text = text.lstrip("﻿")
    s = text.lstrip()
    out: list[str] = []
    if s[:1] in "[{":
        try:
            _walk(json.loads(s), out)
            return out
        except ValueError:
            pass
    first = s.split("\n", 1)[0] if s else ""
    head = {_key(c) for c in re.split(r"[,\t;]", first)}
    if head & set(TEXT_COLS):
        return _from_csv(text)
    return [ln for ln in text.splitlines() if ln.strip()]


def load_comments(path) -> list[str]:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949", errors="replace")  # 한국어 엑셀이 저장한 CSV
    return parse_comments(text)[:MAX_COMMENTS]


# --------------------------------------------------------------------------
# 정규화와 분류
# --------------------------------------------------------------------------
_TAG = re.compile(r"<[^>]{1,200}>")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_MENTION = re.compile(r"@[\w.\-가-힣]+")
_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_REPEAT = re.compile(r"(.)\1{2,}")
_KEEP = re.compile(r"[^0-9a-z가-힣\s]")
_WS = re.compile(r"\s+")

SPAM_RE = re.compile(r"맞구독|구독 ?(?:하고|할게|해요|해줘)\s*(?:가|갑니다|와)|텔레그램|카톡 ?문의|오픈채팅|수익 ?보장|"
                     r"재택 ?부업|토토|바카라|카지노|대출|조건만남|무료 ?상담|클릭", re.I)
REQUEST_RE = re.compile(r"해 ?주세요|해 ?줘요?|해 ?주실|보고 ?싶|듣고 ?싶|다뤄|올려 ?주|후속|다음 ?편|다음에|풀어 ?주|썰|부탁")
QUESTION_RE = re.compile(r"\?|인가요|나요\b|까요|궁금|어떻게|어떡|왜 ")
PRAISE_RE = re.compile(r"최고|재밌|재미있|감동|공감|대박|잘 ?봤|고마|좋아요|멋지|사이다")


def clean_text(raw: str) -> str:
    """표시용 본문: HTML 제거, 멘션/URL 제거, 개인정보 마스킹, 공백 정리."""
    t = html.unescape(_TAG.sub(" ", raw or ""))
    t = _URL.sub(" ", t)  # NFKC는 쓰지 않는다(ㅋㅋ가 조합형 자모로 바뀌어 깨져 보임)
    t = _MENTION.sub(" ", t)
    t = redact.redact(t)
    return _WS.sub(" ", t).strip()[:MAX_COMMENT_CHARS]


def normalize(raw: str) -> str:
    """특징 추출용: 소문자, 한글/영문/숫자만, 반복 글자 줄이기, 시각 표기 제거."""
    t = html.unescape(_TAG.sub(" ", raw or ""))
    t = unicodedata.normalize("NFKC", t).lower()
    t = _URL.sub(" ", t)
    t = _MENTION.sub(" ", t)
    t = _TIME.sub(" ", t)
    t = _REPEAT.sub(r"\1\1", t)
    t = _KEEP.sub(" ", t)
    return _WS.sub(" ", t).strip()


def kind_of(raw: str) -> str:
    """spam / request / question / praise / other (규칙 기반. 사람이 검토할 목록용이며 자동 응답에 쓰지 않는다)."""
    if _URL.search(raw or "") or SPAM_RE.search(raw or ""):
        return "spam"
    if REQUEST_RE.search(raw):
        return "request"
    if QUESTION_RE.search(raw):
        return "question"
    if PRAISE_RE.search(raw):
        return "praise"
    return "other"


# --------------------------------------------------------------------------
# TF-IDF
# --------------------------------------------------------------------------
def ngrams(norm: str, sizes=(2, 3)) -> list[str]:
    """어절 안에서만 글자 n-gram을 만든다(공백을 넘지 않음). 영문 단어는 통째로 하나."""
    out = []
    for w in norm.split():
        if re.fullmatch(r"[a-z]{2,}", w):
            out.append(w)
            continue
        for part in re.findall(r"[가-힣]+", w):
            if len(part) == 2:
                out.append(part)
                continue
            for n in sizes:
                for i in range(len(part) - n + 1):
                    out.append(part[i:i + n])
    return out


def tfidf(docs: list[list[str]], min_df: int = 2, max_df: float = 0.5):
    """(벡터 목록, 어휘 목록). 벡터는 {특징번호: 가중치}이고 L2 정규화되어 있다."""
    n = len(docs)
    df: dict[str, int] = {}
    for d in docs:
        for g in set(d):
            df[g] = df.get(g, 0) + 1
    max_abs = max(2, int(max_df * n)) if n >= 6 else n
    vocab = sorted(g for g, c in df.items() if min_df <= c <= max_abs)
    index = {g: i for i, g in enumerate(vocab)}
    idf = [math.log((1 + n) / (1 + df[g])) + 1.0 for g in vocab]
    vecs = []
    for d in docs:
        tf: dict[int, int] = {}
        for g in d:
            j = index.get(g)
            if j is not None:
                tf[j] = tf.get(j, 0) + 1
        v = {j: (1 + math.log(c)) * idf[j] for j, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values()))
        vecs.append({j: x / norm for j, x in v.items()} if norm else {})
    return vecs, vocab


def _dot(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(j, 0.0) for j, x in a.items())


def _centroid(vecs: list[dict], members: list[int]) -> dict:
    acc: dict[int, float] = {}
    for i in members:
        for j, x in vecs[i].items():
            acc[j] = acc.get(j, 0.0) + x
    norm = math.sqrt(sum(x * x for x in acc.values()))
    return {j: x / norm for j, x in acc.items()} if norm else {}


def _kmeans_once(vecs, k, rng, max_iter=40):
    n = len(vecs)
    # k-means++ 초기화 (코사인 거리, 시드 고정)
    first = rng.randrange(n)
    centers = [vecs[first]]
    best = [1.0 - _dot(v, centers[0]) for v in vecs]
    while len(centers) < k:
        total = sum(b * b for b in best)
        if total <= 1e-12:
            pick = rng.randrange(n)
        else:
            r, acc, pick = rng.random() * total, 0.0, n - 1
            for i, b in enumerate(best):
                acc += b * b
                if acc >= r:
                    pick = i
                    break
        centers.append(vecs[pick])
        for i, v in enumerate(vecs):
            best[i] = min(best[i], 1.0 - _dot(v, centers[-1]))
    assign = [-1] * n
    for _ in range(max_iter):
        new = []
        for v in vecs:
            sims = [_dot(v, c) for c in centers]
            new.append(max(range(k), key=lambda c: (sims[c], -c)))
        if new == assign:
            break
        assign = new
        for c in range(k):
            members = [i for i, a in enumerate(assign) if a == c]
            if members:
                centers[c] = _centroid(vecs, members)
            else:  # 빈 군집: 자기 중심과 가장 안 닮은 문서로 다시 시작
                far = min(range(n), key=lambda i: (_dot(vecs[i], centers[assign[i]]), i))
                centers[c] = vecs[far]
    score = sum(_dot(v, centers[a]) for v, a in zip(vecs, assign))
    return assign, centers, score


def _margin(vecs, assign, centers) -> float:
    """중심 기준 실루엣 비슷한 값: (자기 군집 유사도 - 다른 군집 최대 유사도)의 평균."""
    tot = 0.0
    for v, a in zip(vecs, assign):
        sims = [_dot(v, c) for c in centers]
        own = sims[a]
        other = max((s for j, s in enumerate(sims) if j != a), default=0.0)
        tot += own - other
    return tot / max(1, len(vecs))


def kmeans(vecs, k, seed=7, n_init=6):
    best = None
    for t in range(n_init):
        rng = random.Random(seed * 1009 + t)
        res = _kmeans_once(vecs, k, rng)
        if best is None or res[2] > best[2] + 1e-12:
            best = res
    return best


def choose_k(vecs, seed=7, kmin=2, kmax=8) -> int:
    n = len(vecs)
    kmax = max(kmin, min(kmax, n // 10))
    best_k, best_s = kmin, -1e9
    for k in range(kmin, kmax + 1):
        assign, centers, _ = kmeans(vecs, k, seed, n_init=3)
        s = _margin(vecs, assign, centers)
        if s > best_s + 0.005:  # 거의 같으면 더 작은 k
            best_k, best_s = k, s
    return best_k


def representative(centroid: dict, vocab: list[str], background: dict, topn: int = 3) -> list[str]:
    """군집 중심에서 가중치가 큰 n-gram. 다른 군집에도 흔한 것은 깎고, 서로 포함하는 것은 긴 쪽만 남긴다."""
    scored = sorted(((x - 0.5 * background.get(j, 0.0), j) for j, x in centroid.items()), reverse=True)
    picked: list[str] = []
    for _, j in scored:
        g = vocab[j]
        if any(g in p for p in picked):
            continue
        picked = [p for p in picked if p not in g]
        picked.append(g)
        if len(picked) >= topn:
            break
    return picked


# --------------------------------------------------------------------------
# 전체 흐름
# --------------------------------------------------------------------------
def cluster_comments(raws: list[str], *, k: int | None = None, seed: int = 7, top: int = 5,
                     min_size: int = 2) -> dict:
    items = []
    kinds = {"spam": 0, "request": 0, "question": 0, "praise": 0, "other": 0}
    for raw in raws:
        if not isinstance(raw, str) or not raw.strip():
            continue
        kd = kind_of(raw)
        kinds[kd] += 1
        if kd == "spam":
            continue
        norm = normalize(raw)
        items.append({"raw": raw, "norm": norm, "kind": kd})
    docs = [ngrams(it["norm"]) for it in items]
    vecs, vocab = tfidf(docs)
    usable = [i for i, v in enumerate(vecs) if v]
    result = {"total": sum(kinds.values()), "kinds": kinds, "used": len(usable), "unclustered": len(items) - len(usable),
              "k": 0, "seed": seed, "clusters": [], "assignments": [-1] * len(items), "top": []}
    if len(usable) < 4:
        result["note"] = "군집을 만들기에 댓글이 너무 적습니다(%d개)" % len(usable)
        return result
    uvecs = [vecs[i] for i in usable]
    kk = k if k else choose_k(uvecs, seed)
    kk = max(1, min(kk, len(uvecs)))
    assign, centers, _ = kmeans(uvecs, kk, seed)
    background: dict[int, float] = {}
    for c in centers:
        for j, x in c.items():
            background[j] = background.get(j, 0.0) + x / len(centers)
    clusters = []
    for c in range(kk):
        members = [usable[i] for i, a in enumerate(assign) if a == c]
        if not members:
            continue
        grams = representative(centers[c], vocab, background)
        order = sorted(members, key=lambda i: (-_dot(vecs[i], centers[c]), i))
        cl = {"id": len(clusters), "size": len(members), "ngrams": grams,
              "name": " · ".join(grams) if grams else "(이름 없음)",
              "requests": sum(1 for i in members if items[i]["kind"] == "request"),
              "questions": sum(1 for i in members if items[i]["kind"] == "question"),
              "samples": [clean_text(items[i]["raw"])[:120] for i in order[:3]],
              "members": members}
        clusters.append(cl)
        for i in members:
            result["assignments"][i] = cl["id"]
    result["k"] = len(clusters)
    result["clusters"] = clusters
    result["top"] = top_topics(clusters, len(usable), top, min_size)
    result["_items"] = [it["raw"] for it in items]  # 내부용(저장 전에 제거)
    return result


def top_topics(clusters: list[dict], used: int, top: int = 5, min_size: int = 2) -> list[dict]:
    """다음 사연 주제 후보: 크기 + 시청자 요청(가중 2) 순."""
    ranked = sorted((c for c in clusters if c["size"] >= min_size),
                    key=lambda c: (-(c["size"] + 2 * c["requests"]), c["id"]))
    return [{"rank": r, "cluster": c["id"], "name": c["name"], "size": c["size"],
             "share": round(100.0 * c["size"] / max(1, used), 1), "requests": c["requests"],
             "questions": c["questions"], "samples": c["samples"][:2]}
            for r, c in enumerate(ranked[:top], 1)]


def purity(assignments: list[int], labels: list) -> float:
    """정답 라벨 기준 순도(테스트/검증용). 군집에 못 들어간(-1) 문서는 틀린 것으로 센다."""
    by: dict[int, dict] = {}
    for a, lab in zip(assignments, labels):
        if a < 0:
            continue
        by.setdefault(a, {})
        by[a][lab] = by[a].get(lab, 0) + 1
    good = sum(max(d.values()) for d in by.values())
    return good / max(1, len(labels))


def llm_names(client, clusters: list[dict]) -> dict[int, str]:
    """로컬 LLM으로 군집 이름을 다듬는다. 어떤 실패든 빈 dict(=n-gram 이름 유지)."""
    if client is None or not clusters:
        return {}
    payload = [{"id": c["id"], "ngrams": c["ngrams"], "samples": c["samples"][:3]} for c in clusters]
    msgs = [{"role": "system", "content": "당신은 유튜브 쇼츠 댓글 분석가입니다. 군집마다 8~16자 한국어 주제 이름을 붙이세요. "
             "개인 이름은 쓰지 마세요."},
            {"role": "user", "content": "아래 군집에 이름을 붙여 JSON으로만 답하세요: "
             '{"names":[{"id":0,"name":"..."}]}\n' + json.dumps(payload, ensure_ascii=False)}]
    try:
        data = client.chat_json(msgs, max_tokens=600)
        out = {}
        valid = {c["id"] for c in clusters}
        for row in data.get("names", []) if isinstance(data, dict) else []:
            if not isinstance(row, dict):
                continue
            name = redact.redact(str(row.get("name", ""))).strip()
            if name and isinstance(row.get("id"), int) and row["id"] in valid:
                out[row["id"]] = name[:30]
        return out
    except Exception:  # noqa: BLE001 - LLM 실패는 조용히 n-gram 이름으로 대체
        return {}


def apply_names(result: dict, names: dict[int, str]) -> None:
    for c in result["clusters"]:
        if c["id"] in names:
            c["llm_name"] = names[c["id"]]
    for t in result["top"]:
        if t["cluster"] in names:
            t["llm_name"] = names[t["cluster"]]


def public_view(result: dict) -> dict:
    """파일/리포트에 내보낼 요약. 원문 전체와 멤버 목록은 빼고, 표시용 마스킹된 발췌만 남긴다."""
    out = {k: v for k, v in result.items() if k not in ("_items", "assignments")}
    out["clusters"] = [{k: v for k, v in c.items() if k != "members"} for c in result["clusters"]]
    return out


def format_top(result: dict) -> str:
    from ._jjkit import textwidth
    kinds = result["kinds"]
    if result.get("note"):
        return "%s (스팸 %d, 요청 %d, 질문 %d, 칭찬 %d)" % (result["note"], kinds["spam"], kinds["request"],
                                                    kinds["question"], kinds["praise"])
    lines = ["댓글 %d개 (스팸 %d, 요청 %d, 질문 %d, 칭찬 %d) -> 군집 %d개, 군집에 못 든 댓글 %d개" %
             (result["total"], kinds["spam"], kinds["request"], kinds["question"], kinds["praise"],
              result["k"], result["unclustered"]), "", "다음 사연 주제 TOP%d" % len(result["top"])]
    rows = []
    for t in result["top"]:
        name = t.get("llm_name") or t["name"]
        rows.append((t["rank"], name, t["size"], "%.0f%%" % t["share"], t["requests"], t["questions"]))
    lines += textwidth.table(rows, header=("순위", "주제(대표 n-gram)", "댓글", "비중", "요청", "질문"),
                             right_cols=(0, 2, 3, 4, 5), max_width={1: 30})
    for t in result["top"]:
        if t.get("llm_name"):
            lines.append("  %d위 n-gram: %s" % (t["rank"], t["name"]))
        for s in t["samples"]:
            lines.append("  %d위 예: %s" % (t["rank"], s))
    return "\n".join(lines)
