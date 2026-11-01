import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sidekick.config import build_config  # noqa: E402


class FakeClock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += dt.timedelta(**kw)


class FakeMono:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeLLM:
    def __init__(self, reply="", fail=False):
        self.reply = reply
        self.fail = fail
        self.calls = []

    def ask(self, prompt, system=None, **kw):
        self.calls.append(("text", prompt))
        if self.fail:
            raise RuntimeError("down")
        return self.reply

    def ask_with_image(self, prompt, image, system=None, **kw):
        self.calls.append(("image", prompt, image))
        if self.fail:
            raise RuntimeError("down")
        return self.reply


def config(**sections):
    raw = {"server": {"token": "secret-token"}, "alarm": {"emergency_code": "4242"}}
    for k, v in sections.items():
        raw.setdefault(k, {}).update(v)
    return build_config(raw)
