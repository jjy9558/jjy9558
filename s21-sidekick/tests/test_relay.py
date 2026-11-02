import unittest

from helpers import FakeMono, LiveServer, config
from sidekick.relay import Forwarder, clean_module, clean_say_text, forward_types
from sidekick.server import Sidekick


class SayEndpointTests(unittest.TestCase):
    def setUp(self):
        self.mono = FakeMono()
        self.app = Sidekick(config(say={"max_per_minute": 3, "min_interval_seconds": 0}),
                            monotonic=self.mono, spawn=lambda fn, *a: fn(*a))
        self.live = LiveServer(self.app)

    def tearDown(self):
        self.live.close()

    def test_auth_header_and_bearer(self):
        self.assertEqual(self.live.call("/api/say", {"text": "hi"}, token=None)[0], 401)
        self.assertEqual(self.live.call("/api/say", {"text": "hi"}, token="wrong")[0], 401)
        code, r = self.live.call("/api/say", {"text": "물 마실 시간이에요.", "module": "nagger-mombot"})
        self.assertEqual((code, r["ok"]), (200, True))
        ev = self.app.events.since(0)[-1]
        self.assertEqual((ev["kind"], ev["text"], ev["module"], ev["id"]),
                         ("say", "물 마실 시간이에요.", "nagger-mombot", r["id"]))

    def test_cleaning_and_validation(self):
        self.assertEqual(self.live.call("/api/say", {"text": " \x00\n "})[0], 400)
        self.assertEqual(self.live.call("/api/say", {"text": None})[0], 400)
        _, r = self.live.call("/api/say", {"text": "줄\r\n바꿈\x1b[31m 색‮", "module": "../../evil mod!"})
        self.assertEqual(r["text"], "줄 바꿈 [31m 색")
        self.assertEqual(self.app.events.since(0)[-1]["module"], "....evilmod")
        _, r = self.live.call("/api/say", {"text": "가" * 1000})
        self.assertEqual(len(r["text"]), 300)
        self.assertEqual(self.app.events.since(0)[-1]["module"], "external")

    def test_rate_limit(self):
        for _ in range(3):
            self.assertEqual(self.live.call("/api/say", {"text": "x"})[0], 200)
        code, r = self.live.call("/api/say", {"text": "x"})
        self.assertEqual(code, 429)
        self.assertGreater(r["retry_after"], 0)
        self.mono.t += 61
        self.assertEqual(self.live.call("/api/say", {"text": "x"})[0], 200)

    def test_disabled(self):
        self.app.cfg["say"]["enabled"] = False
        self.assertEqual(self.live.call("/api/say", {"text": "x"})[0], 403)

    def test_helpers(self):
        self.assertEqual(clean_say_text("a\tb"), "a b")
        self.assertEqual(clean_module(""), "external")


class ForwardTests(unittest.TestCase):
    def test_off_by_default(self):
        app = Sidekick(config(), spawn=lambda fn, *a: fn(*a))
        live = LiveServer(app)
        try:
            _, r = live.call("/api/config")
            self.assertEqual(r["forward_types"], [])
            code, r = live.call("/api/phone_event", {"type": "sigh"})
            self.assertEqual((code, r["ok"], r["forwarded"]), (200, False, 0))
        finally:
            live.close()

    def test_forwards_only_to_configured_targets(self):
        sent = []

        def post(url, body, headers, timeout):
            sent.append((url, body, headers))
            return 200
        targets = [{"url": "http://127.0.0.1:8799/api/event", "token_env": "NAG_TOK", "types": ["sigh", "pushup"]},
                   {"url": "http://127.0.0.1:9000/x", "token": "inline"}]  # types 생략 = ["sigh"]
        cfg = config()
        cfg["forward_events"] = targets
        mono = FakeMono()
        app = Sidekick(cfg, spawn=lambda fn, *a: fn(*a), forward_post=post, monotonic=mono)
        app.forwarder.environ = {"NAG_TOK": "nag-secret"}
        live = LiveServer(app)
        try:
            self.assertEqual(live.call("/api/phone_event", {"type": "sigh"}, token=None)[0], 401)
            _, r = live.call("/api/config")
            self.assertEqual(r["forward_types"], ["pushup", "sigh"])
            code, r = live.call("/api/phone_event", {"type": "sigh"})
            self.assertEqual((code, r["forwarded"]), (200, 2))
            self.assertEqual(sent[0], ("http://127.0.0.1:8799/api/event", {"type": "sigh", "n": 1},
                                       {"X-Sidekick-Token": "nag-secret"}))
            self.assertEqual(sent[1][2], {"X-Sidekick-Token": "inline"})
            self.assertEqual(live.call("/api/phone_event", {"type": "pushup"})[0], 429)  # 연타 방지
            mono.t += 1
            live.call("/api/phone_event", {"type": "pushup", "n": 3})
            self.assertEqual(sent[-1][:2], ("http://127.0.0.1:8799/api/event", {"type": "pushup", "n": 3}))
            self.assertEqual(live.call("/api/phone_event", {"type": "dishes"})[1]["forwarded"], 0)
            for bad in [{"type": "Sigh!"}, {"type": "sigh", "n": 0}, {"type": "sigh", "n": True},
                        {"type": 5}, {}]:
                self.assertEqual(live.call("/api/phone_event", bad)[0], 400, bad)
            # 폰이 url을 보내도 무시된다: 전송 대상은 설정에서만
            mono.t += 1
            live.call("/api/phone_event", {"type": "sigh", "url": "http://evil/"})
            self.assertTrue(all("evil" not in u for u, _, _ in sent))
        finally:
            live.close()

    def test_forwarder_swallows_errors(self):
        def post(url, body, headers, timeout):
            raise OSError("refused")
        f = Forwarder([{"url": "http://127.0.0.1:1/api/event"}], post=post, environ={})
        self.assertEqual(f.send("sigh"), [("http://127.0.0.1:1/api/event", "error: OSError")])
        self.assertEqual(forward_types([{"url": "u", "types": ["ok_type", "BAD TYPE"]}]), ["ok_type"])


if __name__ == "__main__":
    unittest.main()
