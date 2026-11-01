"""Summaries via a local OpenAI-compatible server (llama.cpp llama-server), with extractive fallback."""
import json
import re
import urllib.request

SYSTEM = ("당신은 개인 맞춤 뉴스 브리핑 편집자입니다. 항상 자연스러운 한국어로, "
          "사실만 간결하게 씁니다. 과장이나 추측은 하지 않습니다.")


class LLMError(Exception):
    pass


class OpenAICompatClient:
    """Minimal client for POST {base_url}/chat/completions (llama-server, LM Studio, vLLM, Ollama...)."""

    def __init__(self, base_url, model="local", api_key="", timeout=60, temperature=0.3, use_proxy=False, **_):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.timeout, self.temperature = model, api_key, timeout, temperature
        # A local llama-server must not be routed through a system/corporate HTTP proxy.
        handlers = [] if use_proxy else [urllib.request.ProxyHandler({})]
        self._open = urllib.request.build_opener(*handlers).open

    def chat(self, messages, max_tokens=300):
        body = json.dumps({"model": self.model, "messages": messages, "temperature": self.temperature,
                           "max_tokens": max_tokens, "stream": False}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        try:
            with self._open(urllib.request.Request(self.url, data=body, headers=headers),
                            timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:
            raise LLMError("LLM request failed: %s" % e)


def clean_llm_text(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)  # reasoning models
    return text.strip().strip('"').strip()


def split_sentences(text):
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s*|\n+", text or "")
    return [p.strip() for p in parts if len(p.strip()) > 1]


def extractive(item, max_sentences=2, max_chars=220):
    sentences = [s for s in split_sentences(item.summary)
                 if not re.match(r"^(Article URL|Comments URL|Points|# Comments|Comments):", s)]
    out = ""
    for s in sentences[:max_sentences]:
        if out and len(out) + len(s) > max_chars:
            break
        out = (out + " " + s).strip()
    if not out:
        return item.title
    return out if len(out) <= max_chars else out[:max_chars - 1].rstrip() + "…"


class Summarizer:
    def __init__(self, client=None):
        self.client = client
        self.llm_ok = client is not None
        self.errors = []

    @property
    def mode(self):
        return "llm" if self.llm_ok else "extractive"

    def _ask(self, prompt, max_tokens=300):
        if not self.llm_ok:
            return None
        try:
            out = clean_llm_text(self.client.chat(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}], max_tokens))
            return out or None
        except Exception as e:  # unreachable server etc.: stop trying for the rest of this run
            self.llm_ok = False
            self.errors.append(str(e))
            return None

    def summarize_item(self, item):
        prompt = ("다음 글을 한국어 1~2문장으로 요약하세요. 요약문만 출력하세요.\n\n"
                  "제목: %s\n출처: %s\n본문: %s" % (item.title, ", ".join(item.sources), item.summary[:1500]))
        text = self._ask(prompt)
        item.brief, item.brief_by = (text, "llm") if text else (extractive(item), "extractive")
        return item.brief

    def overview(self, items):
        """Exactly three lines for the '오늘의 요약' section."""
        if not items:
            return ["오늘은 관심사에 맞는 새 글이 없습니다.", "", ""][:3]
        listing = "\n".join("- [%s] %s: %s" % (i.topic, i.title, i.brief) for i in items[:15])
        text = self._ask("아래는 오늘 수집한 글 목록입니다. 가장 중요한 흐름을 한국어로 정확히 3줄로 "
                         "요약하세요. 각 줄은 한 문장, 번호나 기호 없이 출력하세요.\n\n" + listing, 400)
        lines = []
        if text:
            lines = [re.sub(r"^\s*(?:[-*•·]|\d+[.)])\s*", "", l).strip() for l in text.splitlines()]
            lines = [l for l in lines if l]
        if len(lines) < 3:
            lines = ["[%s] %s" % (i.topic, i.title) for i in items[:3]]
        return (lines + ["", "", ""])[:3]
