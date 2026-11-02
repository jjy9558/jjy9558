"""OpenAI 호환 로컬 서버(llama-server 등) 클라이언트와 견고한 JSON 파서. 프록시는 쓰지 않는다."""
import base64
import json
import re
import urllib.error
import urllib.request

PROMPT = (
    "이 사진은 집 안 방을 찍은 것입니다. 사진에 보이는 '잃어버리기 쉬운 작은 물건과 택배'를 찾아서 "
    "각각 어디에 있는지 한국어로 짧게 설명하세요 (리모컨, 지갑, 열쇠, 휴대폰, 안경, 충전기, 택배 상자, 가방, 컵, 책 등). "
    "큰 가구나 벽·바닥은 물건으로 적지 말고 위치 설명에만 쓰세요. 사람의 얼굴이나 신원은 적지 마세요.\n"
    "반드시 JSON 하나만 출력하세요. 설명이나 코드블록 없이 다음 형식으로:\n"
    '{"objects":[{"name":"리모컨","where":"소파 왼쪽 쿠션 위","confidence":0.9}],"scene":"거실 전경, 불이 켜져 있음"}\n'
    "confidence 는 0~1 사이 숫자입니다. 물건이 없으면 objects 를 빈 배열로 두세요."
)


class VlmError(Exception):
    pass


class VlmClient:
    def __init__(self, base_url="http://127.0.0.1:8080/v1", model="local", api_key="none", timeout=180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or "none"
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def chat(self, messages, max_tokens=700, temperature=0.1):
        body = json.dumps({"model": self.model, "messages": messages,
                           "max_tokens": max_tokens, "temperature": temperature}).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.api_key})
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            return data["choices"][0]["message"]["content"] or ""
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
            raise VlmError("VLM 호출 실패 (%s): %s" % (self.base_url, e))

    def describe(self, jpeg_bytes):
        """JPEG 바이트 -> 정규화된 {"objects":[...], "scene": str}. 파싱 불가면 VlmError."""
        uri = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")
        text = self.chat([{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": uri}}]}])
        return parse_description(text)

    def phrase(self, question, facts):
        """사실 목록을 자연스러운 한국어 한두 문장으로. 실패 시 VlmError."""
        prompt = ("사용자가 '%s' 라고 물었습니다. 아래 기록만 근거로 친근한 존댓말 한두 문장으로 답하세요. "
                  "기록에 없는 내용은 지어내지 마세요. 시간 표현과 위치 문구는 그대로 쓰세요.\n%s" % (question, facts))
        return self.chat([{"role": "user", "content": prompt}], max_tokens=200, temperature=0.2).strip()


# ---------------------------------------------------------------- JSON 파싱

def _strip_noise(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S | re.I)   # 여는 태그 없이 닫기만 있는 경우
    text = re.sub(r"<think>.*$", "", text, flags=re.S | re.I)       # 닫히지 않은 think (전부 사고)
    m = re.search(r"```(?:json|JSON)?\s*(.*?)(?:```|$)", text, flags=re.S)
    if m and ("{" in m.group(1) or "[" in m.group(1)):
        text = m.group(1)
    return text.strip()


def _remove_trailing_commas(s):
    out, in_str, esc = [], False, False
    for i, ch in enumerate(s):
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
        elif ch in "}]":
            j = len(out) - 1
            while j >= 0 and out[j] in " \t\r\n":
                j -= 1
            if j >= 0 and out[j] == ",":
                del out[j]
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def _scan(s):
    """(스택, 문자열 안인지, 마지막 문자열 밖 쉼표 위치들)"""
    stack, in_str, esc, commas = [], False, False, []
    for i, ch in enumerate(s):
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
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
        elif ch == ",":
            commas.append(i)
    return stack, in_str, commas


def _close(s):
    stack, in_str, _ = _scan(s)
    if in_str:
        s += '"'
    s = s.rstrip()
    s = re.sub(r",\s*$", "", s)
    return s + "".join("}" if c == "{" else "]" for c in reversed(stack))


def _loads_lenient(s):
    s = _remove_trailing_commas(s)
    try:
        return json.JSONDecoder().raw_decode(s)[0]
    except ValueError:
        pass
    # 잘림 복구: 끝에서부터 쉼표 위치로 잘라 가며 닫아 본다
    cur = s
    for _ in range(200):
        try:
            return json.loads(_remove_trailing_commas(_close(cur)))
        except ValueError:
            pass
        _, _, commas = _scan(cur)
        if not commas:
            break
        cur = cur[:commas[-1]]
    raise ValueError("unparseable")


def _clamp(v):
    try:
        if isinstance(v, str):
            v = v.strip()
            v = float(v[:-1]) / 100.0 if v.endswith("%") else float(v)
        v = float(v)
    except (TypeError, ValueError):
        return 0.5
    if v > 1.0:
        v = v / 100.0 if v <= 100 else 1.0
    return max(0.0, min(1.0, v))


def _s(v, n=200):
    return re.sub(r"\s+", " ", v).strip()[:n] if isinstance(v, str) else ""


def normalize(data):
    if isinstance(data, list):
        data = {"objects": data}
    if not isinstance(data, dict):
        raise ValueError("object expected")
    raw = data.get("objects")
    if raw is None:
        raw = data.get("물건", data.get("items"))
    if raw is None and "scene" not in data and "장면" not in data:
        raise ValueError("no objects/scene keys")
    raw = raw or []
    objs = []
    for o in raw if isinstance(raw, list) else []:
        if isinstance(o, str):
            o = {"name": o}
        if not isinstance(o, dict):
            continue
        name = _s(o.get("name", o.get("이름", "")), 60)
        if not name:
            continue
        objs.append({"name": name, "where": _s(o.get("where", o.get("위치", o.get("location", ""))), 200),
                     "confidence": _clamp(o.get("confidence", o.get("신뢰도", 0.5)))})
        if len(objs) >= 50:
            break
    return {"objects": objs, "scene": _s(data.get("scene", data.get("장면", "")), 300)}


def parse_description(text):
    """VLM 출력에서 JSON 을 뽑아 정규화. 코드펜스, <think>, 앞뒤 잡담, 끝쉼표, 잘림 허용."""
    body = _strip_noise(text)
    starts = [m.start() for m in re.finditer(r"[{\[]", body)]
    for st in starts[:5]:
        try:
            return normalize(_loads_lenient(body[st:]))
        except ValueError:
            continue
    raise VlmError("VLM 출력에서 JSON 을 찾지 못했습니다: %r" % (body[:120],))
