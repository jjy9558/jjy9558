"""선택 기능: 로컬 LLM으로 문장만 다시 다듬는다. 사실(이름, 숫자)이 하나라도 사라지면 원문을 쓴다."""
from __future__ import annotations

import json
import re
from dataclasses import replace

from ._jjkit import llm
from .engine import Judgment

_TOKEN = re.compile(r"\d+")


def polish(j: Judgment, client, names: list[str]) -> Judgment:
    """주문/이유/변명 등 문장 목록을 한 번에 다시 쓴다. 실패하거나 사실이 변하면 원본 그대로."""
    idx: list[tuple[int, int]] = []
    texts: list[str] = []
    for si, (_, lines) in enumerate(j.sections):
        for li, ln in enumerate(lines):
            if ln.strip() and not ln.startswith("복사용"):
                idx.append((si, li))
                texts.append(ln)
    if not texts or client is None:
        return j
    prompt = ("아래 JSON 배열의 문장들을 법원 판결문 말투로 더 웃기게 다시 쓰세요. 배열 길이와 순서를 유지하고, "
              "이름과 숫자는 그대로 두세요. 앞의 '- '는 유지하세요. JSON 배열만 출력하세요.\n"
              + json.dumps(texts, ensure_ascii=False))
    try:
        data = client.chat_json([{"role": "user", "content": prompt}], temperature=0.7)
    except llm.LLMError:
        return j
    if not isinstance(data, list) or len(data) != len(texts) or not all(isinstance(x, str) for x in data):
        return j
    joined_old, joined_new = " ".join(texts), " ".join(data)
    if any(n and n not in joined_new for n in names):
        return j
    if sorted(_TOKEN.findall(joined_old)) != sorted(_TOKEN.findall(joined_new)):
        return j
    new_sections = [(h, list(ls)) for h, ls in j.sections]
    for (si, li), old, new in zip(idx, texts, data):
        new = new.strip()
        if old.startswith("- ") and not new.startswith("- "):
            new = "- " + new
        new_sections[si][1][li] = new or old
    return replace(j, sections=new_sections, llm_used=True)
