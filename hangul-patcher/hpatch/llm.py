"""OpenAI 호환 엔드포인트(llama-server 등) 클라이언트. 표준 라이브러리만 사용."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Dict, List, Optional


class LLMError(Exception):
    """LLM 호출 실패 (응답 형식 오류, 4xx 등)."""


class LLMUnreachable(LLMError):
    """서버에 연결할 수 없음. 번역을 중단하고 진행 상황을 저장해야 한다."""


class OpenAIChatClient:
    """``POST {endpoint}/chat/completions`` 를 호출한다. 클라이언트는 주입 가능
    (``complete(messages) -> str`` 만 있으면 된다)."""

    def __init__(self, endpoint: str = "http://127.0.0.1:8080/v1", model: str = "local",
                 api_key: Optional[str] = None, temperature: float = 0.3, timeout: float = 600.0,
                 max_tokens: Optional[int] = None) -> None:
        ep = endpoint.rstrip("/")
        if not ep.endswith("/chat/completions"):
            ep += "/chat/completions"
        self.url = ep
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self.max_tokens = max_tokens
        # 로컬 서버이므로 환경변수 프록시는 쓰지 않는다
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def complete(self, messages: List[Dict[str, str]]) -> str:
        body = {"model": self.model, "messages": messages, "temperature": self.temperature, "stream": False}
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504):  # 모델 로딩 중 등
                raise LLMUnreachable(f"서버가 준비되지 않았습니다 (HTTP {e.code})") from e
            raise LLMError(f"HTTP {e.code}: {e.reason}") from e
        except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError, OSError) as e:
            raise LLMUnreachable(f"LLM 서버에 연결할 수 없습니다: {self.url} ({e})") from e
        try:
            data = json.loads(payload.decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"예상하지 못한 응답 형식: {e}") from e
        return content or ""
