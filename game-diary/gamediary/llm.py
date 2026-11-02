"""OpenAI 호환 서버(llama-server 등) 클라이언트. urllib 만 사용, 프록시 사용 안 함."""
import base64
import json
import re
import urllib.error
import urllib.request

from .sources import mime_for


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url, model="local", api_key="none", timeout=180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        # 환경의 HTTP(S)_PROXY 를 무시: 로컬 서버 직접 연결
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def chat(self, messages, max_tokens=400, temperature=0.2):
        body = json.dumps({"model": self.model, "messages": messages,
                           "max_tokens": max_tokens, "temperature": temperature}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key})
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                payload = json.loads(r.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            raise LLMError("HTTP %s: %s" % (e.code, e.read()[:200]))
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
            raise LLMError("LLM 서버 호출 실패 (%s): %s" % (self.base_url, e))


CAPTION_PROMPT = (
    "이 게임 스크린샷을 보고 아래 JSON 형식으로만 답하세요. 다른 설명은 쓰지 마세요.\n"
    '{"caption": "한 줄 설명(한국어)", "mood": "분위기(예: 긴장감, 평화로움)", '
    '"tags": ["태그1", "태그2"], "notable": true 또는 false}\n'
    "notable 은 보스전, 업적 달성, 웃긴 장면처럼 기억에 남을 장면이면 true 입니다. "
    "채팅창이나 사용자 이름이 보여도 옮겨 적지 마세요.")


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "y", "1", "예", "네", "참")
    return bool(v)


def _balanced_object(text):
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def _try_loads(s):
    for cand in (s, re.sub(r",\s*([}\]])", r"\1", s)):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except ValueError:
            pass
    return None


def parse_caption_json(text):
    """모델 출력에서 캡션 dict 를 최대한 복구. 항상 caption/mood/tags/notable 키를 가짐."""
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    obj = _try_loads(text)
    if obj is None:
        frag = _balanced_object(text)
        if frag:
            obj = _try_loads(frag)
    if obj is None:
        obj = {}
        m = re.search(r'"?caption"?\s*[:=]\s*"((?:[^"\\]|\\.)*)', text)
        if m:
            obj["caption"] = m.group(1)
        m = re.search(r'"?mood"?\s*[:=]\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            obj["mood"] = m.group(1)
        m = re.search(r'"?tags"?\s*[:=]\s*\[(.*?)\]', text, re.S)
        if m:
            obj["tags"] = re.findall(r'"([^"]+)"', m.group(1))
        m = re.search(r'"?notable"?\s*[:=]\s*"?(\w+)', text)
        if m:
            obj["notable"] = m.group(1)
        if "caption" not in obj:
            first = re.sub(r"```\w*", "", text).strip().splitlines()
            obj["caption"] = first[0].strip() if first else ""
    tags = obj.get("tags", [])
    if isinstance(tags, str):
        tags = re.split(r"[,#\s]+", tags)
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip().lstrip("#") for t in tags if str(t).strip()]
    return {"caption": str(obj.get("caption", "") or "").strip(),
            "mood": str(obj.get("mood", "") or "").strip(),
            "tags": tags[:8], "notable": _truthy(obj.get("notable", False))}


def caption_image(client, path, game=None):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    prompt = CAPTION_PROMPT + ("\n게임: " + game if game else "")
    msg = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime_for(path), b64)}}]}]
    return parse_caption_json(client.chat(msg, max_tokens=300))
