import http.client
import unittest
from unittest import mock

from phonehome.proxy import safe_rest, split_service_path
from phonehome.server import COOKIE, make_passthrough_servers, session_value
from phonehome._jjkit import httpkit
from phonehome._jjkit.httpkit import serve_in_thread

from helpers import FakeBackend, Harness, dead_url, fake_token

PAGE = (b'<!doctype html><html><head><title>x</title></head><body><a href="/help">h</a>'
        b'<script>fetch("/api/events?since=0");api(\'/api/config\');var u=`/api/x`;'
        b'var keep="//cdn.example/api/";</script></body></html>')


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.side_tok = fake_token("side")
        self.jar_tok = fake_token("jar")
        self.side = FakeBackend({
            ("GET", "/api/x"): (200, {"ok": True}, None),
            ("POST", "/api/x"): (200, {"posted": True}, None),
            ("GET", "/api/ping"): (200, {"ok": True}, None),
            ("GET", "/"): (200, PAGE, "text/html; charset=utf-8"),
        })
        self.jar = FakeBackend({("GET", "/api/ping"): (200, {"ok": True}, None)})
        services = {
            "sidekick": {"url": self.side.url, "token": self.side_tok},
            "jarvis": {"url": self.jar.url, "token": self.jar_tok},
            "voice-journal": {"url": dead_url()},
        }
        self.h = Harness(services, server={"max_body_mb": 0.01})

    def tearDown(self):
        self.h.close()
        self.side.close()
        self.jar.close()

    # (1) 토큰이 틀리면 401, 상수 시간 비교
    def test_wrong_token_401(self):
        for tok in (fake_token("wrong"), None):
            code, _ = self.h.json("GET", "/s/sidekick/api/x", token=tok)
            self.assertEqual(code, 401)
            code, _ = self.h.json("GET", "/api/health", token=tok)
            self.assertEqual(code, 401)
        code, _, body = self.h.request("GET", "/", token=None)
        self.assertEqual(code, 401)
        self.assertIn("토큰".encode("utf-8"), body)
        self.assertEqual(self.side.requests, [])  # 인증 전에 뒤쪽으로 아무것도 안 간다

    def test_constant_time_compare(self):
        with mock.patch.object(httpkit.hmac, "compare_digest", wraps=httpkit.hmac.compare_digest) as cd:
            code, _ = self.h.json("GET", "/api/tiles", token=fake_token("nope"))
        self.assertEqual(code, 401)
        self.assertTrue(cd.called)

    def test_bearer_and_cookie_login(self):
        code, _ = self.h.json("GET", "/api/tiles", token=None, headers={"Authorization": "Bearer " + self.h.token})
        self.assertEqual(code, 200)
        code, hdrs, _ = self.h.request("POST", "/login", {"token": fake_token("bad")}, token=None)
        self.assertEqual(code, 401)
        code, hdrs, _ = self.h.request("POST", "/login", {"token": self.h.token}, token=None)
        self.assertEqual(code, 200)
        sc = hdrs["Set-Cookie"]
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Strict", sc)
        self.assertNotIn(self.h.token, sc)  # 쿠키에는 토큰 원문이 아니라 HMAC 값
        cookie = sc.split(";", 1)[0]
        code, _ = self.h.json("GET", "/s/sidekick/api/x", token=None, headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        fwd = self.side.requests[-1]["headers"]
        self.assertNotIn("Cookie", fwd)
        code, _ = self.h.json("GET", "/api/tiles", token=None, headers={"Cookie": COOKIE + "=" + "0" * 64})
        self.assertEqual(code, 401)

    def test_login_requires_json(self):
        code, _, _ = self.h.request("POST", "/login", b"token=" + self.h.token.encode(), token=None,
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(code, 415)

    def test_login_throttle(self):
        for _ in range(10):
            self.h.request("POST", "/login", {"token": fake_token("bad")}, token=None)
        code, _, _ = self.h.request("POST", "/login", {"token": self.h.token}, token=None)
        self.assertEqual(code, 429)

    # (2) 경로 매핑 + 뒤쪽 토큰 붙이기 + 클라이언트 토큰 안 넘김
    def test_prefix_maps_and_injects_backend_token(self):
        code, body = self.h.json("GET", "/s/sidekick/api/x?a=1&b=%ED%95%9C", headers={
            "X-Sidekick-Token": fake_token("evil"), "X-Jarvis-Token": fake_token("evil2"),
            "Authorization": "Bearer " + self.h.token, "X-Custom": "keep"})
        self.assertEqual((code, body), (200, {"ok": True}))
        r = self.side.requests[-1]
        self.assertEqual(r["path"], "/api/x?a=1&b=%ED%95%9C")
        hdrs = r["headers"]
        self.assertEqual(hdrs["X-Sidekick-Token"], self.side_tok)
        self.assertNotIn("X-Jarvis-Token", hdrs)
        self.assertNotIn("Authorization", hdrs)
        self.assertNotIn("X-Phone-Token", hdrs)
        self.assertEqual(hdrs["X-Custom"], "keep")
        for v in hdrs.values():
            self.assertNotIn(self.h.token, v)

    def test_post_body_forwarded_and_backend_cookie_dropped(self):
        code, hdrs, data = self.h.request("POST", "/s/sidekick/api/x", {"hi": "안녕"})
        self.assertEqual(code, 200)
        self.assertEqual(self.side.requests[-1]["body"].decode("utf-8"), '{"hi": "\\uc548\\ub155"}')
        self.assertNotIn("Set-Cookie", hdrs)

    def test_jarvis_extra_headers(self):
        self.h.json("GET", "/s/jarvis/api/ping", headers={"X-Jarvis-Device": "evil"})
        hdrs = self.jar.requests[-1]["headers"]
        self.assertEqual(hdrs["X-Jarvis-Device"], "s21")
        self.assertEqual(hdrs["X-Jarvis-Token"], self.jar_tok)

    # (3) 뒤쪽이 죽으면 502, 타일 빨간 점(health state=down)
    def test_dead_backend_502_and_red_dot(self):
        code, body = self.h.json("GET", "/s/voice-journal/health")
        self.assertEqual(code, 502)
        self.assertIn("voice-journal", body["error"])
        code, body = self.h.json("GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["services"]["voice-journal"]["state"], "down")
        self.assertFalse(body["services"]["voice-journal"]["ok"])
        self.assertEqual(body["services"]["sidekick"]["state"], "up")
        _, _, page = self.h.request("GET", "/")
        self.assertIn(b"dot.down", page)  # 빨간 점 스타일
        self.assertIn(b'"state"', page.replace(b"s.state", b'"state"'))

    def test_health_auth_state(self):
        self.side.routes[("GET", "/api/ping")] = (401, {"error": "no"}, None)
        _, body = self.h.json("GET", "/api/health")
        self.assertEqual(body["services"]["sidekick"]["state"], "auth")

    # (4) 경로 탈출 / 미등록 서비스 404
    def test_path_escape_and_unknown_404(self):
        for p in ("/s/../etc/passwd", "/s/..", "/s/%2e%2e/x", "/s/sidekick/../jarvis/api/ping",
                  "/s/sidekick/%2e%2e/%2e%2e/x", "/s/sidekick/%252e%252e/x", "/s/sidekick//etc",
                  "/s/sidekick/a%5cb", "/s/nope/api/x", "/s/SIDEKICK/api/x", "/s//api/x"):
            code, _ = self.h.json("GET", p)
            self.assertEqual(code, 404, p)
        self.assertEqual(self.side.requests, [])
        self.assertEqual(self.jar.requests, [])

    def test_no_trailing_slash_redirect(self):
        code, hdrs, _ = self.h.request("GET", "/s/sidekick")
        self.assertEqual(code, 302)
        self.assertEqual(hdrs["Location"], "/s/sidekick/")

    # (5) 본문 크기 제한
    def test_body_limit_413(self):
        big = b"x" * (11 * 1024)  # 한도 0.01MB ≈ 10KB
        code, _, _ = self.h.request("POST", "/s/sidekick/api/x", big,
                                    headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(code, 413)
        self.assertEqual([r for r in self.side.requests if r["method"] == "POST"], [])
        code, _, _ = self.h.request("POST", "/s/sidekick/api/x", b"x" * 1000,
                                    headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(code, 200)

    # (6, D-2) HTML 안의 /api/ 절대경로를 prefix 모드에서 고쳐 쓴다
    def test_html_rewrite(self):
        code, hdrs, data = self.h.request("GET", "/s/sidekick/")
        self.assertEqual(code, 200)
        html = data.decode("utf-8")
        self.assertIn('fetch("/s/sidekick/api/events?since=0")', html)
        self.assertIn("api('/s/sidekick/api/config')", html)
        self.assertIn("`/s/sidekick/api/x`", html)
        self.assertIn('href="/s/sidekick/help"', html)
        self.assertIn('"//cdn.example/api/"', html)
        self.assertIn('localStorage.setItem', html)  # 토큰 칸에 가짜 값(서버가 버림)
        self.assertLess(html.index("localStorage"), html.index("fetch("))
        self.assertEqual(int(hdrs["Content-Length"]), len(data))

    def test_location_rewrite(self):
        b = self.h.app.backends["sidekick"]
        self.assertEqual(b.rewrite_location("/api/y"), "/s/sidekick/api/y")
        self.assertEqual(b.rewrite_location("//evil/x"), "//evil/x")
        self.assertEqual(b.rewrite_location("http://a/b"), "http://a/b")

    def test_go_links(self):
        code, hdrs, _ = self.h.request("GET", "/go/sidekick")
        self.assertEqual((code, hdrs["Location"]), (302, "/s/sidekick/"))
        code, _, _ = self.h.request("GET", "/go/voice-journal")  # 페이지 없는 서비스
        self.assertEqual(code, 404)


class PassthroughTests(unittest.TestCase):
    def test_passthrough_port(self):
        tok = fake_token("sw")
        be = FakeBackend({("GET", "/api/status"): (200, {"ok": 1}, None),
                          ("GET", "/"): (200, b'<html><head></head><script>fetch("/api/status")</script></html>',
                                         "text/html")})
        h = Harness({"sound-watch": {"url": be.url, "token": tok, "path_mode": "passthrough",
                                     "listen_port": 18999}})
        extras = make_passthrough_servers(h.app, port_override=lambda n: 0)
        try:
            for x in extras:
                serve_in_thread(x)
            port = extras[0].server_address[1]

            def get(path, headers):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                c.request("GET", path, headers=headers)
                r = c.getresponse()
                d = r.read()
                c.close()
                return r.status, d

            self.assertEqual(get("/api/status", {})[0], 401)
            cookie = "%s=%s" % (COOKIE, session_value(h.token))
            code, d = get("/api/status", {"Cookie": cookie, "X-SoundWatch-Token": "x"})
            self.assertEqual(code, 200)
            self.assertEqual(be.requests[-1]["path"], "/api/status")
            self.assertEqual(be.requests[-1]["headers"]["X-SoundWatch-Token"], tok)
            code, d = get("/", {"Cookie": cookie})
            self.assertIn(b'fetch("/api/status")', d)  # passthrough는 고쳐 쓰지 않는다
            self.assertEqual(get("/a/../../x", {"Cookie": cookie})[0], 404)
            # prefix 경로로는 열리지 않는다
            self.assertEqual(h.json("GET", "/s/sound-watch/api/status")[0], 404)
            code, hdrs, _ = h.request("GET", "/go/sound-watch", headers={"Host": "phone.local:8790"})
            self.assertEqual(hdrs["Location"], "http://phone.local:18999/")
        finally:
            for x in extras:
                x.shutdown()
                x.server_close()
            h.close()
            be.close()


class PathUnitTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_service_path("/s/a/b/c?x=1"), ("a", "/b/c", "x=1"))
        self.assertEqual(split_service_path("/s/a"), ("a", None, ""))
        self.assertIsNone(split_service_path("/x/a/b"))
        self.assertIsNone(split_service_path("/s/a/./b"))
        self.assertTrue(safe_rest("/api/x%20y"))
        self.assertFalse(safe_rest("/api/%2E%2E/x"))
        self.assertFalse(safe_rest("/api/x%00"))


if __name__ == "__main__":
    unittest.main()
