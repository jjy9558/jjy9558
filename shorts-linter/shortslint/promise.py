"""B9: 제목/썸네일 문구가 영상(대본)에서 지켜지는지 검사."""
import json
import os
import re
import urllib.request

from .model import FAIL, OK, SKIP, WARN, Result

CAT = "약속 일치"

JOSA = sorted(["에서는", "으로는", "에게서", "이라고", "라는", "에서", "으로", "에게", "까지", "부터",
               "처럼", "보다", "하고", "이랑", "한테", "이야", "인데", "하는", "했던", "은", "는", "이",
               "가", "을", "를", "의", "에", "와", "과", "도", "로", "만", "요", "야", "다"],
              key=len, reverse=True)
STOP = {"그리고", "하지만", "그런데", "이거", "저거", "진짜", "정말", "너무", "지금", "오늘", "영상",
        "쇼츠", "shorts", "short", "있는", "없는", "하는", "되는", "그냥", "이런", "저런", "그런"}
KO_NUM = {1: ["하나", "한 "], 2: ["둘", "두 "], 3: ["셋", "세 "], 4: ["넷", "네 "], 5: ["다섯"],
          6: ["여섯"], 7: ["일곱"], 8: ["여덟"], 9: ["아홉"], 10: ["열 ", "십 "]}


def stem(tok):
    if re.fullmatch(r"[A-Za-z]+", tok):
        return tok.lower()
    for j in JOSA:
        if tok.endswith(j) and len(tok) - len(j) >= 2:
            return tok[:-len(j)]
    return tok


def keywords(text):
    out = []
    for t in re.findall(r"[가-힣]+|[A-Za-z]{2,}", text or ""):
        s = stem(t)
        if len(s) < 2 or s in STOP or s in out:
            continue
        out.append(s)
    return out


def _squash(t):
    return re.sub(r"\s+", "", t.lower().replace(",", ""))


def keyword_hit(kw, flat):
    if kw in flat:
        return True
    return len(kw) >= 3 and kw[:-1] in flat


def number_present(n, transcript):
    t = transcript.replace(",", "")
    if re.search(r"(?<!\d)%d(?!\d)" % n, t):
        return True
    return any(w in transcript for w in KO_NUM.get(n, []))


def extract_names(text):
    names = re.findall(r"[\"'“‘「『]([^\"'”’」』]{2,12})[\"'”’」』]", text)
    names += re.findall(r"\b([A-Z][a-z]{2,})\b", text)
    names += [m[0] for m in re.findall(r"([가-힣]{2,4})\s?(씨|님|대리|과장|부장|사장|팀장)", text)]
    out = []
    for n in names:
        n = n.strip()
        if n and n not in out:
            out.append(n)
    return out


def score_promise(title, thumb_text, claims, transcript, cfg):
    p = cfg["promise"]
    promise_text = " ".join([title or "", thumb_text or ""] + [str(c) for c in (claims or [])])
    flat = _squash(transcript)
    kws = []
    for k in keywords(promise_text):
        if k not in kws:
            kws.append(k)
    matched = [k for k in kws if keyword_hit(k, flat)]
    missing = [k for k in kws if k not in matched]
    score = (len(matched) / float(len(kws))) if len(kws) >= p["min_keywords"] else None
    nums = [int(n) for n in re.findall(r"\d+", promise_text.replace(",", ""))]
    nums = [n for n in dict.fromkeys(nums) if n != 0]
    nums_missing = [n for n in nums if not number_present(n, transcript)]
    words_missing = []
    for w, syns in p["word_synonyms"].items():
        if w in promise_text and not any(_squash(s) in flat for s in syns):
            words_missing.append(w)
    names = extract_names(promise_text)
    names_missing = [n for n in names if _squash(n) not in flat]
    return {"score": score, "keywords": kws, "matched": matched, "missing": missing,
            "numbers": nums, "numbers_missing": nums_missing, "words_missing": words_missing,
            "names": names, "names_missing": names_missing}


def results_from_score(s, cfg):
    p = cfg["promise"]
    out = []
    sc = s["score"]
    if sc is None:
        out.append(Result("키워드 일치도", SKIP, "비교할 키워드가 부족합니다 (< %d개)" % p["min_keywords"], CAT))
    else:
        msg = "%.0f%% (%d/%d)" % (100 * sc, len(s["matched"]), len(s["keywords"]))
        if s["missing"]:
            msg += " - 대본에 없음: %s" % ", ".join(s["missing"][:6])
        st = FAIL if sc < p["fail_score"] else WARN if sc < p["warn_score"] else OK
        out.append(Result("키워드 일치도", st, msg, CAT))
    issues = []
    if s["numbers_missing"]:
        issues.append("숫자 %s 대본에 없음" % ", ".join(str(n) for n in s["numbers_missing"]))
    if s["words_missing"]:
        issues.append("'%s' 약속했으나 대본에 해당 표현 없음" % "/".join(s["words_missing"]))
    if s["names_missing"]:
        issues.append("이름/인용 '%s' 대본에 없음" % "', '".join(s["names_missing"]))
    out.append(Result("약속 항목", WARN if issues else OK,
                      "; ".join(issues) if issues else "숫자/이름/결말·반전 약속이 대본에서 확인됨", CAT))
    return out


def llm_judge(title, thumb_text, transcript, llm, opener=urllib.request.urlopen):
    """OpenAI 호환 /chat/completions 호출. 실패 시 예외."""
    url = llm["base_url"].rstrip("/") + "/chat/completions"
    if not url.startswith(("http://", "https://")):
        raise ValueError("base_url은 http(s)여야 합니다")
    prompt = ("유튜브 쇼츠의 제목/썸네일 문구가 영상 대본의 내용과 일치하는지 평가하세요.\n"
              "제목: %s\n썸네일 문구: %s\n대본:\n%s\n\n"
              'JSON으로만 답하세요: {"score": 0-100, "missing": ["대본에서 확인되지 않는 약속"], "verdict": "한 줄 평"}'
              % (title, thumb_text or "-", transcript[:6000]))
    body = json.dumps({"model": llm["model"], "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(llm.get("api_key_env") or "", "")
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with opener(req, timeout=llm.get("timeout", 60)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    j = json.loads(m.group(0))
    return {"score": float(j["score"]), "missing": j.get("missing") or [], "verdict": j.get("verdict", "")}


def check_promise(meta, transcript, cfg, opener=urllib.request.urlopen):
    title = (meta.get("title") or "").strip()
    if not title:
        return [Result("약속 일치", SKIP, "제목 없음", CAT)]
    if not transcript or not transcript.strip():
        return [Result("약속 일치", SKIP, "대본(.srt/.ass/.txt) 없음 - 비교 불가", CAT)]
    s = score_promise(title, meta.get("thumbnail_text"), meta.get("claims"), transcript, cfg)
    out = results_from_score(s, cfg)
    llm = cfg["llm"]
    if llm.get("enabled"):
        try:
            j = llm_judge(title, meta.get("thumbnail_text"), transcript, llm, opener)
            st = FAIL if j["score"] < llm["fail_below"] else WARN if j["score"] < llm["warn_below"] else OK
            msg = "LLM %.0f점 - %s" % (j["score"], j["verdict"])
            if j["missing"]:
                msg += " (미확인: %s)" % ", ".join(str(x) for x in j["missing"][:4])
            out.append(Result("LLM 판정", st, msg, CAT))
        except Exception as e:  # noqa: BLE001
            out.append(Result("LLM 판정", SKIP, "LLM 호출 실패, 규칙 기반 결과만 사용: %s" % str(e)[:80], CAT))
    return out
