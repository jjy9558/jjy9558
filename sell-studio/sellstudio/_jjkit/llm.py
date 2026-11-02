# generated from jjkit@29f495e8; 직접 고치지 말 것
"""OpenAI 호환 ``/chat/completions`` 클라이언트 (LM Studio, llama.cpp server, Ollama, vLLM 등).

기준 구현: paper-butler/paperbutler/llm.py, used-pc-judge/usedpc/llm.py.

- 기본은 로컬/사설망 주소만 허용한다(개인 데이터가 밖으로 나가지 않게). 외부 주소는 ``allow_remote=True``.
- 로컬 주소면 환경 프록시를 끈다(``ProxyHandler({})``).
- ``opener``를 주입하면 그걸로 보낸다(테스트용 가짜 opener).
- ``parse_json``은 코드펜스, ``<think>`` 블록, 앞뒤 설명문, 끝 쉼표, 잘린 괄호를 복구한다.
- ``json_mode=True``는 ``response_format`` 을 보내지 않는다(LM Studio 등이 거부함). 대신
  응답을 ``parse_json``으로 복구한다. 서버가 지원하면 ``response_format=...``을 직접 넘긴다.

예::

    c = LLMClient("http://127.0.0.1:1234/v1", model="qwen2.5-7b")
    text = c.chat([{"role": "user", "content": "안녕"}])
    obj = c.chat_json([...])                       # dict 또는 list
    cap = c.vision("이 사진을 한 줄로", ["photo.jpg"])
"""
import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from .netutil import NetError, Opener, is_local_url, opener_for, request_json

__all__ = ["LLMError", "LLMClient", "is_local_url", "parse_json", "strip_think",
           "image_data_uri", "vision_message", "client_from_env"]

MAX_IMAGE_BYTES = 15 * 1024 * 1024


class LLMError(Exception):
    pass


def _endpoint(base_url: str) -> str:
    u = base_url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    return u + "/chat/completions"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 일부 서버는 [{"type":"text","text":...}]로 준다
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


class LLMClient:
    """OpenAI 호환 엔드포인트 클라이언트."""

    def __init__(self, base_url: str, model: str = "local-model", *, api_key: str = "",
                 vision_model: str = "", timeout: float = 120.0, allow_remote: bool = False,
                 opener: Opener | None = None, max_tokens: int | None = None):
        if not base_url:
            raise LLMError("LLM 주소가 비었습니다.")
        if not allow_remote and not is_local_url(base_url):
            raise LLMError("로컬/사설망 LLM 주소만 허용합니다: %s (외부로 보내려면 allow_remote=True)"
                           % base_url)
        self.base_url = base_url.rstrip("/")
        self.url = _endpoint(base_url)
        self.model = model or "local-model"
        self.vision_model = vision_model or self.model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.opener = opener_for(self.url, opener)

    # -- 저수준 -------------------------------------------------------------
    def chat(self, messages: list[dict], *, model: str | None = None, max_tokens: int | None = None,
             temperature: float = 0.0, json_mode: bool = False,
             response_format: dict | None = None, extra: dict | None = None) -> str:
        """응답 본문 문자열. ``json_mode``면 JSON만 내라는 시스템 지시를 앞에 붙인다."""
        msgs = list(messages)
        if json_mode and not any(m.get("role") == "system" for m in msgs):
            msgs.insert(0, {"role": "system", "content": "반드시 JSON 하나만 출력하세요. 설명 문장 금지."})
        body: dict[str, Any] = {"model": model or self.model, "messages": msgs,
                                "temperature": temperature, "stream": False}
        mt = max_tokens if max_tokens is not None else self.max_tokens
        if mt:
            body["max_tokens"] = int(mt)
        if response_format:
            body["response_format"] = response_format
        if extra:
            body.update(extra)
        headers = {"Authorization": "Bearer " + self.api_key} if self.api_key else {}
        try:
            _, data = request_json(self.url, body, headers=headers, opener=self.opener,
                                   timeout=self.timeout)
        except NetError as e:
            raise LLMError("LLM 호출 실패: %s" % e) from e
        try:
            return _content_text(data["choices"][0]["message"]["content"]) or ""
        except (TypeError, KeyError, IndexError) as e:
            raise LLMError("LLM 응답 형식이 이상합니다") from e

    # -- 고수준 -------------------------------------------------------------
    def chat_json(self, messages: list[dict], **kw: Any) -> Any:
        kw.setdefault("json_mode", True)
        return parse_json(self.chat(messages, **kw))

    def vision(self, prompt: str, images: list[str | bytes | Path], *, system: str = "",
               **kw: Any) -> str:
        msgs = [{"role": "system", "content": system}] if system else []
        msgs.append(vision_message(prompt, images))
        kw.setdefault("model", self.vision_model)
        return self.chat(msgs, **kw)

    def vision_json(self, prompt: str, images: list[str | bytes | Path], **kw: Any) -> Any:
        return parse_json(self.vision(prompt, images, **kw))


# --------------------------------------------------------------------------
# 비전 메시지
# --------------------------------------------------------------------------
def _sniff_mime(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw[:3] == b"GIF":
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def image_data_uri(image: str | bytes | Path, mime: str = "",
                   max_bytes: int = MAX_IMAGE_BYTES) -> str:
    """파일 경로/바이트/이미 만든 data URI -> ``data:image/...;base64,...``."""
    if isinstance(image, str) and image.startswith(("data:image/", "http://", "https://")):
        return image
    if isinstance(image, (bytes, bytearray)):
        raw = bytes(image)
    else:
        p = Path(image)
        raw = p.read_bytes()
        mime = mime or mimetypes.guess_type(p.name)[0] or ""
    if len(raw) > max_bytes:
        raise LLMError("이미지가 너무 큽니다(%dMB 초과)" % (max_bytes // (1024 * 1024)))
    mime = mime or _sniff_mime(raw)
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def vision_message(prompt: str, images: list[str | bytes | Path], detail: str = "") -> dict:
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for img in images:
        iu: dict[str, str] = {"url": image_data_uri(img)}
        if detail:
            iu["detail"] = detail
        parts.append({"type": "image_url", "image_url": iu})
    return {"role": "user", "content": parts}


# --------------------------------------------------------------------------
# JSON 복구 파서
# --------------------------------------------------------------------------
_THINK = re.compile(r"<think>.*?(?:</think>|\Z)", re.S | re.I)
_FENCE = re.compile(r"```(?:json|JSON)?")
_SMART = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def strip_think(text: str) -> str:
    """추론 모델의 ``<think>...</think>`` 블록을 지운다."""
    return _THINK.sub("", text or "").strip()


def _scan(t: str, start: int) -> tuple[int, list[str]]:
    """start의 여는 괄호부터 짝이 맞는 곳까지. (끝 인덱스 또는 -1, 남은 닫는 괄호 스택)."""
    stack: list[str] = []
    in_str = esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                return i, stack  # 짝이 안 맞음: 여기서 끊는다
            stack.pop()
            if not stack:
                return i, []
    if in_str:
        stack.append('"')
    return -1, stack


def _cleanup(s: str) -> str:
    s = re.sub(r",\s*([}\]])", r"\1", s)  # 끝 쉼표
    return s


def parse_json(text: str, want: type | tuple[type, ...] = (dict, list)) -> Any:
    """LLM 응답에서 첫 JSON 값을 꺼낸다. 실패하면 ``LLMError``."""
    if not text or not text.strip():
        raise LLMError("빈 응답")
    t = _FENCE.sub("", strip_think(text)).translate(_SMART)
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise LLMError("응답에서 JSON을 찾지 못했습니다")
    start = min(starts)
    if isinstance(want, type):
        want = (want,)
    if dict in want and list not in want and t[start] == "[" and "{" in t:
        start = t.find("{")
    end, rest = _scan(t, start)
    if end >= 0:
        cand = t[start:end + 1]
        if rest:  # 짝이 어긋난 지점에서 끊긴 경우
            cand = t[start:end] + "".join(reversed(rest))
    else:  # 잘린 응답: 남은 괄호를 닫아 본다
        cand = t[start:].rstrip().rstrip(",") + "".join(reversed(rest))
    obj: Any = None
    for attempt in (cand, _cleanup(cand)):
        try:
            obj = json.loads(attempt)
            break
        except ValueError:
            continue
    else:
        raise LLMError("JSON 파싱 실패")
    if not isinstance(obj, want):
        raise LLMError("기대한 JSON 형식이 아닙니다(%s)" % type(obj).__name__)
    return obj


# --------------------------------------------------------------------------
def _env_true(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def client_from_env(prefix: str, *, default_url: str = "", env: dict | None = None,
                    **kw: Any) -> LLMClient | None:
    """``{PREFIX}_LLM_URL``, ``_LLM_MODEL``, ``_LLM_VISION_MODEL``, ``_LLM_KEY``,
    ``_ALLOW_REMOTE_LLM`` 을 읽어 클라이언트를 만든다. 주소가 없으면 None(LLM 끔)."""
    e = os.environ if env is None else env
    p = prefix.upper().rstrip("_")
    url = e.get(p + "_LLM_URL") or default_url
    if not url:
        return None
    kw.setdefault("model", e.get(p + "_LLM_MODEL") or "local-model")
    kw.setdefault("vision_model", e.get(p + "_LLM_VISION_MODEL") or "")
    kw.setdefault("api_key", e.get(p + "_LLM_KEY") or "")
    kw.setdefault("allow_remote", _env_true(e.get(p + "_ALLOW_REMOTE_LLM")))
    return LLMClient(url, **kw)
