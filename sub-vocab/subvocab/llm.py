"""Optional Korean meanings from a local OpenAI-compatible chat endpoint.

If the endpoint cannot be reached, fill_meanings stops after the first failure and
leaves every meaning blank, so the vocabulary is still produced.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .vocab import Entry

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "local-model"
DEFAULT_TIMEOUT = 60.0
DEFAULT_BATCH_SIZE = 20
MAX_MEANING_CHARS = 60

SYSTEM_PROMPT = (
    "You are an English-Korean dictionary assistant for a Korean learner. "
    "For each item, give a short Korean meaning of the word as it is used in the "
    "given subtitle sentence (at most 15 Korean characters, no example sentences). "
    "Reply with JSON only, in this exact shape: "
    '{"items": [{"word": "<word>", "meaning": "<Korean meaning>"}]}'
)


class LLMError(Exception):
    """The LLM endpoint is unreachable or returned an unusable HTTP response."""


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    timeout: float = DEFAULT_TIMEOUT
    batch_size: int = DEFAULT_BATCH_SIZE


def load_llm_config(path: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> LLMConfig:
    """Load settings. Environment variables override the JSON file, which overrides defaults.

    Environment: SUBVOCAB_LLM_BASE_URL, SUBVOCAB_LLM_MODEL, SUBVOCAB_LLM_API_KEY.
    Raises ValueError if the config file exists but is not valid JSON.
    """
    env = os.environ if env is None else env
    data: Dict[str, object] = {}
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ValueError(f"LLM 설정 파일을 읽을 수 없습니다: {path} ({exc})") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"LLM 설정 파일은 JSON 객체여야 합니다: {path}")
        data = loaded

    def pick(key: str, env_key: str, default, cast):
        if env.get(env_key):
            return cast(env[env_key])
        if key in data and data[key] not in (None, ""):
            return cast(data[key])
        return default

    return LLMConfig(
        base_url=pick("base_url", "SUBVOCAB_LLM_BASE_URL", DEFAULT_BASE_URL, str),
        model=pick("model", "SUBVOCAB_LLM_MODEL", DEFAULT_MODEL, str),
        api_key=pick("api_key", "SUBVOCAB_LLM_API_KEY", "", str),
        timeout=pick("timeout", "SUBVOCAB_LLM_TIMEOUT", DEFAULT_TIMEOUT, float),
        batch_size=max(1, pick("batch_size", "SUBVOCAB_LLM_BATCH_SIZE", DEFAULT_BATCH_SIZE, int)),
    )


class LLMClient:
    """Minimal client for POST {base_url}/chat/completions."""

    def __init__(self, config: LLMConfig):
        self.config = config
        host = urllib.parse.urlsplit(config.base_url).hostname or ""
        if host in ("127.0.0.1", "localhost", "::1"):
            # A local model should not be routed through an HTTP proxy from the environment.
            self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            self._opener = urllib.request.build_opener()

    @property
    def endpoint(self) -> str:
        return self.config.base_url.rstrip("/") + "/chat/completions"

    def complete(self, messages: List[Dict[str, str]]) -> str:
        payload = {"model": self.config.model, "messages": messages, "temperature": 0.2}
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = "Bearer " + self.config.api_key
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.config.timeout) as response:
                raw = response.read()
        except (OSError, http.client.HTTPException) as exc:  # URLError, HTTPError, timeouts
            raise LLMError(f"LLM 서버에 연결할 수 없습니다 ({self.endpoint}): {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
            return str(data["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM 응답 형식이 올바르지 않습니다: {exc}") from exc


def build_messages(entries: Sequence[Entry]) -> List[Dict[str, str]]:
    items = [{"word": e.word, "sentence": e.example} for e in entries]
    user = (
        "Give the Korean meaning of each word as used in its sentence.\n"
        + json.dumps(items, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _extract_json(text: str):
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start:end + 1])
        except ValueError:
            return None
    return None


def parse_meanings(content: str) -> Dict[str, str]:
    """Map lowercase word -> meaning from the model's JSON reply. Bad replies give {}."""
    data = _extract_json(content)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {}
    result: Dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word", "")).strip().lower()
        meaning = " ".join(str(item.get("meaning", "")).split())[:MAX_MEANING_CHARS]
        if word and meaning:
            result[word] = meaning
    return result


def fill_meanings(
    entries: Sequence[Entry],
    client,
    batch_size: int = DEFAULT_BATCH_SIZE,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """Fill Entry.meaning in batches. Returns how many entries got a meaning.

    `client` needs a complete(messages) -> str method. On LLMError the remaining
    batches are skipped and their meanings stay blank.
    """
    filled = 0
    for start in range(0, len(entries), max(1, batch_size)):
        batch = entries[start:start + max(1, batch_size)]
        try:
            content = client.complete(build_messages(batch))
        except LLMError as exc:
            if log:
                log(f"LLM 사용 불가, 뜻 칸을 비워 둡니다: {exc}")
            break
        mapping = parse_meanings(content)
        for entry in batch:
            meaning = mapping.get(entry.word.lower())
            if meaning:
                entry.meaning = meaning
                filled += 1
    return filled
