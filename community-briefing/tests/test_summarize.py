import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import fixtures as fx
from briefing.models import Item
from briefing.summarize import LLMError, OpenAICompatClient, Summarizer, extractive


def _items():
    return [Item("llama.cpp 새 양자화", "u1", "GeekNews", topic="로컬 LLM",
                 summary="첫 문장입니다. 두 번째 문장이다. 세 번째 문장."),
            Item("GPU price drop", "u2", "HN", topic="GPU", summary="Prices fell. A lot. Really."),
            Item("No summary here", "u3", "HN", topic="GPU")]


class SummarizeTests(unittest.TestCase):
    def test_llm_used_and_think_stripped(self):
        llm = fx.FakeLLM()
        s = Summarizer(llm)
        items = _items()
        for i in items:
            s.summarize_item(i)
        self.assertEqual(items[0].brief, "한국어 요약 문장입니다.")
        self.assertEqual(items[0].brief_by, "llm")
        lines = s.overview(items)
        self.assertEqual(lines, ["로컬 LLM 소식이 많았습니다.", "GPU 가격 이야기도 있었습니다.", "파이썬 새 버전 소식입니다."])
        self.assertEqual(s.mode, "llm")

    def test_fallback_when_llm_down(self):
        llm = fx.DownLLM()
        s = Summarizer(llm)
        items = _items()
        for i in items:
            s.summarize_item(i)
        lines = s.overview(items)
        self.assertEqual(llm.calls, 1)  # disabled after first failure: no repeated timeouts
        self.assertEqual(s.mode, "extractive")
        self.assertEqual(items[0].brief, "첫 문장입니다. 두 번째 문장이다.")
        self.assertEqual(items[1].brief, "Prices fell. A lot.")
        self.assertEqual(items[2].brief, "No summary here")  # falls back to title
        self.assertTrue(all(i.brief_by == "extractive" for i in items))
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "[로컬 LLM] llama.cpp 새 양자화")
        self.assertTrue(s.errors)

    def test_no_client(self):
        s = Summarizer(None)
        self.assertEqual(s.overview([]), ["오늘은 관심사에 맞는 새 글이 없습니다.", "", ""])

    def test_extractive_skips_hn_boilerplate_and_truncates(self):
        it = Item("T", "u", "HN", summary="Article URL: https://x Comments URL: https://y Points: 3 # Comments: 1")
        self.assertEqual(extractive(it), "T")
        long = Item("T", "u", "s", summary="가" * 500)
        self.assertLessEqual(len(extractive(long)), 220)

    def test_openai_client_against_local_fake_server(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured["path"] = self.path
                captured["auth"] = self.headers.get("Authorization")
                captured["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                out = json.dumps({"choices": [{"message": {"content": "요약"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d/v1/" % server.server_port
            client = OpenAICompatClient(base, model="qwen", api_key="k", timeout=5)
            self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), "요약")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(captured["path"], "/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "qwen")
        self.assertEqual(captured["auth"], "Bearer k")
        # server gone -> LLMError, which Summarizer turns into the extractive fallback
        with self.assertRaises(LLMError):
            client.chat([])


if __name__ == "__main__":
    unittest.main()
