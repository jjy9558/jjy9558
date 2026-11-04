"""결정 도우미: 동전 / 점수표 / LLM. GUI 의존성 없음."""
import json
import random
import re
import urllib.request

from .storage import llm_endpoint

MIN_OPTIONS = 2
MAX_OPTIONS = 5


def parse_options(text):
    """'라면 vs 김밥 vs 굶기' 또는 쉼표/슬래시 구분 문자열을 리스트로."""
    parts = re.split(r"\s+vs\.?\s+|\s*,\s*|\s*/\s*|\s*\|\s*", text.strip(), flags=re.I)
    return validate_options([p.strip() for p in parts if p.strip()])


def validate_options(options):
    opts = [str(o).strip() for o in options if str(o).strip()]
    if len(set(opts)) != len(opts):
        raise ValueError("선택지가 중복되었어요.")
    if not (MIN_OPTIONS <= len(opts) <= MAX_OPTIONS):
        raise ValueError("선택지는 %d~%d개여야 해요." % (MIN_OPTIONS, MAX_OPTIONS))
    return opts


def _result(mode, options, choice, reason, **extra):
    d = {"mode": mode, "options": list(options), "choice": choice, "reason": reason}
    d.update(extra)
    return d


def decide_coin(options, rng=None):
    rng = rng or random
    options = validate_options(options)
    choice = rng.choice(options)
    return _result("coin", options, choice, "동전을 던졌더니 '%s'이(가) 나왔어요. 고민은 짧게!" % choice)


def score_options(options, criteria, scores):
    """criteria: [(이름, 가중치>0)], scores: {옵션: {기준: 1~5}}.
    반환: {옵션: 가중 평균(1~5 스케일)}"""
    if not (2 <= len(criteria) <= 3):
        raise ValueError("기준은 2~3개여야 해요.")
    total_w = 0.0
    for name, w in criteria:
        if w <= 0:
            raise ValueError("가중치는 0보다 커야 해요: %s" % name)
        total_w += w
    out = {}
    for opt in options:
        s = 0.0
        for name, w in criteria:
            v = scores[opt][name]
            if not (1 <= v <= 5):
                raise ValueError("점수는 1~5여야 해요: %s/%s" % (opt, name))
            s += w * v
        out[opt] = round(s / total_w, 4)
    return out


def decide_scores(options, criteria, scores, rng=None):
    options = validate_options(options)
    criteria = [(str(n), float(w)) for n, w in criteria]
    result = score_options(options, criteria, scores)
    best = max(result.values())
    top = [o for o in options if result[o] == best]
    rng = rng or random
    choice = top[0] if len(top) == 1 else rng.choice(top)
    # 이유: 가장 점수 차이를 만든 기준을 설명
    crit_best = max(criteria, key=lambda c: c[1] * scores[choice][c[0]])
    table = " / ".join("%s %.2f" % (o, result[o]) for o in options)
    reason = "가중 점수 %s → '%s' 승! 특히 '%s'(가중치 %g, %d점)에서 강했어요." % (
        table, choice, crit_best[0], crit_best[1], scores[choice][crit_best[0]])
    if len(top) > 1:
        reason += " (동점이라 동전으로 결정)"
    return _result("score", options, choice, reason, scores=result,
                   criteria=[list(c) for c in criteria])


def _chat_url(endpoint):
    e = endpoint.rstrip("/")
    if e.endswith("/chat/completions"):
        return e
    return e + "/chat/completions"


def decide_llm(options, llm_config, rng=None, opener=None):
    """OpenAI 호환 엔드포인트에 물어본다. 실패하면 동전으로 폴백."""
    options = validate_options(options)
    opener = opener or urllib.request.urlopen
    prompt = (
        "다음 선택지 중 하나를 골라 주세요: %s\n"
        '반드시 JSON 한 줄로만 답하세요: {"choice": "<선택지 그대로>", "reason": "<한국어 한 문장 이유>"}'
        % ", ".join(options)
    )
    body = {
        "model": llm_config.get("model", "local-model"),
        "messages": [
            {"role": "system", "content": "당신은 결정 장애를 도와주는 귀여운 데스크톱 펫입니다. 짧고 단호하게 답합니다."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
    }
    headers = {"Content-Type": "application/json"}
    if llm_config.get("api_key"):
        headers["Authorization"] = "Bearer " + llm_config["api_key"]
    try:
        req = urllib.request.Request(_chat_url(llm_endpoint(llm_config)),
                                     data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
        resp = opener(req, timeout=llm_config.get("timeout", 8))
        try:
            payload = json.loads(resp.read().decode("utf-8"))
        finally:
            close = getattr(resp, "close", None)
            if close:
                close()
        content = payload["choices"][0]["message"]["content"]
        choice, reason = _parse_llm_answer(content, options)
        if choice is None:
            raise ValueError("선택지를 알아볼 수 없는 답변")
        return _result("llm", options, choice, reason or "LLM이 골랐어요.")
    except Exception as exc:  # 네트워크/파싱 모든 실패 -> 폴백
        res = decide_coin(options, rng)
        res["mode"] = "llm-fallback"
        res["reason"] = "LLM에 연결하지 못해(%s) 동전으로 대신 정했어요. %s" % (
            type(exc).__name__, res["reason"])
        return res


def _parse_llm_answer(content, options):
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            ch = str(obj.get("choice", "")).strip()
            if ch in options:
                return ch, str(obj.get("reason", "")).strip()
        except (ValueError, AttributeError):
            pass
    found = [o for o in options if o in content]
    if len(found) == 1:
        return found[0], content.strip()[:120]
    return None, None
