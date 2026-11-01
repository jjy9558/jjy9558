import json
import re

from subpipe.srt import Cue

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello everyone, welcome back.

2
00:00:03,200 --> 00:00:06,000
Today we test the NVIDIA RTX 5080
with 3,000 samples.

3
00:00:07,000 --> 00:00:09,500
It costs 1.5 dollars.
"""


def src_items(user_msg):
    m = re.search(r"### TRANSLATE\n(.*?)(?:\n\n###|\Z)", user_msg, re.S)
    return json.loads(m.group(1))


class FakeLLM:
    """Scripted LLM. responder(items, call_no) -> raw string; default translates to 'KO:<text>'."""

    def __init__(self, responder=None):
        self.calls = []
        self.responder = responder

    def chat(self, messages, temperature=0.2):
        items = src_items(messages[-1]["content"])
        self.calls.append((messages, items))
        if self.responder:
            return self.responder(items, len(self.calls))
        return json.dumps([{"id": i["id"], "text": "KO:" + i["text"]} for i in items], ensure_ascii=False)


def mkcues(texts, dur=2000, gap=100):
    out, t = [], 1000
    for n, tx in enumerate(texts, 1):
        out.append(Cue(n, t, t + dur, tx))
        t += dur + gap
    return out
