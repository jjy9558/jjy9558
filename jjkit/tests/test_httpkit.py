import json
import unittest
import urllib.error
import urllib.request

from jjkit import httpkit
from jjkit.httpkit import HttpError, TokenHandler, make_server, route, serve_in_thread, server_url

TOKEN = "t" + "0" * 15  # 테스트용 가짜 토큰


class Handler(TokenHandler):
    token_header = "X-Test-Token"
    max_body = 64

    @route("GET", "/", auth=False)
    def index(self, q):
        self.send_html(200, "<h1>홈</h1>")

    @route("GET", "/api/ping", auth=False)
    def ping(self, q):
        return 200, {"ok": True}

    @route("POST", "/api/echo")
    def echo(self, q):
        return 200, {"got": self.read_json(), "app": self.app}

    @route("GET", "/api/files/", prefix=True)
    def files(self, q):
        return 200, {"rest": self.route_rest}

    @route("GET", "/api/files/special/", prefix=True)
    def special(self, q):
        return 200, {"special": self.route_rest}

    @route("GET", "/api/text")
    def text(self, q):
        return 200, "평문"

    @route("GET", "/api/teapot")
    def teapot(self, q):
        raise HttpError(418, "주전자")

    @route("GET", "/api/boom")
    def boom(self, q):
        raise RuntimeError("/secret/path 노출 금지")


class QueryHandler(Handler):
    allow_query_token = True


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 환경 프록시 무시


def call(base, path, method="GET", body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
    try:
        with OPENER.open(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.srv = make_server(Handler, token=TOKEN, app="앱")
        serve_in_thread(self.srv)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = server_url(self.srv)

    def j(self, *a, **kw):
        code, data, headers = call(self.base, *a, **kw)
        return code, json.loads(data.decode("utf-8")), headers

    def test_defaults(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        self.assertNotEqual(self.srv.server_address[1], 0)

    def test_public_routes_and_headers(self):
        code, body, h = self.j("/api/ping")
        self.assertEqual((code, body), (200, {"ok": True}))
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        code, data, h = call(self.base, "/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", h["Content-Type"])
        self.assertEqual(data.decode("utf-8"), "<h1>홈</h1>")

    def test_auth_required(self):
        self.assertEqual(self.j("/api/echo", "POST", {"a": 1})[0], 401)
        self.assertEqual(self.j("/api/echo", "POST", {"a": 1}, {"X-Test-Token": "wrong"})[0], 401)
        code, body, _ = self.j("/api/echo", "POST", {"a": "가"}, {"X-Test-Token": TOKEN})
        self.assertEqual((code, body), (200, {"got": {"a": "가"}, "app": "앱"}))
        code, _, _ = self.j("/api/echo", "POST", {}, {"Authorization": "Bearer " + TOKEN})
        self.assertEqual(code, 200)

    def test_query_token_off_by_default(self):
        self.assertEqual(self.j("/api/text?token=" + TOKEN)[0], 401)

    def test_body_limit_and_bad_json(self):
        hdr = {"X-Test-Token": TOKEN}
        self.assertEqual(self.j("/api/echo", "POST", {"x": "a" * 100}, hdr)[0], 413)
        self.assertEqual(self.j("/api/echo", "POST", raw=b"{oops", headers=hdr)[0], 400)
        self.assertEqual(self.j("/api/echo", "POST", raw=b"[1]", headers=hdr)[0], 400)

    def test_prefix_routes_longest_first(self):
        hdr = {"X-Test-Token": TOKEN}
        self.assertEqual(self.j("/api/files/a/b.txt", headers=hdr)[1], {"rest": "a/b.txt"})
        self.assertEqual(self.j("/api/files/special/x", headers=hdr)[1], {"special": "x"})

    def test_text_errors_404_405(self):
        hdr = {"X-Test-Token": TOKEN}
        code, data, h = call(self.base, "/api/text", headers=hdr)
        self.assertEqual((code, data.decode("utf-8")), (200, "평문"))
        self.assertEqual(self.j("/api/teapot", headers=hdr)[:2], (418, {"error": "주전자"}))
        self.assertEqual(self.j("/nope")[0], 404)
        self.assertEqual(self.j("/api/ping", "POST", {})[0], 405)

    def test_500_hides_details(self):
        code, body, _ = self.j("/api/boom", headers={"X-Test-Token": TOKEN})
        self.assertEqual(code, 500)
        self.assertNotIn("secret", json.dumps(body))


class ConfigTests(unittest.TestCase):
    def test_empty_server_token_denies(self):
        srv = make_server(Handler, token="")
        serve_in_thread(srv)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        code, _, _ = call(server_url(srv), "/api/text", headers={"X-Test-Token": ""})
        self.assertEqual(code, 401)

    def test_query_token_opt_in(self):
        srv = make_server(QueryHandler, token=TOKEN)
        serve_in_thread(srv)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        code, _, _ = call(server_url(srv), "/api/text?token=" + TOKEN)
        self.assertEqual(code, 200)

    def test_public_bind_requires_token(self):
        with self.assertRaises(ValueError):
            make_server(Handler, host="0.0.0.0", token="")

    def test_helpers(self):
        self.assertTrue(httpkit.token_matches("abc", "abc"))
        self.assertFalse(httpkit.token_matches("", ""))
        self.assertFalse(httpkit.token_matches("abc", "abd"))
        self.assertGreaterEqual(len(httpkit.generate_token()), 30)
        self.assertTrue(httpkit.is_loopback_host("127.0.0.1"))
        self.assertTrue(httpkit.is_loopback_host("localhost"))
        self.assertFalse(httpkit.is_loopback_host("0.0.0.0"))
        self.assertFalse(httpkit.is_loopback_host(""))

    def test_routes_isolated_per_class(self):
        self.assertNotIn(("GET", "/api/ping"), TokenHandler._exact)
        self.assertIn(("GET", "/api/ping"), QueryHandler._exact)


if __name__ == "__main__":
    unittest.main()
