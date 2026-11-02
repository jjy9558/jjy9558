"""LLM 백엔드: OpenAI 호환(llama-server 등), Anthropic Messages API, 테스트용 Fake."""
import json
import os
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class LLMError(RuntimeError):
    pass


class LLM:
    """messages: [{"role": "system"|"user"|"assistant", "content": str}] -> str"""

    def chat(self, messages, max_tokens=1024, temperature=0.8):
        raise NotImplementedError


def _post_json(url, headers, payload, timeout, use_proxy):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    if use_proxy:
        opener = urllib.request.build_opener()
    else:  # 로컬 서버는 프록시 환경변수를 무시한다
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise LLMError("LLM 요청 실패: %s" % e) from e


class OpenAICompatLLM(LLM):
    def __init__(self, base_url=None, model="local", api_key=None,
                 timeout=300, transport=None):
        self.base_url = (base_url or os.environ.get("GMTRPG_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.timeout = timeout
        self.transport = transport  # (url, headers, payload) -> dict

    def chat(self, messages, max_tokens=1024, temperature=0.8):
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": max_tokens, "temperature": temperature,
                   "stream": False}
        headers = {}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        url = self.base_url + "/chat/completions"
        if self.transport:
            resp = self.transport(url, headers, payload)
        else:
            resp = _post_json(url, headers, payload, self.timeout, False)
        try:
            return resp["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("예상치 못한 응답 형식: %r" % (resp,)) from e


class AnthropicLLM(LLM):
    def __init__(self, model=None, api_key=None, timeout=120, transport=None):
        self.model = model or os.environ.get("GMTRPG_MODEL") or DEFAULT_CLAUDE_MODEL
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.timeout = timeout
        self.transport = transport

    def build_payload(self, messages, max_tokens, temperature):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = []
        for m in messages:
            if m["role"] == "system":
                continue
            if msgs and msgs[-1]["role"] == m["role"]:  # 같은 역할 연속은 병합
                msgs[-1]["content"] += "\n\n" + m["content"]
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        if not msgs or msgs[0]["role"] != "user":
            msgs.insert(0, {"role": "user", "content": "(시작)"})
        payload = {"model": self.model, "max_tokens": max_tokens,
                   "temperature": temperature, "messages": msgs}
        if system:
            payload["system"] = system
        return payload

    def chat(self, messages, max_tokens=1024, temperature=0.8):
        if not self.api_key and not self.transport:
            raise LLMError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
        headers = {"x-api-key": self.api_key,
                   "anthropic-version": "2023-06-01"}
        payload = self.build_payload(messages, max_tokens, temperature)
        if self.transport:
            resp = self.transport(ANTHROPIC_URL, headers, payload)
        else:
            resp = _post_json(ANTHROPIC_URL, headers, payload, self.timeout, True)
        try:
            return "".join(b.get("text", "") for b in resp["content"]
                           if b.get("type") == "text")
        except (KeyError, TypeError, AttributeError) as e:
            raise LLMError("예상치 못한 응답 형식: %r" % (resp,)) from e


class FakeLLM(LLM):
    """replies: 문자열 리스트(마지막은 반복) 또는 callable(messages)->str."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []
        self._i = 0

    def chat(self, messages, max_tokens=1024, temperature=0.8):
        self.calls.append([dict(m) for m in messages])
        if callable(self.replies):
            return self.replies(messages)
        r = self.replies[min(self._i, len(self.replies) - 1)]
        self._i += 1
        return r
