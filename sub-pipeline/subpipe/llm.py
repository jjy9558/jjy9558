"""Minimal OpenAI-compatible chat client (llama-server) using urllib."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Dict, List


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


class OpenAIClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080/v1", model: str = "local",
                 api_key: str = "none", timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        body = json.dumps({"model": self.model, "messages": messages,
                           "temperature": temperature, "stream": False}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LLMError("LLM 서버 오류 HTTP %s: %s" % (e.code, e.reason)) from e
        except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError, OSError) as e:
            raise LLMUnavailable(
                "LLM 서버(%s)에 연결할 수 없습니다: %s\n"
                "llama-server가 실행 중인지 확인하세요. 예) llama-server -m model.gguf --port 8080"
                % (self.base_url, e)) from e
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("예상하지 못한 LLM 응답 형식: %r" % (data,)) from e
