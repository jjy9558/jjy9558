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


class LiveServer:
    """테스트용: 포트 0으로 실제 HTTP 서버를 띄운다."""

    def __init__(self, app):
        import threading
        from sidekick.server import make_server
        self.app = app
        self.srv = make_server(app, "127.0.0.1", 0, quiet=True)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, path, body=None, token="secret-token"):
        import json
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET")
        if token:
            req.add_header("X-Sidekick-Token", token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def free_port():
    """닫힌(아무도 안 듣는) 포트 번호."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
