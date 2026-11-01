"""OpenAI 호환 chat/completions 클라이언트 (llama-server, vLLM, OpenAI 등). 테스트에서는 가짜로 교체한다."""
import base64
import json
import urllib.request


class LLMError(RuntimeError):
    pass


class OpenAICompatClient:
    def __init__(self, base_url, model="local", api_key="", timeout=60):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def chat(self, messages, max_tokens=300, temperature=0.2):
        url = self.base_url
        if not url.endswith("/chat/completions"):
            url += "/chat/completions" if url.endswith("/v1") else "/v1/chat/completions"
        body = json.dumps({"model": self.model, "messages": messages,
                           "max_tokens": max_tokens, "temperature": temperature}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:  # 네트워크/형식 오류를 한 종류로
            raise LLMError("LLM 호출 실패: %s" % e) from e

    def ask(self, prompt, system=None, **kw):
        msgs = [{"role": "system", "content": system}] if system else []
        msgs.append({"role": "user", "content": prompt})
        return self.chat(msgs, **kw)

    def ask_with_image(self, prompt, image_data_uri, system=None, **kw):
        msgs = [{"role": "system", "content": system}] if system else []
        msgs.append({"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_uri}},
        ]})
        return self.chat(msgs, **kw)


def client_from_config(section):
    if not section or not section.get("base_url"):
        return None
    return OpenAICompatClient(section["base_url"], section.get("model", "local"),
                              section.get("api_key", ""), section.get("timeout", 60))


def to_data_uri(raw_bytes, mime="image/jpeg"):
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw_bytes).decode("ascii"))
