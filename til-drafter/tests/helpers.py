# -*- coding: utf-8 -*-
"""테스트 공용 도구: 합성 JSONL 픽스처 생성 (실제 로그 내용은 사용하지 않음)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SID = "sess-0001"


def user(ts, text, cwd="/work/blog", sid=SID, **extra):
    d = {"type": "user", "timestamp": ts, "cwd": cwd, "sessionId": sid,
         "message": {"role": "user", "content": text}}
    d.update(extra)
    return d


def assistant(ts, content, cwd="/work/blog", sid=SID, **extra):
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    d = {"type": "assistant", "timestamp": ts, "cwd": cwd, "sessionId": sid,
         "message": {"role": "assistant", "content": content}}
    d.update(extra)
    return d


def tool_use(name, **inp):
    return {"type": "tool_use", "id": "toolu_x", "name": name, "input": inp}


def tool_result(ts, text, cwd="/work/blog", sid=SID):
    return {"type": "user", "timestamp": ts, "cwd": cwd, "sessionId": sid,
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "toolu_x", "content": text}]}}


def write_jsonl(path, lines):
    """lines: dict 는 JSON 으로, str 은 그대로(깨진 줄 시뮬레이션) 기록."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for ln in lines:
            fh.write((json.dumps(ln, ensure_ascii=False) if isinstance(ln, dict) else ln) + "\n")
    return path
