import base64
import os
import tempfile
import unittest
import urllib.request
from unittest import mock

from jjkit import llm, netutil
from jjkit.llm import LLMClient, LLMError, parse_json
from tests.fakes import FakeOpener, chat_reply

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class LocalUrlTests(unittest.TestCase):
    def test_local_and_private(self):
        for u in ("http://127.0.0.1:1234/v1", "http://localhost:8080", "http://192.168.0.5/v1",
                  "http://10.0.0.2", "http://172.20.1.1", "http://mini.local:1234", "http://[::1]:9/v1",
                  "http://169.254.1.1"):
            self.assertTrue(netutil.is_local_url(u), u)

    def test_remote(self):
        for u in ("https://api.openai.com/v1", "http://8.8.8.8", "", "not a url", "http://example.com"):
            self.assertFalse(netutil.is_local_url(u), u)

    def test_local_opener_disables_proxy(self):
        def proxy_maps(op):
            return [h.proxies for h in op.handlers if isinstance(h, urllib.request.ProxyHandler)]
        with mock.patch.dict(os.environ, {"http_proxy": "http://proxy.invalid:3128",
                                          "HTTP_PROXY": "http://proxy.invalid:3128"}):
            self.assertTrue(any(proxy_maps(netutil.opener_for("https://example.com"))))
            self.assertFalse(any(proxy_maps(netutil.opener_for("http://127.0.0.1:1234"))))

    def test_injected_opener_wins(self):
        fake = FakeOpener()
        self.assertIs(netutil.opener_for("https://example.com", fake), fake)


class ClientTests(unittest.TestCase):
    def test_remote_refused_by_default(self):
        with self.assertRaises(LLMError):
            LLMClient("https://api.example.com/v1")
        LLMClient("https://api.example.com/v1", allow_remote=True, opener=FakeOpener())

    def test_empty_url(self):
        with self.assertRaises(LLMError):
            LLMClient("")

    def test_chat_request_shape(self):
        fake = FakeOpener(chat_reply("안녕하세요"))
        key = "test-" + "k" * 6
        c = LLMClient("http://127.0.0.1:1234/v1/", model="m1", api_key=key, opener=fake, timeout=7)
        out = c.chat([{"role": "user", "content": "hi"}], max_tokens=50)
        self.assertEqual(out, "안녕하세요")
        req, timeout = fake.requests[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:1234/v1/chat/completions")
        self.assertEqual(timeout, 7)
        self.assertEqual(fake.header("Authorization"), "Bearer " + key)
        body = fake.last_json()
        self.assertEqual(body["model"], "m1")
        self.assertEqual(body["max_tokens"], 50)
        self.assertEqual(body["temperature"], 0.0)
        self.assertNotIn("response_format", body)

    def test_full_endpoint_kept(self):
        fake = FakeOpener(chat_reply("x"))
        LLMClient("http://127.0.0.1:1/v1/chat/completions", opener=fake).chat([])
        self.assertEqual(fake.last.full_url, "http://127.0.0.1:1/v1/chat/completions")

    def test_no_auth_header_without_key(self):
        fake = FakeOpener(chat_reply("x"))
        LLMClient("http://127.0.0.1:1/v1", opener=fake).chat([])
        self.assertIsNone(fake.header("Authorization"))

    def test_content_parts_list(self):
        fake = FakeOpener(chat_reply([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]))
        self.assertEqual(LLMClient("http://127.0.0.1:1", opener=fake).chat([]), "ab")

    def test_http_error_becomes_llmerror(self):
        fake = FakeOpener((500, {"error": "boom"}))
        with self.assertRaises(LLMError) as cm:
            LLMClient("http://127.0.0.1:1", opener=fake).chat([])
        self.assertIn("500", str(cm.exception))

    def test_connection_error(self):
        fake = FakeOpener(OSError("refused"))
        with self.assertRaises(LLMError):
            LLMClient("http://127.0.0.1:1", opener=fake).chat([])

    def test_bad_shape(self):
        fake = FakeOpener({"nope": 1})
        with self.assertRaises(LLMError):
            LLMClient("http://127.0.0.1:1", opener=fake).chat([])

    def test_chat_json_adds_system_and_repairs(self):
        fake = FakeOpener(chat_reply('```json\n{"a": 1,}\n```'))
        c = LLMClient("http://127.0.0.1:1", opener=fake)
        self.assertEqual(c.chat_json([{"role": "user", "content": "x"}]), {"a": 1})
        msgs = fake.last_json()["messages"]
        self.assertEqual(msgs[0]["role"], "system")

    def test_response_format_passthrough(self):
        fake = FakeOpener(chat_reply("{}"))
        LLMClient("http://127.0.0.1:1", opener=fake).chat([], response_format={"type": "json_object"})
        self.assertEqual(fake.last_json()["response_format"], {"type": "json_object"})


class VisionTests(unittest.TestCase):
    def test_data_uri_from_bytes_and_path(self):
        uri = llm.image_data_uri(PNG)
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(uri.split(",", 1)[1]), PNG)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "사진.jpg")
            with open(p, "wb") as f:
                f.write(b"\xff\xd8\xff" + b"0" * 10)
            self.assertTrue(llm.image_data_uri(p).startswith("data:image/jpeg;base64,"))
        self.assertEqual(llm.image_data_uri("data:image/png;base64,AAAA"), "data:image/png;base64,AAAA")

    def test_too_big(self):
        with self.assertRaises(LLMError):
            llm.image_data_uri(b"x" * 11, max_bytes=10)

    def test_vision_uses_vision_model(self):
        fake = FakeOpener(chat_reply("고양이"))
        c = LLMClient("http://127.0.0.1:1", model="text", vision_model="vlm", opener=fake)
        self.assertEqual(c.vision("무엇?", [PNG], system="짧게"), "고양이")
        body = fake.last_json()
        self.assertEqual(body["model"], "vlm")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "짧게"})
        parts = body["messages"][1]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "무엇?"})
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png"))

    def test_vision_json(self):
        fake = FakeOpener(chat_reply('결과: {"label": "컵"}'))
        c = LLMClient("http://127.0.0.1:1", opener=fake)
        self.assertEqual(c.vision_json("?", [PNG]), {"label": "컵"})


class ParseJsonTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_json('{"a": "b"}'), {"a": "b"})

    def test_prose_and_fence(self):
        self.assertEqual(parse_json('네, 결과입니다:\n```json\n{"x": [1, 2]}\n```\n끝'), {"x": [1, 2]})

    def test_think_block(self):
        self.assertEqual(parse_json('<think>{"wrong": 1} 고민</think>{"ok": true}'), {"ok": True})

    def test_braces_inside_strings(self):
        self.assertEqual(parse_json('{"t": "a } b { c"} 뒤 {"z": 1}'), {"t": "a } b { c"})

    def test_trailing_commas(self):
        self.assertEqual(parse_json('{"a": [1, 2,], "b": 3,}'), {"a": [1, 2], "b": 3})

    def test_truncated(self):
        self.assertEqual(parse_json('{"a": 1, "b": [1, 2'), {"a": 1, "b": [1, 2]})
        self.assertEqual(parse_json('{"a": "잘린 문'), {"a": "잘린 문"})

    def test_smart_quotes(self):
        self.assertEqual(parse_json("{“a”: “가”}"), {"a": "가"})

    def test_list(self):
        self.assertEqual(parse_json("[1, 2, 3]"), [1, 2, 3])

    def test_want_dict_skips_leading_list(self):
        self.assertEqual(parse_json('[참고] {"a": 1}', want=dict), {"a": 1})

    def test_wrong_type(self):
        with self.assertRaises(LLMError):
            parse_json("[1]", want=dict)

    def test_failures(self):
        for bad in ("", "   ", "그냥 문장", "{not json at all}"):
            with self.assertRaises(LLMError, msg=bad):
                parse_json(bad)


class EnvTests(unittest.TestCase):
    def test_none_without_url(self):
        self.assertIsNone(llm.client_from_env("MB", env={}))

    def test_reads_env(self):
        c = llm.client_from_env("mb", env={"MB_LLM_URL": "http://127.0.0.1:1/v1", "MB_LLM_MODEL": "q",
                                           "MB_LLM_VISION_MODEL": "v"}, opener=FakeOpener())
        self.assertEqual((c.model, c.vision_model), ("q", "v"))

    def test_remote_flag(self):
        env = {"MB_LLM_URL": "https://api.example.com/v1"}
        with self.assertRaises(LLMError):
            llm.client_from_env("MB", env=env)
        env["MB_ALLOW_REMOTE_LLM"] = "1"
        self.assertIsNotNone(llm.client_from_env("MB", env=env, opener=FakeOpener()))


if __name__ == "__main__":
    unittest.main()
