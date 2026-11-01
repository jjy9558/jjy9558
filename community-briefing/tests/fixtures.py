"""Local fixtures: feed/HTML strings, fake fetcher and fake LLMs. No network is used."""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from briefing.fetch import FetchError  # noqa: E402

logging.getLogger("briefing").addHandler(logging.NullHandler())  # keep test output quiet

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:slash="http://purl.org/rss/1.0/modules/slash/">
<channel><title>GeekNews</title>
<item>
  <title>llama.cpp 새 양자화 포맷 공개</title>
  <link>https://news.hada.io/topic?id=100&amp;utm_source=rss</link>
  <description>&lt;p&gt;llama.cpp에 새로운 양자화 방식이 추가되었다. VRAM 사용량이 크게 줄었다.&lt;/p&gt;&lt;p&gt;세 번째 문장.&lt;/p&gt;</description>
  <pubDate>Sun, 01 Nov 2026 05:00:00 +0900</pubDate>
  <slash:comments>12</slash:comments>
</item>
<item>
  <title>새로운 코인 거래소 오픈</title>
  <link>https://news.hada.io/topic?id=101</link>
  <description>LLM 기반 코인 추천 서비스</description>
  <pubDate>Sun, 01 Nov 2026 04:00:00 +0900</pubDate>
</item>
<item>
  <title>오래된 GPU 이야기</title>
  <link>https://news.hada.io/topic?id=99</link>
  <description>RTX 이야기</description>
  <pubDate>Mon, 20 Oct 2026 04:00:00 +0900</pubDate>
</item>
<item>
  <title>요리 레시피 모음</title>
  <link>https://news.hada.io/topic?id=102</link>
  <description>오늘의 저녁 메뉴</description>
</item>
</channel></rss>"""

HN_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Hacker News: Front Page</title>
<item>
  <title>Show HN: llama.cpp new quantization format released</title>
  <link>https://github.com/ggml-org/llama.cpp/pull/1</link>
  <description><![CDATA[<p>Article URL: <a href="https://github.com/ggml-org/llama.cpp/pull/1">x</a></p><p>Points: 300</p><p># Comments: 150</p>]]></description>
  <pubDate>Sat, 31 Oct 2026 22:00:00 +0000</pubDate>
</item>
<item>
  <title>He said it was fine</title>
  <link>https://example.org/said</link>
  <description>Nothing about AI here, he said.</description>
  <pubDate>Sat, 31 Oct 2026 22:00:00 +0000</pubDate>
</item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>LocalLLaMA</title>
<entry>
  <title>Best GPU for running a local LLM with 24GB VRAM?</title>
  <link href="https://www.reddit.com/r/LocalLLaMA/comments/abc/best_gpu/"/>
  <updated>2026-10-31T20:15:30.123+00:00</updated>
  <content type="html">&lt;div&gt;I want to run 70B models. Which GPU should I buy? Thanks.&lt;/div&gt;</content>
</entry>
<entry>
  <title>llama.cpp: new quantization format released!</title>
  <link href="https://www.reddit.com/r/LocalLLaMA/comments/ghi/new_quant/"/>
  <published>2026-10-31T23:00:00Z</published>
  <summary>Discussion of the new quant format.</summary>
</entry>
<entry>
  <title>Weekly meme thread</title>
  <link rel="alternate" href="https://www.reddit.com/r/LocalLLaMA/comments/def/meme/"/>
  <published>2026-10-31T21:00:00Z</published>
  <summary>memes</summary>
</entry>
</feed>"""

HTML_PAGE = """<html><body>
<table>
<tr><td class="subject"><a href="/board/view/1">파이썬 3.14 릴리스 노트 정리</a> <span>[3]</span></td></tr>
<tr><td class="subject hot"><a href="/board/view/2?x=1&amp;y=2">RTX 그래픽카드 가격 하락</a></td></tr>
<tr><td class="writer"><a href="/user/9">작성자</a></td></tr>
<tr><td class="subject"><a href="/notice">공지</a></td></tr>
</table>
<div class="post"><h3><a class="title" href="https://example.com/p/7">  Open source  GPU drivers </a></h3></div>
</body></html>"""

SOURCES = [
    {"name": "GeekNews", "type": "rss", "url": "https://news.hada.io/rss"},
    {"name": "Hacker News", "type": "rss", "url": "https://hnrss.org/frontpage"},
    {"name": "r/LocalLLaMA", "type": "rss", "url": "https://www.reddit.com/r/LocalLLaMA/.rss"},
    {"name": "Board", "type": "html", "url": "https://example.com/board/list",
     "rules": {"tag": "td", "class": "subject", "href_regex": r"/board/view/\d+"}},
    {"name": "Broken", "type": "rss", "url": "https://broken.example/rss"},
]

INTERESTS = {
    "topics": {
        "로컬 LLM": {"llama.cpp": 4, "llm": 3, "양자화": 2, "quantization": 2},
        "GPU": {"gpu": 2, "rtx": 2, "vram": 2, "그래픽카드": 2},
        "개발": {"파이썬": 1.5, "python": 1.5, "ai": 1},
    },
    "mute": ["코인"],
    "min_score": 1.0,
    "top_n": 10,
}


class FakeFetcher:
    def __init__(self, pages=None):
        self.pages = pages if pages is not None else {
            "https://news.hada.io/rss": RSS, "https://hnrss.org/frontpage": HN_RSS,
            "https://www.reddit.com/r/LocalLLaMA/.rss": ATOM, "https://example.com/board/list": HTML_PAGE}
        self.calls = []

    def get(self, url, delay=None, check_robots=False):
        self.calls.append(url)
        if url not in self.pages:
            raise FetchError("connection refused: " + url)
        return self.pages[url].encode("utf-8")


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=300):
        self.calls += 1
        if "정확히 3줄" in messages[-1]["content"]:
            return "<think>hmm</think>1. 로컬 LLM 소식이 많았습니다.\n2. GPU 가격 이야기도 있었습니다.\n- 파이썬 새 버전 소식입니다."
        return "<think>reasoning</think>한국어 요약 문장입니다."


class DownLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=300):
        self.calls += 1
        raise OSError("[Errno 111] Connection refused")
