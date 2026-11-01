"""로컬 OpenAI 호환 엔드포인트로 한국어 요약. 실패 시 발췌(번역 없음)."""
import json
import re
import urllib.request

PROMPT = ("다음은 Steam 게임 '{title}'의 패치노트(영어일 수 있음)입니다. 핵심 변경점을 한국어 불릿 3~5개로 "
          "요약하세요. 각 줄은 '- '로 시작하고, 고유명사는 원문을 유지하세요. 다른 말은 쓰지 마세요.\n\n{text}")


def _http_chat(base_url, model, messages, timeout, api_key=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 프록시 미사용
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2,
                       "max_tokens": 500}).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body, headers=headers)
    with opener.open(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def parse_bullets(text, limit=5):
    out = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        m = re.match(r"^(?:[-*•·]|\d+[.)])\s+(.*)$", ln)
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
    return out[:limit]


class Summarizer:
    def __init__(self, base_url="http://127.0.0.1:8080/v1", model="local", enabled=True,
                 timeout=60, api_key=None, llm=None):
        """llm: 테스트용 callable(messages)->str"""
        self.base_url, self.model, self.enabled = base_url, model, enabled
        self.timeout, self.api_key, self._llm = timeout, api_key, llm
        self._down = False

    def extractive(self, text, n=4, width=140):
        lines = [ln.lstrip("- ").strip() for ln in (text or "").splitlines()]
        lines = [ln for ln in lines if len(ln) >= 4]
        return [ln if len(ln) <= width else ln[:width - 1] + "…" for ln in lines[:n]]

    def summarize(self, title, text):
        """-> (bullets, mode)"""
        if self.enabled and not self._down and text.strip():
            msgs = [{"role": "user", "content": PROMPT.format(title=title, text=text[:6000])}]
            try:
                raw = self._llm(msgs) if self._llm else _http_chat(
                    self.base_url, self.model, msgs, self.timeout, self.api_key)
                bullets = parse_bullets(raw)
                if bullets:
                    return bullets, "llm"
            except Exception:
                self._down = True  # 한 번 실패하면 이번 실행에선 재시도 안 함
        return self.extractive(text), "extract"
