import tempfile
import unittest
from datetime import datetime

from nagger.config import load_config
from nagger.engine import Nagger
from nagger.fakes import FakeClock, FakePlatform
from nagger.outputs import Notifier


class Sink:
    """Notifier 대신 메시지를 모으는 출력."""
    min_level = 0

    def __init__(self):
        self.msgs = []

    def send(self, msg, now):
        self.msgs.append((now, msg))

    def texts(self, module=None):
        return [m.text for _, m in self.msgs if module in (None, m.module)]


class Base(unittest.TestCase):
    def make(self, start=datetime(2026, 11, 2, 1, 55), overrides=None):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        ov = {"state_dir": self._tmp.name, "games": ["game.exe"], "work_apps": ["code.exe"],
              "outputs": {"console": {"enabled": False}, "toast": {"enabled": False}}}
        for k, v in (overrides or {}).items():
            if isinstance(v, dict) and isinstance(ov.get(k), dict):
                ov[k].update(v)
            else:
                ov[k] = v
        self.cfg = load_config(None, ov)
        self.clock = FakeClock(start)
        self.plat = FakePlatform()
        self.sink = Sink()
        self.n = Nagger(self.cfg, self.plat, self.clock, Notifier([self.sink]))
        return self.n

    def step(self, **adv):
        self.clock.advance(**adv)
        return self.n.tick()
