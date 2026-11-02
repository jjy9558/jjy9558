import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

import helpers
from helpers import FakeVlm, jpeg, make_app, obj
from objmem.server import make_server

NOPROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ServerCase(unittest.TestCase, helpers.TmpCase):
    def setUp(self):
        self.vlm = FakeVlm()
        self.app = make_app(self.make_tmp(), vlm=self.vlm, max_frame_bytes=2000, hash_threshold=4, quiet=True)
        self.srv = make_server(self.app, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.t.start()
        self.addCleanup(self._stop)
        self.tok = self.app.cfg["token"]

    def _stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, method, path, data=None, token="default", headers=None):
        h = dict(headers or {})
        if token == "default":
            token = self.tok
        if token is not None:
            h["X-ObjMem-Token"] = token
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method, headers=h)
        try:
            with NOPROXY.open(req, timeout=10) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def post_frame(self, data, camera="거실", hash_=None, **kw):
        q = "camera=" + urllib.parse.quote(camera)
        if hash_ is not None:
            q += "&hash=" + hash_
        return self.call("POST", "/api/frame?" + q, data, **kw)


class ApiTest(ServerCase):
    def test_binds_loopback_by_default(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        self.assertEqual(helpers.cfgmod.DEFAULTS["host"], "127.0.0.1")

    def test_auth_required(self):
        for tok in (None, "wrong-token-value", ""):
            code, _, _ = self.post_frame(jpeg(), token=tok)
            self.assertEqual(code, 401)
        self.assertEqual(self.call("GET", "/api/ask?q=x", token=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/status", token="nope")[0], 401)
        self.assertEqual(self.app.store.stats()["frames"], 0)
        code, _, _ = self.call("GET", "/api/ping", token=None, headers={"Authorization": "Bearer " + self.tok})
        self.assertEqual(code, 200)

    def test_phone_page_is_public_without_secrets(self):
        code, body, h = self.call("GET", "/", token=None)
        self.assertEqual(code, 200)
        self.assertIn("text/html", h["Content-Type"])
        self.assertIn(b"getUserMedia", body)
        self.assertNotIn(self.tok.encode(), body)

    def test_store_frame_and_ask(self):
        self.vlm.results = [{"objects": [obj("리모컨", "소파 왼쪽 쿠션 위", 0.9)], "scene": "거실"}]
        code, body, _ = self.post_frame(jpeg(), "거실", "%016x" % 0xFF)
        r = json.loads(body)
        self.assertEqual((code, r["status"], r["described"], r["objects"]), (200, "stored", True, 1))
        self.assertNotIn("path", r)
        code, body, _ = self.call("GET", "/api/ask?q=" + urllib.parse.quote("리모컨 어디 뒀지?"))
        a = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(a["found"])
        self.assertIn("소파 왼쪽 쿠션 위", a["answer"])
        self.assertEqual(a["sightings"][0]["camera"], "거실")
        fid = a["sightings"][0]["frame_id"]
        code, body, h = self.call("GET", "/api/photo?id=%d" % fid)
        self.assertEqual((code, h["Content-Type"]), (200, "image/jpeg"))
        self.assertEqual(body[:2], b"\xff\xd8")
        self.assertEqual(self.call("GET", "/api/photo?id=%d" % fid, token=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/photo?id=9999")[0], 404)
        self.assertEqual(self.call("GET", "/api/photo?id=abc")[0], 400)

    def test_similar_frame_skipped(self):
        self.post_frame(jpeg(), "거실", "%016x" % 0)
        code, body, _ = self.post_frame(jpeg(), "거실", "%016x" % 1)
        self.assertEqual(json.loads(body)["status"], "similar")
        self.assertEqual(self.vlm.calls, 1)

    def test_size_limit(self):
        big = b"\xff\xd8" + b"a" * 5000 + b"\xff\xd9"
        code, body, _ = self.post_frame(big)
        self.assertEqual(code, 413)
        self.assertEqual(self.app.store.stats()["frames"], 0)

    def test_oversize_rejected_without_reading_body(self):
        import socket
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(("POST /api/frame HTTP/1.1\r\nHost: x\r\nX-ObjMem-Token: %s\r\nContent-Length: 999999999\r\n\r\n" % self.tok).encode())
        self.assertIn(b"413", s.recv(200))
        s.close()

    def test_bad_requests(self):
        self.assertEqual(self.post_frame(b"not a jpeg at all")[0], 415)
        import socket
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(("POST /api/frame HTTP/1.1\r\nHost: x\r\nX-ObjMem-Token: %s\r\n\r\n" % self.tok).encode())
        self.assertIn(b"411", s.recv(200))
        s.close()
        self.assertEqual(self.call("POST", "/api/nope", b"x")[0], 404)
        self.assertEqual(self.call("GET", "/api/ask?q=")[0], 400)
        self.assertEqual(self.call("GET", "/api/ask?q=" + urllib.parse.quote("가" * 400))[0], 400)
        self.assertEqual(self.call("GET", "/etc/passwd", token=None)[0], 404)

    def test_privacy_over_http(self):
        self.app.cfg["privacy_hours"] = ["00:00-00:00"]       # 하루 종일
        code, body, _ = self.post_frame(jpeg())
        self.assertEqual(json.loads(body)["status"], "privacy")
        self.assertEqual(self.app.store.stats()["frames"], 0)
        self.assertEqual(self.vlm.calls, 0)

    def test_status(self):
        code, body, _ = self.call("GET", "/api/status")
        self.assertEqual(json.loads(body)["frames"], 0)


class TokenRequiredTest(unittest.TestCase, helpers.TmpCase):
    def test_refuses_empty_or_short_token(self):
        app = make_app(self.make_tmp())
        for t in ("", "abc"):
            app.cfg["token"] = t
            with self.assertRaises(ValueError):
                make_server(app, "127.0.0.1", 0)


if __name__ == "__main__":
    unittest.main()
