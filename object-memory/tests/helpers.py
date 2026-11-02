import os
import secrets
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from objmem import config as cfgmod  # noqa: E402
from objmem.core import App  # noqa: E402
from objmem.vlm import VlmError  # noqa: E402

_counter = [0]


def fake_token():
    return "t" + secrets.token_hex(12)      # 실행 시점에 생성 (고정 비밀값을 코드에 두지 않음)


def jpeg(tag=None):
    """디코딩하지 않으므로 SOI/EOI 마커만 맞춘 바이트면 충분. tag 마다 sha256 이 다름."""
    _counter[0] += 1
    return b"\xff\xd8" + (tag or ("frame-%d-%s" % (_counter[0], secrets.token_hex(4)))).encode() + b"\xff\xd9"


def at(y, mo, d, h=0, mi=0):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


NOW = at(2026, 11, 2, 22, 0)


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class FakeVlm:
    """describe() 는 queue 에서 하나씩 꺼내거나 fail=True 면 VlmError."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = 0
        self.fail = False
        self.phrased = None

    def describe(self, data):
        self.calls += 1
        if self.fail:
            raise VlmError("fake down")
        if self.results:
            return self.results.pop(0)
        return {"objects": [], "scene": ""}

    def phrase(self, q, facts):
        if self.phrased is None:
            raise VlmError("no llm")
        return self.phrased


def obj(name, where="", conf=0.9):
    return {"name": name, "where": where, "confidence": conf}


def make_app(tmp, vlm=None, clock=None, **over):
    cfg = dict(cfgmod.DEFAULTS)
    cfg.update(data_dir=os.path.join(tmp, "data"), token=fake_token(), **over)
    return App(cfg, clock=clock or Clock(), vlm=vlm or FakeVlm())


class TmpCase:
    def make_tmp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name
