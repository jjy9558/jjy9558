import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import fixtures  # noqa: F401  (sets sys.path)
from briefing.models import Item
from briefing.store import SeenStore, merge_items, normalize_title, normalize_url

NOW = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


class DedupeTests(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(normalize_url("http://www.Example.com/a/?utm_source=x&b=2&a=1#frag"),
                         "https://example.com/a?a=1&b=2")
        self.assertEqual(normalize_url("https://old.reddit.com/r/x/"), normalize_url("https://www.reddit.com/r/x"))

    def test_normalize_title(self):
        self.assertEqual(normalize_title("Show HN: Llama.cpp — New Format!"), "llama cpp new format")
        self.assertEqual(normalize_title("[속보] 그래픽카드 가격!"), "속보 그래픽카드 가격")

    def test_merge_similar_titles_lists_both_sources(self):
        a = Item("Show HN: llama.cpp new quantization format released", "https://hn/1", "Hacker News", score=5)
        b = Item("llama.cpp: new quantization format released!", "https://reddit/2", "r/LocalLLaMA",
                 summary="longer summary text", comments=40, score=4)
        c = Item("Totally different story", "https://x/3", "GeekNews", score=3)
        merged = merge_items([c, b, a], 0.82, multi_source_boost=0.5)
        self.assertEqual(len(merged), 2)
        top = merged[0]
        self.assertEqual(top.sources, ["Hacker News", "r/LocalLLaMA"])
        self.assertEqual(top.score, 5.5)
        self.assertEqual(top.summary, "longer summary text")
        self.assertEqual(top.comments, 40)

    def test_merge_same_url_different_source(self):
        a = Item("A title", "https://site.com/post?utm_source=gn", "GeekNews", score=2)
        b = Item("Completely other wording", "http://www.site.com/post/", "HN", score=1)
        merged = merge_items([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].sources, ["GeekNews", "HN"])

    def test_seen_store_across_runs(self):
        with tempfile.TemporaryDirectory() as d:
            store = SeenStore(os.path.join(d, "seen.sqlite3"))
            first = [Item("llama.cpp 새 양자화 포맷 공개", "https://news.hada.io/topic?id=100", "GeekNews")]
            self.assertEqual(len(store.filter_new(first, NOW)), 1)
            store.mark(first, NOW)
            store.close()

            store = SeenStore(os.path.join(d, "seen.sqlite3"))
            again = [
                Item("same url", "http://news.hada.io/topic?id=100&utm_medium=x", "Other"),
                Item("llama.cpp 새 양자화 포맷 공개!", "https://other.site/1", "Other"),  # similar title
                Item("brand new story", "https://new.site/1", "Other"),
            ]
            fresh = store.filter_new(again, NOW + timedelta(hours=1))
            self.assertEqual([i.title for i in fresh], ["brand new story"])
            # title similarity only applies within remember_days
            later = store.filter_new([again[1]], NOW + timedelta(days=30), remember_days=14)
            self.assertEqual(len(later), 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
