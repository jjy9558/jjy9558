# -*- coding: utf-8 -*-
import json
import unittest
import os
import tempfile
from datetime import datetime

from vjournal.store import Journal

# 2026-11-02 는 월요일
MON = datetime(2026, 11, 2, 9, 0)


def mk(now=MON):
    box = {"now": now}
    j = Journal(":memory:", now=lambda: box["now"])
    j.box = box
    return j


def at(j, y, m, d, h=21, mi=0):
    j.box["now"] = datetime(y, m, d, h, mi)
    return j.box["now"]


def write_jsonl(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for l in lines:
            fh.write((l if isinstance(l, str) else json.dumps(l, ensure_ascii=False)) + "\n")


def msg(role, ts, content, cwd="/home/user/proj-a", sid="s1", **extra):
    o = {"type": role, "timestamp": ts, "cwd": cwd, "sessionId": sid,
         "message": {"role": role, "content": content}}
    o.update(extra)
    return o


class Base(unittest.TestCase):
    """Journal 을 만들고 테스트 끝에 닫아준다."""

    def mk(self, now=MON, llm=None):
        j = mk(now)
        j.llm = llm
        self.addCleanup(j.close)
        return j
