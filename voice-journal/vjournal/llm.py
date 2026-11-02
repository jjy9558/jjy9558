# -*- coding: utf-8 -*-
"""선택적 OpenAI 호환 로컬 LLM. 실패/무응답/이상한 출력이면 항상 None/[] 로 폴백한다.

client 는 ``chat(messages) -> str`` 만 있으면 된다 (테스트에선 가짜 객체).
"""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime

from .goals import Declaration, extract_keywords
from .parse import find_number


class LLMError(Exception):
    pass


def _is_loopback(host):
    return host in ("127.0.0.1", "localhost", "::1", "[::1]") or (host or "").startswith("127.")


class LLMClient(object):
    def __init__(self, base_url, model="local", api_key=None, timeout=30, allow_remote=False):
        host = urllib.parse.urlparse(base_url).hostname or ""
        if not allow_remote and not _is_loopback(host):
            raise LLMError("루프백이 아닌 LLM 주소는 VJ_LLM_ALLOW_REMOTE=1 일 때만 허용됩니다: %s" % host)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def chat(self, messages, max_tokens=512, temperature=0.2):
        body = json.dumps({"model": self.model, "messages": messages,
                           "max_tokens": max_tokens, "temperature": temperature}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body,
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except Exception as e:  # 네트워크/파싱 전부 LLMError 로
            raise LLMError(str(e))


def from_env(environ=None):
    env = os.environ if environ is None else environ
    base = env.get("VJ_LLM_BASE_URL")
    if not base:
        return None
    return LLMClient(base, env.get("VJ_LLM_MODEL", "local"), env.get("VJ_LLM_API_KEY"),
                     allow_remote=env.get("VJ_LLM_ALLOW_REMOTE") == "1")


def _safe_chat(client, messages):
    if client is None:
        return None
    try:
        out = client.chat(messages)
    except Exception:
        return None
    return out if isinstance(out, str) and out.strip() else None


def parse_json_block(text):
    """코드펜스/잡설이 섞인 문자열에서 JSON 값을 뽑는다. 실패하면 None."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except ValueError:
                continue
    return None


_GOAL_PROMPT = (
    "다음 한국어 일기에서 사용자가 스스로 한 '선언/목표'(기한이나 반복 주기가 있는 다짐)만 뽑아라. "
    "JSON 배열만 출력: [{\"title\":str,\"period\":\"once|day|week\",\"target\":number|null,"
    "\"unit\":str|null,\"deadline\":\"YYYY-MM-DD\"|null}]. 없으면 []. 오늘 날짜: %s\n\n%s")


def llm_extract_goals(client, text, today):
    """LLM 으로 선언을 추출. 어떤 오류든 [] 를 돌려준다."""
    out = _safe_chat(client, [{"role": "user", "content": _GOAL_PROMPT % (today.isoformat(), text)}])
    data = parse_json_block(out)
    if not isinstance(data, list):
        return []
    decls = []
    for it in data[:10]:
        if not isinstance(it, dict):
            continue
        title = it.get("title")
        if not isinstance(title, str) or len(title.strip()) < 2:
            continue
        period = it.get("period") if it.get("period") in ("once", "day", "week") else "once"
        target = it.get("target")
        if isinstance(target, bool) or not isinstance(target, (int, float)) or target <= 0:
            target = None
        unit = it.get("unit") if isinstance(it.get("unit"), str) else None
        deadline = None
        if isinstance(it.get("deadline"), str):
            try:
                deadline = datetime.strptime(it["deadline"], "%Y-%m-%d").date()
            except ValueError:
                deadline = None
        title = title.strip()
        if target is None:
            num = find_number(title)
            if num:
                target, unit = num
        decls.append(Declaration(title, extract_keywords(title), period, target, unit,
                                 deadline, None, title))
    return decls


def llm_summarize_lines(client, context, n=3, instruction=None):
    """정확히 n 줄 요약을 기대. 형식이 안 맞으면 None(호출자가 규칙 기반으로 폴백)."""
    ins = instruction or ("아래 내용을 한국어로 정확히 %d줄로 요약해라. 각 줄은 한 문장, 번호/불릿 없이." % n)
    out = _safe_chat(client, [{"role": "user", "content": ins + "\n\n" + context}])
    if out is None:
        return None
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", l).strip() for l in out.splitlines()]
    lines = [l for l in lines if l]
    if len(lines) != n:
        return None
    return [l[:200] for l in lines]
