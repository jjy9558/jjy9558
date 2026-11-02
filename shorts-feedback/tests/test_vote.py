import http.client
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from shortsfb import vote
from shortsfb._jjkit.httpkit import serve_in_thread

from helpers import png_bytes, write_pngs


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class VoteServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        imgs = write_pngs(Path(self.tmp.name), 3)
        self.poll = vote.create_poll(self.data, imgs, "어느 게 좋아요?", ["A안", "B안", "C안"])
        self.clock = Clock()
        self.start()

    def start(self, **kw):
        self.srv = vote.build_server(self.data, self.poll, port=0, clock=self.clock, **kw)
        serve_in_thread(self.srv)
        self.port = self.srv.server_address[1]
        self.base = "/v/%s/" % self.poll["token"]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, r.read(), r
        finally:
            c.close()

    def post_vote(self, choice, cookie="", json_body=False):
        h = {}
        if cookie:
            h["Cookie"] = cookie
        if json_body:
            h["Content-Type"] = "application/json"
            body = json.dumps({"choice": choice})
        else:
            h["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode({"choice": choice})
        return self.req("POST", self.base + "vote", body, h)

    def result(self):
        return vote.tally(self.poll, vote.read_votes(vote.polls_dir(self.data) / self.poll["id"] / "votes.jsonl"))

    # D-5 (1)
    def test_same_cookie_counts_once(self):
        st, body, resp = self.post_vote("c1")
        self.assertEqual(st, 200)
        cookie = resp.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        pair = cookie.split(";")[0]
        st, body, _ = self.post_vote("c2", cookie=pair)
        self.assertEqual(st, 200)
        self.assertIn("이미 투표".encode(), body)
        res = self.result()
        self.assertEqual(res["n"], 1)
        self.assertEqual({r["id"]: r["votes"] for r in res["rows"]}, {"c1": 1, "c2": 0, "c3": 0})

    def test_json_vote_reports_counted(self):
        st, body, resp = self.post_vote("c3", json_body=True)
        self.assertEqual(json.loads(body), {"ok": True, "counted": True})
        pair = resp.getheader("Set-Cookie").split(";")[0]
        st, body, _ = self.post_vote("c3", cookie=pair, json_body=True)
        self.assertEqual(json.loads(body), {"ok": True, "counted": False})

    def test_different_cookies_count_separately(self):
        self.post_vote("c1")
        self.post_vote("c1")
        self.assertEqual(self.result()["n"], 2)

    def test_missing_or_wrong_token_is_403(self):
        for path in ("/v/", "/v/wrongtoken/", "/v/wrongtoken/vote", "/v//"):
            st, _, _ = self.req("GET", path)
            self.assertEqual(st, 403, path)
        st, _, _ = self.req("POST", "/v/wrongtoken/vote", urlencode({"choice": "c1"}),
                            {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 403)
        self.assertEqual(self.result()["n"], 0)
        st, _, _ = self.req("GET", "/")
        self.assertEqual(st, 404)

    def test_bad_choice_400_and_unknown_subpath_404(self):
        self.assertEqual(self.post_vote("zzz")[0], 400)
        self.assertEqual(self.req("GET", self.base + "result")[0], 404)  # 결과는 웹으로 내보내지 않는다
        self.assertEqual(self.result()["n"], 0)

    def test_redirect_adds_trailing_slash(self):
        st, _, resp = self.req("GET", "/v/" + self.poll["token"])
        self.assertEqual(st, 302)
        self.assertEqual(resp.getheader("Location"), self.base)

    def test_page_is_self_contained(self):
        st, body, resp = self.req("GET", self.base)
        self.assertEqual(st, 200)
        page = body.decode("utf-8")
        self.assertEqual(page.count("data:image/png;base64,"), 3)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("<script", page)
        self.assertNotIn("<link", page)
        self.assertIn("default-src 'none'", resp.getheader("Content-Security-Policy"))
        self.assertNotIn("A안", page)  # 후보 이름은 투표자에게 보이지 않는다
        self.assertIn("어느 게 좋아요?", page)

    def test_post_rate_limit_per_ip(self):
        for _ in range(6):
            self.assertEqual(self.post_vote("c1")[0], 200)
        self.assertEqual(self.post_vote("c1")[0], 429)
        self.clock.t += 61
        self.assertEqual(self.post_vote("c1")[0], 200)

    def test_bad_token_attempts_are_limited(self):
        codes = [self.req("GET", "/v/guess%d/" % i)[0] for i in range(12)]
        self.assertEqual(codes[:10], [403] * 10)
        self.assertEqual(codes[10:], [429, 429])

    def test_vote_file_has_no_ip_or_raw_cookie(self):
        _, _, resp = self.post_vote("c1")
        raw_cookie = resp.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]
        text = (vote.polls_dir(self.data) / self.poll["id"] / "votes.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("127.0.0.1", text)
        self.assertNotIn(raw_cookie, text)

    def test_votes_survive_restart_without_double_count(self):
        _, _, resp = self.post_vote("c1")
        pair = resp.getheader("Set-Cookie").split(";")[0]
        self.srv.shutdown()
        self.srv.server_close()
        self.start()
        self.post_vote("c2", cookie=pair)
        self.assertEqual(self.result()["n"], 1)


class VoteLogicCase(unittest.TestCase):
    def poll(self):
        return {"id": "abcd1234", "question": "Q", "candidates": [
            {"id": "c1", "label": "A"}, {"id": "c2", "label": "B"}]}

    # D-5 (2)
    def test_small_sample_warning_and_percent(self):
        votes = [{"v": "h%d" % i, "c": "c1" if i < 3 else "c2"} for i in range(5)]
        res = vote.tally(self.poll(), votes)
        self.assertEqual(res["n"], 5)
        self.assertEqual([r["pct"] for r in res["rows"]], [60.0, 40.0])
        self.assertTrue(any("표본 5명(10 미만)" in w for w in res["warnings"]))
        text = vote.format_result(res)
        self.assertIn("60.0%", text)
        self.assertIn("경고:", text)

    def test_no_small_sample_warning_at_ten_and_wide_gap(self):
        votes = [{"v": "h%d" % i, "c": "c1" if i < 95 else "c2"} for i in range(100)]
        res = vote.tally(self.poll(), votes)
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["winner"], "A")
        res10 = vote.tally(self.poll(), [{"v": "h%d" % i, "c": "c1"} for i in range(10)])
        self.assertFalse(any("10 미만" in w for w in res10["warnings"]))

    def test_overlapping_interval_warning(self):
        votes = [{"v": "h%d" % i, "c": "c1" if i < 12 else "c2"} for i in range(22)]
        res = vote.tally(self.poll(), votes)
        self.assertTrue(any("신뢰 구간이 겹칩니다" in w for w in res["warnings"]))

    def test_empty_poll(self):
        res = vote.tally(self.poll(), [])
        self.assertEqual(res["n"], 0)
        self.assertIsNone(res["winner"])
        self.assertTrue(res["warnings"])

    def test_wilson_bounds(self):
        lo, hi = vote.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.237, places=2)
        self.assertAlmostEqual(hi, 0.763, places=2)
        self.assertEqual(vote.wilson(0, 0), (0.0, 1.0))

    def test_duplicate_rows_in_file_count_once(self):
        votes = [{"v": "same", "c": "c1"}, {"v": "same", "c": "c2"}, {"v": "x", "c": "bogus"}]
        self.assertEqual(vote.tally(self.poll(), votes)["n"], 1)

    def test_create_validates_images(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            good = write_pngs(d, 2)
            bad = d / "x.png"
            bad.write_bytes(b"not an image")
            with self.assertRaises(ValueError):
                vote.create_poll(d / "data", [good[0], bad])
            with self.assertRaises(ValueError):
                vote.create_poll(d / "data", [good[0]])  # 후보 1장
            with self.assertRaises(ValueError):
                vote.create_poll(d / "data", good, labels=["only one"])
            self.assertFalse((d / "data" / "polls").exists())  # 실패한 생성은 흔적을 남기지 않는다
            big = d / "big.png"
            big.write_bytes(png_bytes() + b"\0" * vote.MAX_IMAGE_BYTES)
            with self.assertRaises(ValueError):
                vote.create_poll(d / "data", [good[0], big])

    def test_load_poll_latest_and_errors(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            with self.assertRaises(ValueError):
                vote.load_poll(d / "data")
            imgs = write_pngs(d, 2)
            p = vote.create_poll(d / "data", imgs)
            self.assertEqual(vote.load_poll(d / "data")["id"], p["id"])
            self.assertEqual(vote.load_poll(d / "data", p["id"])["token"], p["token"])
            with self.assertRaises(ValueError):
                vote.load_poll(d / "data", "../etc")


if __name__ == "__main__":
    unittest.main()
