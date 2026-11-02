import copy
import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.config import build_hub_config  # noqa: E402
from jarvis.hub import Hub, make_server  # noqa: E402
from jarvis.store import Store  # noqa: E402

logging.getLogger("jarvis").setLevel(logging.CRITICAL + 1)

TOKENS = {"minipc": "tok-minipc-0123456789", "main": "tok-main-0123456789ab", "s21": "tok-s21-0123456789abc",
          "guest": "tok-guest-0123456789a"}

BASE = {
    "db": ":memory:",
    "devices": {
        "minipc": {"token": TOKENS["minipc"], "label": "미니PC", "worker": True, "caps": ["lan", "speaker"],
                   "trusted": True},
        "main": {"token": TOKENS["main"], "label": "5080", "worker": True,
                 "caps": ["gpu", "llm", "windows", "claude", "test"], "trusted": True},
        "s21": {"token": TOKENS["s21"], "label": "S21", "approve": True},
        "guest": {"token": TOKENS["guest"], "label": "손님", "submit": ["llm.chat"]},
    },
    "wake": {"enabled": True, "mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.0.255", "label": "5080"},
    "notify": {"say_job": True, "say_events": True},
    "presence": {"offline_after_seconds": 30},
    "queue": {"retry_delay_seconds": 0},
}


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class FakeNtfy:
    def __init__(self):
        self.pushed = []

    def push(self, title, message, priority=None, tags=None):
        self.pushed.append((title, message))
        return True


def make_hub(overrides=None, clock=None, today=None):
    from jarvis.config import deep_merge
    cfg = build_hub_config(deep_merge(copy.deepcopy(BASE), overrides or {}))
    clock = clock or FakeClock()
    store = Store(":memory:", clock=clock)
    return Hub(cfg, store=store, clock=clock, ntfy=FakeNtfy(), today=today)


def register_worker(hub, device, caps, types):
    hub.seen(device, caps, types)


class ServerThread:
    def __init__(self, hub):
        self.srv = make_server(hub, "127.0.0.1", 0, quiet=True)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
