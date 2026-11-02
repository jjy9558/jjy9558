import unittest

import helpers  # noqa: F401
from objmem.vlm import VlmClient, VlmError, parse_description

GOOD = '{"objects":[{"name":"리모컨","where":"소파 왼쪽 쿠션 위","confidence":0.9}],"scene":"거실"}'


class ParseTest(unittest.TestCase):
    def test_plain(self):
        d = parse_description(GOOD)
        self.assertEqual(d["objects"][0]["name"], "리모컨")
        self.assertEqual(d["objects"][0]["where"], "소파 왼쪽 쿠션 위")
        self.assertEqual(d["scene"], "거실")

    def test_fence_and_chatter(self):
        d = parse_description("물론이죠!\n```json\n" + GOOD + "\n```\n끝")
        self.assertEqual(len(d["objects"]), 1)

    def test_think_block(self):
        d = parse_description("<think>{\"objects\": [bad</think>\n" + GOOD)
        self.assertEqual(d["objects"][0]["name"], "리모컨")

    def test_think_without_open_tag(self):
        d = parse_description("생각 중...</think>" + GOOD)
        self.assertEqual(len(d["objects"]), 1)

    def test_trailing_commas(self):
        d = parse_description('{"objects":[{"name":"지갑","where":"책상","confidence":0.8,},],"scene":"x",}')
        self.assertEqual(d["objects"][0]["name"], "지갑")

    def test_trailing_text_after_json(self):
        d = parse_description(GOOD + " 이상입니다 {끝}")
        self.assertEqual(len(d["objects"]), 1)

    def test_truncated_mid_string(self):
        d = parse_description('{"objects":[{"name":"리모컨","where":"소파 위","confidence":0.9},{"name":"열쇠","where":"현관 선')
        names = [o["name"] for o in d["objects"]]
        self.assertIn("리모컨", names)

    def test_truncated_mid_key(self):
        d = parse_description('{"objects":[{"name":"리모컨","where":"소파","confidence":0.9},{"name":"열쇠","wh')
        self.assertEqual([o["name"] for o in d["objects"]], ["리모컨", "열쇠"])   # 완결된 name 은 살림

    def test_truncated_after_objects(self):
        d = parse_description('{"objects":[{"name":"안경","where":"책상","confidence":0.7}],"scene":"방 전')
        self.assertEqual(d["objects"][0]["name"], "안경")

    def test_bare_list(self):
        d = parse_description('[{"name":"컵","where":"식탁"}]')
        self.assertEqual(d["objects"][0]["confidence"], 0.5)

    def test_confidence_variants(self):
        d = parse_description('{"objects":[{"name":"a","confidence":"80%"},{"name":"b","confidence":85},{"name":"c","confidence":"x"},{"name":"d","confidence":2.5}]}')
        c = [o["confidence"] for o in d["objects"]]
        self.assertEqual(c[0], 0.8)
        self.assertEqual(c[1], 0.85)
        self.assertEqual(c[2], 0.5)
        self.assertTrue(0 <= c[3] <= 1)

    def test_skips_bad_entries(self):
        d = parse_description('{"objects":[{"name":""},5,null,{"where":"x"},{"name":"모자"}],"scene":3}')
        self.assertEqual([o["name"] for o in d["objects"]], ["모자"])
        self.assertEqual(d["scene"], "")

    def test_empty_objects(self):
        self.assertEqual(parse_description('{"objects":[],"scene":"빈 방"}')["objects"], [])

    def test_garbage(self):
        for t in ("", "죄송합니다. 설명할 수 없습니다.", "<think>only thinking", "{{{"):
            with self.assertRaises(VlmError):
                parse_description(t)

    def test_client_ignores_proxy_env_and_talks_to_local_server(self):
        import json
        import os
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        seen = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen["url"] = body["messages"][0]["content"][1]["image_url"]["url"][:23]
                out = json.dumps({"choices": [{"message": {"content": GOOD}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = os.environ.get("http_proxy")
        os.environ["http_proxy"] = "http://127.0.0.1:9"      # 죽은 프록시: 쓰면 실패해야 함
        try:
            c = VlmClient("http://127.0.0.1:%d/v1" % srv.server_address[1], timeout=5)
            d = c.describe(helpers.jpeg())
        finally:
            srv.shutdown()
            srv.server_close()
            if old is None:
                os.environ.pop("http_proxy", None)
            else:
                os.environ["http_proxy"] = old
        self.assertEqual(d["objects"][0]["name"], "리모컨")
        self.assertEqual(seen["url"], "data:image/jpeg;base64,")
        self.assertEqual(VlmClient().base_url, "http://127.0.0.1:8080/v1")

    def test_client_error_is_vlmerror(self):
        with self.assertRaises(VlmError):
            VlmClient("http://127.0.0.1:9/v1", timeout=2).describe(helpers.jpeg())


if __name__ == "__main__":
    unittest.main()
