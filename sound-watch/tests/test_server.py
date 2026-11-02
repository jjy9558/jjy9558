import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import helpers  # noqa: F401
import synth
from soundwatch import audio, config
from soundwatch.notify import Notifier
from soundwatch.server import SoundWatchApp, make_server
from soundwatch.store import Store
from soundwatch.templates import Template, TemplateStore, sample_from_samples

TOKEN = "test-token"


def pcm(x):
    a = audio.floats_to_int16(x)
    return a.tobytes()


class Recorder(ThreadingHTTPServer):
    """가짜 ntfy / sidekick 수신 서버."""

    def __init__(self):
        self.requests = []
        rec = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                rec.requests.append({"path": self.path, "headers": dict(self.headers), "body": self.rfile.read(n)})
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        super().__init__(("127.0.0.1", 0), H)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server_address[1]


def make_cfg(**over):
    c = config.load_config("/nonexistent")
    c["server"]["token"] = TOKEN
    c["noise"]["threshold_db"] = 200.0   # 소음 이벤트는 여기서 관심 없음
    for k, v in over.items():
        c[k].update(v) if isinstance(v, dict) else c.__setitem__(k, v)
    return c


class ServerTestBase(unittest.TestCase):
    def start(self, cfg=None, templates=None, notifier=None):
        cfg = cfg or make_cfg()
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.app = SoundWatchApp(cfg, self.store, notifier, templates)
        self.srv = make_server(self.app)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.port = self.srv.server_address[1]

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def auth(self, extra=None):
        h = {"X-SoundWatch-Token": TOKEN}
        h.update(extra or {})
        return h


class ServerAuthTest(ServerTestBase):
    def test_binds_localhost_by_default(self):
        self.assertEqual(config.DEFAULTS["server"]["host"], "127.0.0.1")
        self.start()
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_empty_token_refused(self):
        c = make_cfg()
        c["server"]["token"] = ""
        with self.assertRaises(ValueError):
            SoundWatchApp(c, Store(":memory:"))

    def test_page_is_public_and_has_no_secret(self):
        self.start()
        st, hd, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("text/html", hd["Content-Type"])
        self.assertIn("Content-Security-Policy", hd)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertIn(b"AudioWorklet", body)
        self.assertIn(b"ScriptProcessor", body)    # 폴백
        self.assertIn("echoCancellation".encode(), body)

    def test_api_requires_token(self):
        self.start()
        for method, path in (("GET", "/api/ping"), ("GET", "/api/status"), ("POST", "/api/audio")):
            self.assertEqual(self.req(method, path, body=b"\0\0" if method == "POST" else None,
                                      headers={"Content-Length": "2"} if method == "POST" else {})[0], 401, path)
        self.assertEqual(self.req("GET", "/api/ping", headers={"X-SoundWatch-Token": "wrong"})[0], 401)
        self.assertEqual(self.req("GET", "/api/ping?token=" + TOKEN)[0], 401)      # 쿼리스트링 토큰은 불허
        self.assertEqual(self.req("GET", "/api/ping", headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.req("GET", "/api/ping", headers=self.auth())[0], 200)
        self.assertEqual(self.req("GET", "/api/ping", headers={"Authorization": "Bearer " + TOKEN})[0], 200)
        self.assertEqual(self.req("GET", "/api/nope", headers=self.auth())[0], 404)
        self.assertEqual(self.app.chunks, 0)

    def test_unauthorized_upload_is_not_ingested(self):
        self.start()
        st, _, _ = self.req("POST", "/api/audio?session=a&seq=0", body=pcm(synth.white(1, 0.1)),
                            headers={"X-SoundWatch-Token": "bad"})
        self.assertEqual(st, 401)
        self.assertEqual(self.app.chunks, 0)


class ServerIngestTest(ServerTestBase):
    def test_chunk_ingestion_updates_level_and_status(self):
        self.start()
        x = synth.tone(1000, 1.0, 0.1, 0, 0)
        for seq in range(3):
            st, _, body = self.req("POST", "/api/audio?session=s1&seq=%d&sr=16000" % seq, pcm(x), self.auth())
            self.assertEqual(st, 200)
            j = json.loads(body)
            self.assertTrue(j["ok"])
        self.assertAlmostEqual(j["level_db"], 96.99, delta=0.3)
        self.assertEqual(j["chunks"], 3)
        st, _, body = self.req("GET", "/api/status", headers=self.auth())
        s = json.loads(body)
        self.assertEqual(s["chunks"], 3)
        self.assertEqual(s["bytes"], 3 * 32000)
        self.assertFalse(s["calibrated"])
        self.assertAlmostEqual(s["summary"]["duration_s"], 3.0, delta=0.15)

    def test_bad_requests(self):
        c = make_cfg()
        c["server"]["max_body_kb"] = 64
        self.start(c)
        post = lambda q, b, extra=None: self.req("POST", "/api/audio" + q, b, self.auth(extra))
        self.assertEqual(post("?seq=0", b"\0\0\0")[0], 400)              # 홀수 바이트
        self.assertEqual(post("?seq=0", b"")[0], 400)                    # 빈 본문
        self.assertEqual(post("?seq=x", b"\0\0")[0], 400)
        self.assertEqual(post("?sr=100", b"\0\0")[0], 400)
        self.assertEqual(post("?seq=0", b"\0" * (65 * 1024))[0], 413)    # 너무 큼
        self.assertEqual(self.app.chunks, 0)
        self.assertEqual(post("?seq=0", pcm(synth.white(0.5, 0.01)))[0], 200)

    def test_other_sample_rate_is_resampled(self):
        self.start()
        x8 = [0.1 * __import__("math").sin(2 * 3.141592653589793 * 1000 * i / 8000) for i in range(8000)]
        st, _, body = self.req("POST", "/api/audio?session=a&seq=0&sr=8000", pcm(x8), self.auth())
        self.assertEqual(st, 200)
        self.assertAlmostEqual(json.loads(body)["level_db"], 96.99, delta=1.0)

    def test_new_session_and_stream_gap_do_not_crash(self):
        self.start()
        x = synth.white(1.0, 0.01)
        for sess in ("a", "a", "b"):
            self.assertEqual(self.req("POST", "/api/audio?session=%s&seq=0" % sess, pcm(x), self.auth())[0], 200)
        self.assertEqual(self.app.session, "b")


class EndToEndTest(ServerTestBase):
    def test_doorbell_chunks_trigger_detection_and_notifications(self):
        ntfy, sk = Recorder(), Recorder()
        self.addCleanup(ntfy.server_close)
        self.addCleanup(sk.server_close)
        self.addCleanup(ntfy.shutdown)
        self.addCleanup(sk.shutdown)
        cfg = make_cfg()
        cfg["notify"]["console"] = False
        cfg["notify"]["ntfy"].update(enabled=True, server=ntfy.url, topic="sw-test-topic")
        cfg["notify"]["sidekick"].update(enabled=True, url=sk.url)
        notifier = Notifier(cfg, env={"NTFY_TOKEN": "fake-ntfy-token", "SIDEKICK_TOKEN": "fake-sidekick-token"}, sync=True)
        s, _ = sample_from_samples(synth.pad(synth.doorbell(), 1.5, 1.5))
        with tempfile.TemporaryDirectory() as td:
            ts = TemplateStore(os.path.join(td, "t.json"))
            ts.templates["초인종"] = Template("초인종", [s], say="초인종이 울렸어요", cooldown=20)
            self.start(cfg, ts, notifier)
            x = synth.add_noise(synth.pad(synth.doorbell(), 3, 3), "pink", 10)
            for seq in range(0, len(x) // 16000):
                st, _, body = self.req("POST", "/api/audio?session=e2e&seq=%d" % seq, pcm(x[seq * 16000:(seq + 1) * 16000]), self.auth())
                self.assertEqual(st, 200)
            names = [d.name for d in self.app.engine.detections]
            self.assertEqual(names, ["초인종"])
            s = json.loads(self.req("GET", "/api/status", headers=self.auth())[2])
            self.assertEqual([r["message"] for r in s["recent"]], ["초인종이 울렸어요"])
            self.assertEqual(s["templates"], ["초인종"])
            rows = self.store.detections_recent()
            self.assertEqual(rows[0]["name"], "초인종")

        self.assertEqual(len(ntfy.requests), 1)
        r = ntfy.requests[0]
        self.assertEqual(r["path"], "/")
        self.assertEqual(r["headers"]["Authorization"], "Bearer fake-ntfy-token")
        payload = json.loads(r["body"].decode("utf-8"))
        self.assertEqual(payload["topic"], "sw-test-topic")
        self.assertEqual(payload["message"], "초인종이 울렸어요")
        self.assertEqual(len(sk.requests), 1)
        r = sk.requests[0]
        self.assertEqual(r["path"], "/api/say")
        self.assertEqual(r["headers"]["X-Sidekick-Token"], "fake-sidekick-token")
        self.assertEqual(json.loads(r["body"].decode("utf-8")), {"text": "초인종이 울렸어요", "module": "sound-watch"})

    def test_templates_hot_reload(self):
        clock = [1000.0]
        with tempfile.TemporaryDirectory() as td:
            ts = TemplateStore(os.path.join(td, "t.json")).load()
            self.store = Store(":memory:")
            self.addCleanup(self.store.close)
            app = SoundWatchApp(make_cfg(), self.store, None, ts, clock=lambda: clock[0])
            self.assertEqual(len(app.engine.matchers), 0)
            other = TemplateStore(ts.path)
            s, _ = sample_from_samples(synth.pad(synth.doorbell(), 1.5, 1.5))
            other.templates["초인종"] = Template("초인종", [s])
            other.save()
            clock[0] += 10
            app.ingest(pcm(synth.white(0.2, 0.001)), "s", 0, 16000)
            self.assertEqual(len(app.engine.matchers), 1)


class NotifierTest(unittest.TestCase):
    def test_failures_never_raise(self):
        cfg = config.load_config("/nonexistent")
        cfg["notify"]["console"] = False
        cfg["notify"]["ntfy"].update(enabled=True, server="http://127.0.0.1:9", topic="x")
        cfg["notify"]["sidekick"].update(enabled=True, url="http://127.0.0.1:9")
        n = Notifier(cfg, sync=True, env={})
        n.notify("t", "m")
        self.assertEqual(len(n.errors), 2)

    def test_async_worker_delivers(self):
        rec = Recorder()
        self.addCleanup(rec.server_close)
        self.addCleanup(rec.shutdown)
        cfg = config.load_config("/nonexistent")
        cfg["notify"]["ntfy"].update(enabled=True, server=rec.url, topic="t1")
        out = []
        n = Notifier(cfg, env={}, printer=out.append)
        n.notify("제목", "내용")
        deadline = time.time() + 5
        while not rec.requests and time.time() < deadline:
            time.sleep(0.02)
        n.close()
        self.assertEqual(len(rec.requests), 1)
        self.assertNotIn("Authorization", rec.requests[0]["headers"])
        self.assertEqual(out, ["[알림] 제목: 내용"])

    def test_disabled_channels_send_nothing(self):
        rec = Recorder()
        self.addCleanup(rec.server_close)
        self.addCleanup(rec.shutdown)
        cfg = config.load_config("/nonexistent")
        cfg["notify"]["console"] = False
        cfg["notify"]["ntfy"].update(enabled=False, server=rec.url, topic="t1")
        Notifier(cfg, sync=True).notify("a", "b")
        self.assertEqual(rec.requests, [])


if __name__ == "__main__":
    unittest.main()
