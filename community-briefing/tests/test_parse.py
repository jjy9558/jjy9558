import unittest

import fixtures as fx
from briefing.parse import parse_date, parse_feed, parse_html, strip_html


class ParseTests(unittest.TestCase):
    def test_rss(self):
        items = parse_feed(fx.RSS, "GeekNews")
        self.assertEqual(len(items), 4)
        first = items[0]
        self.assertEqual(first.title, "llama.cpp 새 양자화 포맷 공개")
        self.assertIn("utm_source", first.url)
        self.assertTrue(first.summary.startswith("llama.cpp에 새로운"))
        self.assertNotIn("<p>", first.summary)
        self.assertEqual(first.comments, 12)
        self.assertEqual(first.published, "2026-10-31T20:00:00+00:00")
        self.assertIsNone(items[3].published)

    def test_hn_comments_in_description(self):
        items = parse_feed(fx.HN_RSS, "HN")
        self.assertEqual(items[0].comments, 150)
        self.assertEqual(items[0].summary, "")  # "Article URL/Points/# Comments" footer removed

    def test_atom(self):
        items = parse_feed(fx.ATOM, "r/LocalLLaMA")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].url, "https://www.reddit.com/r/LocalLLaMA/comments/abc/best_gpu/")
        self.assertIn("70B", items[0].summary)
        self.assertEqual(items[0].published, "2026-10-31T20:15:30+00:00")
        self.assertEqual(items[2].published, "2026-10-31T21:00:00+00:00")

    def test_feed_bytes_and_max_items(self):
        self.assertEqual(len(parse_feed(fx.RSS.encode("utf-8"), "x", max_items=2)), 2)

    def test_html_tag_class(self):
        rules = {"tag": "td", "class": "subject", "href_regex": r"/board/view/\d+"}
        items = parse_html(fx.HTML_PAGE, "Board", "https://example.com/board/list", rules)
        self.assertEqual([i.title for i in items], ["파이썬 3.14 릴리스 노트 정리", "RTX 그래픽카드 가격 하락"])
        self.assertEqual(items[0].url, "https://example.com/board/view/1")
        self.assertEqual(items[1].url, "https://example.com/board/view/2?x=1&y=2")

    def test_html_anchor_class(self):
        items = parse_html(fx.HTML_PAGE, "B", "https://example.com/", {"tag": "a", "class": "title"})
        self.assertEqual([(i.title, i.url) for i in items], [("Open source GPU drivers", "https://example.com/p/7")])

    def test_html_regex(self):
        rules = {"regex": r'<a href="(?P<url>/board/view/\d+)[^"]*">(?P<title>.*?)</a>'}
        items = parse_html(fx.HTML_PAGE.encode("cp949", errors="ignore"), "B", "https://example.com/", rules)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "파이썬 3.14 릴리스 노트 정리")  # cp949 bytes decoded

    def test_html_requires_rules(self):
        with self.assertRaises(ValueError):
            parse_html("<a href='/x'>x</a>", "B", "https://e.com/", {})

    def test_helpers(self):
        self.assertEqual(strip_html("<p>a&amp;b</p><script>x()</script><br>c"), "a&b c")
        self.assertIsNone(parse_date("not a date"))


if __name__ == "__main__":
    unittest.main()
