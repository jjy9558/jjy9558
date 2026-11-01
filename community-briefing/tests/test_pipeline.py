import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import fixtures as fx
from briefing import config, deliver, render, timeline, tts
from briefing.fetch import Fetcher
from briefing.models import Item
from briefing.pipeline import parse_duration, run

NOW = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = config.from_dict({"sources": fx.SOURCES, "interests": fx.INTERESTS,
                                     "timeline": {"enabled": True}, "tts": {"enabled": True}}, self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_run_writes_markdown_and_json(self):
        res = run(self.cfg, since="24h", fetcher=fx.FakeFetcher(), llm_client=fx.FakeLLM(), now=NOW)
        md, data = res["markdown"], res["json"]
        titles = [i.title for i in res["items"]]
        self.assertNotIn("새로운 코인 거래소 오픈", titles)        # muted
        self.assertNotIn("오래된 GPU 이야기", titles)             # older than --since
        self.assertNotIn("요리 레시피 모음", titles)              # no interest match
        self.assertNotIn("He said it was fine", titles)
        self.assertIn("RTX 그래픽카드 가격 하락", titles)         # html source
        merged = [i for i in res["items"] if "Hacker News" in i.sources]
        self.assertEqual(merged[0].sources, ["Hacker News", "r/LocalLLaMA"])
        self.assertEqual(res["errors"], ["Broken: connection refused: https://broken.example/rss"])

        self.assertTrue(md.startswith("# 커뮤니티 브리핑 — 2026-11-01"))
        self.assertIn("## 오늘의 요약\n\n1. 로컬 LLM 소식이 많았습니다.\n2. ", md)
        self.assertIn("## 로컬 LLM (", md)
        self.assertIn("[Hacker News](https://github.com/ggml-org/llama.cpp/pull/1) · [r/LocalLLaMA]", md)
        self.assertIn("요약 방식: llm", md)

        paths = res["paths"]
        self.assertTrue(paths["markdown"].endswith(os.path.join("out", "2026-11-01.md")))
        with open(paths["json"], encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["overview"], data["overview"])
        self.assertEqual(len(saved["overview"]), 3)
        self.assertTrue(saved["clusters"][0]["items"][0]["links"])
        self.assertTrue(paths["timeline"])
        self.assertIsNone(paths["audio"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "out", "2026-11-01.script.txt")))

        # second run: everything already seen -> today's file is kept, not overwritten
        res2 = run(self.cfg, since="24h", fetcher=fx.FakeFetcher(), llm_client=fx.FakeLLM(), now=NOW)
        self.assertEqual(res2["items"], [])
        self.assertIn("새로 올라온 관심 글이 없습니다", res2["markdown"])
        with open(paths["markdown"], encoding="utf-8") as f:
            self.assertEqual(f.read(), md)

        # third run with a new story the same day -> YYYY-MM-DD-2.md
        pages = fx.FakeFetcher().pages
        pages["https://hnrss.org/frontpage"] = fx.HN_RSS.replace("Show HN: llama.cpp new quantization format released",
                                                                 "Ollama adds GPU scheduler").replace("/pull/1", "/pull/2")
        res3 = run(self.cfg, since="24h", fetcher=fx.FakeFetcher(pages), llm_client=fx.FakeLLM(), now=NOW)
        self.assertEqual([i.title for i in res3["items"]], ["Ollama adds GPU scheduler"])
        self.assertTrue(res3["paths"]["markdown"].endswith("2026-11-01-2.md"))

    def test_llm_down_falls_back(self):
        res = run(self.cfg, since="24h", fetcher=fx.FakeFetcher(), llm_client=fx.DownLLM(), now=NOW)
        self.assertTrue(res["items"])
        self.assertIn("요약 방식: extractive", res["markdown"])
        self.assertEqual(len(res["json"]["overview"]), 3)

    def test_dry_run_writes_nothing(self):
        res = run(self.cfg, since="24h", dry_run=True, fetcher=fx.FakeFetcher(), llm_client=None, now=NOW)
        self.assertTrue(res["items"])
        self.assertEqual(os.listdir(self.tmp.name), [])  # no out/, no data/seen.sqlite3
        res2 = run(self.cfg, since="24h", dry_run=True, fetcher=fx.FakeFetcher(), llm_client=None, now=NOW)
        self.assertEqual(len(res2["items"]), len(res["items"]))  # nothing was marked seen

    def test_ntfy_delivery(self):
        self.cfg["delivery"]["ntfy"].update(enabled=True, topic="my-topic")
        with mock.patch.object(deliver, "ntfy_send") as send:
            run(self.cfg, since="24h", fetcher=fx.FakeFetcher(), llm_client=None, now=NOW)
        args = send.call_args[0]
        self.assertEqual(args[:3], ("https://ntfy.sh", "my-topic", "커뮤니티 브리핑 2026-11-01"))

    def test_parse_duration(self):
        self.assertEqual(parse_duration("24h").total_seconds(), 86400)
        self.assertEqual(parse_duration("2d").days, 2)
        with self.assertRaises(ValueError):
            parse_duration("yesterday")


class OutputTests(unittest.TestCase):
    def test_markdown_escapes_and_clusters(self):
        a = Item("[속보] RTX 출시", "https://a", "GN", topic="GPU", score=3, brief="요약", comments=7)
        b = Item("llama", "https://b", "HN", topic="LLM", score=5, brief="llama")
        clusters = render.cluster([a, b])
        self.assertEqual(list(clusters), ["LLM", "GPU"])
        md = render.render_markdown("2026-11-01", ["x", "y", "z"], clusters,
                                    {"generated_at": "t", "summary_mode": "llm", "count": 2})
        self.assertIn("**[\\[속보\\] RTX 출시](https://a)**", md)
        self.assertIn("출처: [GN](https://a) · 댓글 7", md)
        self.assertIn("1. x\n2. y\n3. z", md)
        self.assertNotIn("  llama\n", md)  # brief identical to title is not repeated

    def test_stubs_do_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            it = Item("RTX 소식", "https://a", "GN", matched=["rtx", "그래픽카드"], brief="b")
            paths = timeline.append([it], d, "2026-11-01")
            timeline.append([it], d, "2026-11-02")  # same url is not duplicated
            with open(os.path.join(d, "rtx.json"), encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)["events"]), 1)
            self.assertEqual(len(paths), 2)
            with open(os.path.join(d, "rtx.json"), "w") as f:
                f.write("{corrupt")
            timeline.append([it], d, "2026-11-03")
            script = tts.build_script({"date": "2026-11-01", "overview": ["a"], "clusters": []})
            self.assertIn("커뮤니티 브리핑", script)
            self.assertIsNone(tts.synthesize(script, os.path.join(d, "a.mp3")))
            self.assertIsNone(tts.synthesize(script, os.path.join(d, "b.mp3"), backend="future"))


class FetcherTests(unittest.TestCase):
    def test_delay_cache_and_user_agent(self):
        sent, slept = [], []

        class Resp:
            headers = {"ETag": '"v1"'}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"<rss/>"

        def opener(req, timeout):
            sent.append(req)
            if req.get_header("If-none-match"):
                import urllib.error
                raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
            return Resp()

        with tempfile.TemporaryDirectory() as d:
            f = Fetcher("test-agent", delay=2.0, cache_dir=d, opener=opener,
                        sleep=slept.append, clock=lambda: 100.0)
            self.assertEqual(f.get("https://h.example/rss"), b"<rss/>")
            self.assertEqual(f.get("https://h.example/rss"), b"<rss/>")  # 304 -> cached body
        self.assertEqual(sent[0].get_header("User-agent"), "test-agent")
        self.assertEqual(sent[1].get_header("If-none-match"), '"v1"')
        self.assertEqual(slept, [2.0])  # second request to same host waits


if __name__ == "__main__":
    unittest.main()
