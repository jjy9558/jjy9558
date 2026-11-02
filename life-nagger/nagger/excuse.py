"""지각 변명 생성기: 템플릿 + (선택) 로컬 LLM(OpenAI 호환). 정직한 옵션은 항상 포함."""
import json
import random

from .outputs import http_post

PLAUSIBLE = [
    "{to}, 안녕하세요. 출근길 지하철이 지연되어 {late} 정도 늦을 것 같습니다. 도착 즉시 업무 시작하겠습니다. 죄송합니다.",
    "{to}, 죄송합니다. 아침에 급한 개인 용무가 생겨 {late} 늦게 {arrive}할 것 같습니다. 양해 부탁드립니다.",
    "{to}, 안녕하세요. 도로 정체가 예상보다 심해 {late} 정도 늦어질 것 같습니다. 먼저 메신저로 확인 가능한 건 처리해 두겠습니다.",
    "{to}, 죄송합니다. 컨디션이 좋지 않아 준비가 지연되어 {late} 늦게 {arrive}하겠습니다. 오늘 일정은 차질 없이 따라가겠습니다.",
    "{to}, 안녕하세요. 집 앞 엘리베이터 점검으로 이동이 늦어져 {late} 정도 지각할 듯합니다. 죄송합니다.",
    "{to}, 죄송합니다. 출발 직전 급한 연락을 처리하느라 {late} 늦게 {arrive}합니다. 이후 일정에는 지장 없도록 하겠습니다.",
]
HONEST = "{to}, 늦잠 잤습니다, 죄송합니다. {late} 안에 {arrive}하겠습니다."


def fmt_late(minutes):
    minutes = int(minutes)
    h, m = divmod(minutes, 60)
    if h and m:
        return "%d시간 %d분" % (h, m)
    if h:
        return "%d시간" % h
    return "%d분" % m


def honorific(to):
    to = (to or "").strip()
    if not to:
        return "안녕하세요"
    return to if to.endswith("님") else to + "님"


def template_drafts(late, to, where="office", seed=None, count=3):
    rng = random.Random(seed)
    ctx = {"to": honorific(to), "late": fmt_late(late), "arrive": "접속" if where == "remote" else "출근"}
    picks = rng.sample(PLAUSIBLE, count)
    return [p.format(**ctx) for p in picks], HONEST.format(**ctx)


def llm_drafts(cfg, late, to, where, post=http_post):
    """실패하면 None. 응답은 JSON 문자열 배열이거나 줄바꿈으로 구분된 문장."""
    if not cfg.get("base_url"):
        return None
    prompt = ("직장에서 %s 지각 메시지를 %s에게 보내려 합니다. 정중한 한국어 사과 메시지 3개를 "
              "서로 다른 사유로 JSON 문자열 배열로만 답하세요. 각 메시지는 2문장 이내. 사유는 일상적인 것만."
              % (fmt_late(late), honorific(to)))
    body = {"model": cfg.get("model") or "local", "temperature": 0.7, "max_tokens": 400,
            "messages": [{"role": "system", "content": "당신은 간결한 한국어 비서입니다."},
                         {"role": "user", "content": prompt}]}
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    try:
        status, raw = post(cfg["base_url"].rstrip("/") + "/chat/completions",
                           json.dumps(body).encode("utf-8"), headers, cfg.get("timeout", 20))
        if status != 200:
            return None
        text = json.loads(raw.decode("utf-8"))["choices"][0]["message"]["content"].strip()
        try:
            items = json.loads(text)
        except ValueError:
            items = [l.strip(" -*0123456789.") for l in text.splitlines()]
        items = [s.strip() for s in items if isinstance(s, str) and 5 <= len(s.strip()) <= 300]
        return items[:3] or None
    except Exception:
        return None


def generate(late, to, where="office", seed=None, llm_cfg=None, use_llm=False, post=http_post):
    """(drafts 리스트, honest, source). LLM이 실패/비면 템플릿으로 폴백. honest는 항상 존재."""
    drafts, honest = template_drafts(late, to, where, seed)
    source = "template"
    if use_llm and llm_cfg:
        got = llm_drafts(llm_cfg, late, to, where, post)
        if got:
            drafts, source = got, "llm"
            while len(drafts) < 3:   # 부족분은 템플릿으로 채움
                extra = [d for d in template_drafts(late, to, where, seed)[0] if d not in drafts]
                drafts.append(extra[0])
    return drafts, honest, source
