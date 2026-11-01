import datetime as dt
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from helpers import FakeClock, FakeLLM, config
from sidekick.config import ConfigError, validate
from sidekick.server import Sidekick, make_server

TOKEN = "secret-token"


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.ran = []

        def runner(argv, cwd=None, timeout=None):
            self.ran.append(argv)
            return 0, "테스트 2개 실패: import 오류."
        cfg = config(debugger={"mode": "command", "command": ["claude", "-p", "{prompt}"]},
                     butler={"providers": [{"name": "greeting", "text": "어서 와요."}]})
        self.clock = FakeClock(dt.datetime(2026, 11, 2, 7, 0))
        self.vision = FakeLLM('{"approved": false, "reason": "이불 속"}')
        self.app = Sidekick(cfg, vision=self.vision, clock=self.clock, runner=runner,
                            spawn=lambda fn, *a: fn(*a), transcriber=lambda audio: "아 왜 안돼")
        self.srv = make_server(self.app, "127.0.0.1", 0, quiet=True)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, path, body=None, token=TOKEN, raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET")
        if token:
            req.add_header("X-Sidekick-Token", token)
        if data is not None:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def j(self, *a, **kw):
        code, body = self.call(*a, **kw)
        return code, json.loads(body)

    def test_page_served_without_token(self):
        code, body = self.call("/", token=None)
        self.assertEqual(code, 200)
        self.assertIn("webkitSpeechRecognition".encode(), body)

    def test_token_required(self):
        self.assertEqual(self.call("/api/ping", token=None)[0], 401)
        self.assertEqual(self.call("/api/ping", token="wrong")[0], 401)
        self.assertEqual(self.j("/api/ping")[0], 200)
        req = urllib.request.Request(self.base + "/api/ping", headers={"Authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)

    def test_unknown_route_and_bad_json(self):
        self.assertEqual(self.call("/api/nope")[0], 404)
        self.assertEqual(self.call("/api/utterance", raw=b"not json")[0], 400)
        self.assertEqual(self.call("/api/utterance", body={"text": ""})[0], 400)

    def test_utterance_triggers_investigation_and_events(self):
        code, r = self.j("/api/utterance", {"text": "아 이거 왜 안 돼"})
        self.assertEqual((code, r["status"]), (200, "trigger"))
        self.assertEqual(self.j("/api/utterance", {"text": "에러 뭐야"})[1]["status"], "cooldown")
        self.assertEqual(self.j("/api/utterance", {"text": "배고프다"})[1]["status"], "ignored")
        self.assertEqual(len(self.ran), 1)
        _, ev = self.j("/api/events?since=0")
        self.assertEqual([e["text"] for e in ev["events"]], ["잠깐, 볼게요.", "테스트 2개 실패: import 오류."])
        _, ev2 = self.j("/api/events?since=%d" % ev["last_id"])
        self.assertEqual(ev2["events"], [])

    def test_long_poll_wakes_on_event(self):
        def later():
            time.sleep(0.3)
            self.app.events.say("안녕")
        threading.Thread(target=later).start()
        t0 = time.time()
        _, ev = self.j("/api/events?since=0&wait=5")
        self.assertLess(time.time() - t0, 4)
        self.assertEqual(ev["events"][0]["text"], "안녕")

    def test_transcribe_route(self):
        code, r = self.j("/api/transcribe", raw=b"\x1aE\xdf\xa3fake", ctype="audio/webm")
        self.assertEqual(code, 200)
        self.assertEqual((r["text"], r["status"]), ("아 왜 안돼", "trigger"))

    def test_alarm_routes(self):
        self.assertTrue(self.j("/api/alarm/status")[1]["ringing"])
        self.assertEqual(self.call("/api/alarm/check", {"image": "http://evil/x.png"})[0], 400)
        _, r = self.j("/api/alarm/check", {"image": "data:image/jpeg;base64,AAAA"})
        self.assertEqual(r["say"], "반려. 이불 속")
        self.assertTrue(self.j("/api/alarm/snooze", {})[1]["ok"])
        self.assertFalse(self.j("/api/alarm/status")[1]["ringing"])
        self.assertTrue(self.j("/api/alarm/emergency", {"code": "4242"})[1]["ok"])
        _, st = self.j("/api/alarm/set", {"time": "06:30"})
        self.assertEqual((st["time"], st["ringing"]), ("06:30", False))
        self.assertEqual(self.call("/api/alarm/set", {"time": "nope"})[0], 400)
        self.assertTrue(self.j("/api/alarm/test", {})[1]["ringing"])
        self.assertEqual(self.j("/api/alarm/answer", {"answer": "1"})[1]["ok"], False)

    def test_arrival_route_speaks(self):
        _, r = self.j("/api/arrival", {"source": "button"})
        self.assertEqual(r["say"], "어서 와요.")
        _, ev = self.j("/api/events?since=0")
        self.assertEqual(ev["events"][-1]["text"], "어서 와요.")
        self.assertTrue(self.j("/api/arrival", {"source": "motion"})[1]["cooldown"])

    def test_body_size_limit(self):
        self.app.cfg["server"]["max_body_mb"] = 0.0001
        self.assertEqual(self.call("/api/utterance", {"text": "x" * 500})[0], 400)

    def test_config_validation(self):
        with self.assertRaises(ConfigError):
            validate(config(server={"token": ""}))
        with self.assertRaises(ConfigError):
            validate(config(alarm={"emergency_code": ""}))
        validate(config())


if __name__ == "__main__":
    unittest.main()
