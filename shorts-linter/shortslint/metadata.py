"""meta.json 검사. 필드: title, description, tags, hashtags, thumbnail, thumbnail_text,
claims, ai_disclosure, transcript."""
import difflib
import json
import os
import re

from .model import FAIL, OK, SKIP, WARN, Result

CAT = "메타데이터"
HASHTAG_RE = re.compile(r"#[0-9A-Za-z_ㄱ-ㆎ가-힣]+")


def load_meta(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("meta.json 최상위는 객체여야 합니다")
    return data


def collect_hashtags(meta):
    tags = []
    for h in meta.get("hashtags") or []:
        h = str(h).strip()
        if h:
            tags.append(h if h.startswith("#") else "#" + h)
    for field in ("title", "description"):
        tags.extend(HASHTAG_RE.findall(meta.get(field) or ""))
    seen, out = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def find_words(text, words):
    low = text.lower()
    return [w for w in words if w.lower() in low]


def check_metadata(meta, cfg, base_dir="."):
    m = cfg["metadata"]
    out = []
    title = (meta.get("title") or "").strip()
    # 제목 길이
    if not title:
        out.append(Result("제목", FAIL, "title이 비어 있습니다", CAT))
    else:
        n = len(title)
        if n > m["title_max_chars"]:
            out.append(Result("제목 길이", FAIL, "%d자 > 유튜브 한도 %d자" % (n, m["title_max_chars"]), CAT))
        elif n > m["title_visible_chars"]:
            out.append(Result("제목 길이", WARN, "%d자 - 쇼츠 피드에서 약 %d자 이후 잘림" % (n, m["title_visible_chars"]), CAT))
        else:
            out.append(Result("제목 길이", OK, "%d자" % n, CAT))
    # 해시태그
    tags = collect_hashtags(meta)
    n = len(tags)
    if n > m["hashtags_ignored_over"]:
        out.append(Result("해시태그", FAIL, "%d개 - %d개 초과 시 유튜브가 해시태그를 모두 무시" % (n, m["hashtags_ignored_over"]), CAT))
    else:
        notes = []
        st = OK
        if m["require_shorts_tag"] and not any(t.lower() == "#shorts" for t in tags):
            st = WARN
            notes.append("#shorts 없음")
        if n > m["hashtags_recommended_max"]:
            st = WARN
            notes.append("%d개 > 권장 %d개" % (n, m["hashtags_recommended_max"]))
        out.append(Result("해시태그", st, "; ".join(notes) or "%d개 (%s)" % (n, " ".join(tags)), CAT))
    # 금지/낚시 단어
    blob = " ".join(str(meta.get(k) or "") for k in ("title", "description", "thumbnail_text"))
    blob += " " + " ".join(str(c) for c in (meta.get("claims") or []))
    banned = find_words(blob, m["banned_words"])
    click = find_words(blob, m["clickbait_words"])
    if banned:
        out.append(Result("금지 단어", FAIL, "정책 위험 단어: %s" % ", ".join(banned), CAT))
    else:
        out.append(Result("금지 단어", OK, "없음", CAT))
    if click:
        out.append(Result("낚시성 단어", WARN, "%s - 영상 내용이 뒷받침하는지 확인 (과장 시 신뢰/노출 하락)" % ", ".join(click), CAT))
    else:
        out.append(Result("낚시성 단어", OK, "없음", CAT))
    # 설명/태그 한도
    desc = meta.get("description") or ""
    if len(desc) > m["description_max"]:
        out.append(Result("설명 길이", FAIL, "%d자 > %d자" % (len(desc), m["description_max"]), CAT))
    tagl = meta.get("tags") or []
    if sum(len(str(t)) for t in tagl) > m["tags_max_chars"]:
        out.append(Result("태그 길이", FAIL, "태그 합계 > %d자" % m["tags_max_chars"], CAT))
    # AI 고지
    if m["ai_content"]:
        d = meta.get("ai_disclosure")
        if d is True:
            out.append(Result("AI 콘텐츠 고지", OK, "고지 설정함 (업로드 시 '변경된 콘텐츠' 체크 확인)", CAT))
        else:
            out.append(Result("AI 콘텐츠 고지", WARN,
                              "현실적인 합성/변형 콘텐츠(AI 이미지·음성)는 업로드 시 고지 필요: %s" % m["ai_disclosure_link"], CAT))
    # 썸네일
    th = meta.get("thumbnail")
    if th:
        p = th if os.path.isabs(th) else os.path.join(base_dir, th)
        if not os.path.isfile(p):
            out.append(Result("썸네일 파일", FAIL, "파일 없음: %s" % th, CAT))
        else:
            mb = os.path.getsize(p) / 1048576.0
            ext = os.path.splitext(p)[1].lower()
            if mb > m["thumbnail_max_mb"]:
                out.append(Result("썸네일 파일", FAIL, "%.1f MB > %s MB 한도" % (mb, m["thumbnail_max_mb"]), CAT))
            elif ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                out.append(Result("썸네일 파일", WARN, "확장자 %s" % ext, CAT))
            else:
                out.append(Result("썸네일 파일", OK, "%.2f MB" % mb, CAT))
    return out


def _norm(t):
    return re.sub(r"[^0-9a-z가-힣]", "", t.lower())


def find_duplicates(entries, threshold=0.85):
    """entries: [(name, title)] -> {name: [Result]} (중복/유사 제목)."""
    out = {}
    normed = [(n, t, _norm(t)) for n, t in entries if t and _norm(t)]
    for i in range(len(normed)):
        for j in range(i + 1, len(normed)):
            a, b = normed[i], normed[j]
            if a[2] == b[2]:
                st, why = FAIL, "제목이 동일"
            else:
                r = difflib.SequenceMatcher(None, a[2], b[2]).ratio()
                if r < threshold:
                    continue
                st, why = WARN, "제목이 매우 유사 (%.0f%%)" % (100 * r)
            for me, other in ((a, b), (b, a)):
                out.setdefault(me[0], []).append(
                    Result("제목 중복", st, "%s: %s '%s'" % (why, "↔", other[0]), "배치"))
    return out
