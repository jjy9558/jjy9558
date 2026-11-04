"""send_ntfy 가 jjkit notify(헤더 모드) 로 예전과 같은 요청을 보내는지 (로컬 가짜 서버)."""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import helpers  # noqa: F401  (sys.path 설정)
from devguard import wallet


class _H(BaseHTTPRequestHandler):
    seen = []
    code = 200

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        _H.seen.append((self.path, dict(self.headers), self.rfile.read(n)))
        self.send_response(_H.code)
        self.end_headers()

    def log_message(self, *a):
        pass


class SendNtfyTests(unittest.TestCase):
    def setUp(self):
        _H.seen, _H.code = [], 200
        self.srv = HTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_port

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_topic_url_body_and_headers(self):
        self.assertTrue(wallet.send_ntfy(self.base + "/my-topic", "devguard wallet", "본문 한글", "high"))
        path, headers, body = _H.seen[0]
        self.assertEqual(path, "/my-topic")
        self.assertEqual(body, "본문 한글".encode("utf-8"))
        self.assertEqual(headers["Title"], "devguard wallet")
        self.assertEqual(headers["Priority"], "4")
        self.assertEqual(headers["Tags"], "money_with_wings")

    def test_prefixed_topic_path(self):
        self.assertTrue(wallet.send_ntfy(self.base + "/pre/fix/topic/", "t", "b"))
        self.assertEqual(_H.seen[0][0], "/pre/fix/topic")

    def test_http_error_is_false(self):
        _H.code = 500
        self.assertFalse(wallet.send_ntfy(self.base + "/t", "t", "b"))

    def test_bad_urls_are_false(self):
        for u in ("", "not a url", "ftp://x/y", "http://127.0.0.1:%d" % self.srv.server_port):
            self.assertFalse(wallet.send_ntfy(u, "t", "b"), u)


if __name__ == "__main__":
    unittest.main()
