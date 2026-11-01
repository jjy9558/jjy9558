import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from html.parser import HTMLParser

import fixtures as fx  # noqa: F401
from briefing import cli, config, timeline
from briefing.models import Item

EVIL = '</script><script>alert(1)</script><img src=x onerror=alert(2)>'


def item(title, url, kws, topic="로컬 LLM", sources=("HN",)):
    it = Item(title, url, sources[0], matched=list(kws), topic=topic, brief="요약 " + title)
    it.links = [{"source": s, "url": url} for s in sources]
    return it


class StoreTests(unittest.TestCase):
    def test_append_per_keyword_and_topic(self):
        with tempfile.TemporaryDirectory() as d:
            a = item("A", "https://a", ["llama.cpp", "gguf"])
            paths = timeline.append([a], d, "2026-11-01")
            self.assertEqual(len(paths), 3)  # 2 keywords + 1 topic
            self.assertTrue(os.path.exists(os.path.join(d, "topic__로컬_llm.json")))
            timeline.append([a, item("B", "https://b", ["gguf"])], d, "2026-11-02")
            tls = timeline.load_all(d, today=date(2026, 11, 3))
            by = {(t["kind"], t["name"]): t for t in tls}
            self.assertEqual([e["date"] for e in by[("keyword", "gguf")]["events"]], ["2026-11-02", "2026-11-01"])
            self.assertEqual(len(by[("keyword", "llama.cpp")]["events"]), 1)  # no duplicate url
            self.assertEqual(tls[0]["kind"], "topic")

    def test_days_filter_and_corrupt_and_unsafe_url(self):
        with tempfile.TemporaryDirectory() as d:
            timeline.append([item("Old", "https://o", ["k"])], d, "2026-09-01")
            timeline.append([item("New", "javascript:alert(1)", ["k"])], d, "2026-10-30")
            tls = timeline.load_all(d, days=30, today=date(2026, 11, 1))
            kw = [t for t in tls if t["kind"] == "keyword"][0]
            self.assertEqual([e["title"] for e in kw["events"]], ["New"])
            self.assertEqual(kw["events"][0]["url"], "")
            with open(os.path.join(d, "k.json"), "w") as f:
                f.write("{corrupt")
            self.assertEqual([t["kind"] for t in timeline.load_all(d, today=date(2026, 11, 1))], ["topic"])
            timeline.append([item("X", "https://x", ["k"])], d, "2026-11-01")  # recovers

    def test_missing_dir(self):
        self.assertEqual(timeline.load_all("/nonexistent/dir"), [])

    def test_find(self):
        tls = [{"kind": "keyword", "name": "llama.cpp", "events": []}, {"kind": "topic", "name": "GPU", "events": []}]
        self.assertEqual(timeline.find(tls, "gpu")[0]["name"], "GPU")
        self.assertEqual(timeline.find(tls, "llama")[0]["name"], "llama.cpp")


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class HtmlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        timeline.append([item(EVIL, "https://a.example/?a=1&b=2", ["k"], topic=EVIL, sources=("HN", "GN"))],
                        self.tmp.name, "2026-11-01")
        self.path = timeline.write_html(self.tmp.name, os.path.join(self.tmp.name, "out", "timeline.html"),
                                        30, today=date(2026, 11, 2))
        with open(self.path, encoding="utf-8") as f:
            self.html = f.read()

    def tearDown(self):
        self.tmp.cleanup()

    def test_escaping(self):
        self.assertNotIn(EVIL, self.html)
        self.assertEqual(self.html.count("</script>"), 2)  # only our own two script blocks
        p = _Tags()
        p.feed(self.html)
        self.assertEqual([t for t, a in p.tags if t in ("img", "iframe", "object")], [])
        m = re.search(r'<script id="data" type="application/json">(.*?)</script>', self.html, re.S)
        data = json.loads(m.group(1))
        self.assertEqual(data["timelines"][0]["events"][0]["title"], EVIL)  # round-trips exactly
        self.assertIn("textContent", self.html)  # rendered via textContent, not innerHTML
        self.assertNotIn("innerHTML", self.html)

    def test_self_contained(self):
        p = _Tags()
        p.feed(self.html)
        for tag, attrs in p.tags:
            self.assertNotIn(tag, ("link", "iframe", "img"))
            if tag == "script":
                self.assertNotIn("src", attrs)
        self.assertNotRegex(self.html, r"""(src|href)=["']https?://""")
        self.assertNotIn("@import", self.html)
        self.assertIn("prefers-color-scheme:dark", self.html)
        self.assertIn("viewport", self.html)

    def test_empty_ok(self):
        html = timeline.build_html([], "now")
        self.assertIn("아직 기록된 타임라인이 없습니다", html)


class CliTests(unittest.TestCase):
    def test_timeline_command(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = os.path.join(d, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({}, f)
            tdir = config.resolve(config.load(cfg_path), "data/timeline")
            today = date.today().isoformat()
            timeline.append([item("글 하나", "https://a", ["gguf"])], tdir, today)
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(cli.main(["timeline", "--config", cfg_path, "--topic", "gguf"]), 0)
            out = buf.getvalue()
            self.assertIn("글 하나", out)
            self.assertTrue(os.path.exists(os.path.join(d, "out", "timeline.html")))
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main(["timeline", "--config", cfg_path])
            self.assertIn("gguf", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
