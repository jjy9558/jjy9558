import http.server
import json
import os
import socket
import tempfile
import threading
import unittest

from subvocab.llm import (
    DEFAULT_BASE_URL,
    LLMClient,
    LLMConfig,
    LLMError,
    build_messages,
    fill_meanings,
    load_llm_config,
    parse_meanings,
)
from subvocab.vocab import Entry

PROJECT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "llm.json")


def entries():
    return [
        Entry(key="go", word="go", count=2, example="We went home."),
        Entry(key="apple", word="apple", count=1, example="I like apples."),
    ]


class FakeClient:
    """Answers with meanings for every word it knows, as a model would."""

    def __init__(self, meanings):
        self.meanings = meanings
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        items = [{"word": w, "meaning": m} for w, m in self.meanings.items()]
        return json.dumps({"items": items}, ensure_ascii=False)


class DownClient:
    """Simulates an unreachable endpoint."""

    def __init__(self):
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        raise LLMError("connection refused")


class FillMeaningsTests(unittest.TestCase):
    def test_fills_each_batch(self):
        items = entries()
        client = FakeClient({"go": "가다", "apple": "사과"})
        filled = fill_meanings(items, client, batch_size=1)
        self.assertEqual(filled, 2)
        self.assertEqual(client.calls, 2)
        self.assertEqual([e.meaning for e in items], ["가다", "사과"])

    def test_unreachable_endpoint_leaves_blank_and_stops(self):
        items = entries()
        client = DownClient()
        messages = []
        filled = fill_meanings(items, client, batch_size=1, log=messages.append)
        self.assertEqual(filled, 0)
        self.assertEqual(client.calls, 1)  # no retries for every remaining batch
        self.assertEqual([e.meaning for e in items], ["", ""])
        self.assertEqual(len(messages), 1)

    def test_bad_reply_leaves_batch_blank_but_continues(self):
        class Garbage:
            def __init__(self):
                self.calls = 0

            def complete(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return "sorry, I cannot do JSON today"
                return json.dumps({"items": [{"word": "apple", "meaning": "사과"}]})

        items = entries()
        client = Garbage()
        self.assertEqual(fill_meanings(items, client, batch_size=1), 1)
        self.assertEqual(client.calls, 2)
        self.assertEqual([e.meaning for e in items], ["", "사과"])

    def test_build_messages_contains_word_and_sentence(self):
        messages = build_messages(entries())
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("We went home.", messages[1]["content"])
        self.assertIn('"word": "go"', messages[1]["content"])


class ParseMeaningsTests(unittest.TestCase):
    def test_fenced_json_and_case_normalised(self):
        content = '```json\n{"items": [{"word": "Go", "meaning": "가다"}]}\n```'
        self.assertEqual(parse_meanings(content), {"go": "가다"})

    def test_json_inside_prose(self):
        content = 'Here you go: {"items": [{"word": "apple", "meaning": "사과"}]} thanks'
        self.assertEqual(parse_meanings(content), {"apple": "사과"})

    def test_plain_list_accepted(self):
        self.assertEqual(parse_meanings('[{"word": "go", "meaning": "가다"}]'), {"go": "가다"})

    def test_garbage_gives_empty(self):
        self.assertEqual(parse_meanings("no json at all"), {})
        self.assertEqual(parse_meanings('{"items": "wrong"}'), {})

    def test_long_meaning_is_truncated(self):
        content = json.dumps({"items": [{"word": "go", "meaning": "가" * 200}]})
        self.assertEqual(len(parse_meanings(content)["go"]), 60)


class ConfigTests(unittest.TestCase):
    def test_defaults_when_no_file_and_no_env(self):
        cfg = load_llm_config(None, env={})
        self.assertEqual(cfg.base_url, "http://127.0.0.1:8080/v1")
        self.assertEqual(cfg.base_url, DEFAULT_BASE_URL)

    def test_project_config_file_loaded(self):
        cfg = load_llm_config(PROJECT_CONFIG, env={})
        self.assertEqual(cfg.base_url, "http://127.0.0.1:8080/v1")
        self.assertEqual(cfg.model, "local-model")

    def test_env_overrides_file(self):
        cfg = load_llm_config(PROJECT_CONFIG, env={
            "SUBVOCAB_LLM_BASE_URL": "http://10.0.0.5:9000/v1",
            "SUBVOCAB_LLM_MODEL": "qwen",
        })
        self.assertEqual(cfg.base_url, "http://10.0.0.5:9000/v1")
        self.assertEqual(cfg.model, "qwen")

    def test_bad_json_file_raises_value_error(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.isdir(tmp) and __import__("shutil").rmtree(tmp, True))
        path = os.path.join(tmp, "llm.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with self.assertRaises(ValueError):
            load_llm_config(path, env={})


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _ChatHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({
            "choices": [{"message": {"content": json.dumps(
                {"items": [{"word": "go", "meaning": "가다"}]}, ensure_ascii=False)}}]
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LocalHttpClientTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _ChatHandler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base_url = "http://127.0.0.1:%d/v1" % self.server.server_address[1]

    def test_round_trip_against_local_openai_style_server(self):
        client = LLMClient(LLMConfig(base_url=self.base_url, timeout=5))
        items = entries()
        self.assertEqual(fill_meanings(items, client, batch_size=10), 1)
        self.assertEqual(items[0].meaning, "가다")
        self.assertEqual(items[1].meaning, "")

    def test_unreachable_port_raises_llm_error(self):
        url = "http://127.0.0.1:%d/v1" % free_port()
        with self.assertRaises(LLMError):
            LLMClient(LLMConfig(base_url=url, timeout=2)).complete([{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()
