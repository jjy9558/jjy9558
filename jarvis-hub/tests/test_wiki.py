import io
import json
import os
import tempfile
import unittest
import urllib.error

import helpers  # noqa: F401
from helpers import make_hub
from jarvis.handlers import PermanentError
from jarvis.handlers.wiki import WikiAskHandler, is_local_url
from jarvis.intents import parse
from jarvis.wikiindex import WikiIndex, fts5_available, html_to_text, tokenize


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class NotesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.g1 = os.path.join(self.root, "g1")
        self.g2 = os.path.join(self.root, "g2")
        write(os.path.join(self.g1, "boss-guide.md"),
              "# 보스 공략\n\n첫 번째 보스는 불꽃 속성에 약합니다. 얼음 무기를 챙기세요.\n\n"
              "# 아이템\n\n회복 물약은 마을 상점에서 팔지 않습니다.\n")
        write(os.path.join(self.g1, "sub", "cooking.txt"), "요리 레시피: 감자와 소금으로 수프를 만든다.\n")
        write(os.path.join(self.g1, "page.html"),
              "<html><head><style>x{}</style></head><body><h1>숨겨진 방</h1><p>도서관 책장 뒤에 숨겨진 방이 있다.</p>"
              "<script>alert(1)</script></body></html>")
        write(os.path.join(self.g2, "other.md"), "# 다른 게임\n\n보스는 번개에 약하다.\n")

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self, **kw):
        c = {"enabled": True, "sources": {"g1": self.g1, "g2": self.g2}, "top_k": 3}
        c.update(kw)
        return c

    def ask(self, handler, question, game=""):
        p = {"question": question}
        if game:
            p["game"] = game
        return handler.run({"payload": p})


class IndexTest(NotesCase):
    def modes(self):
        return [True, False] if fts5_available() else [False]

    def test_tokenize(self):
        self.assertEqual(tokenize("보스는 HP 3"), ["보스", "스는", "hp", "3"])
        self.assertEqual(tokenize("가"), ["가"])

    def test_html_text(self):
        t = html_to_text("<style>a</style><h1>제목</h1><p>본문</p><script>x()</script>")
        self.assertIn("제목", t)
        self.assertIn("본문", t)
        self.assertNotIn("x()", t)

    def test_ranking_both_modes(self):
        for fts in self.modes():
            idx = WikiIndex(use_fts=fts)
            self.assertEqual(idx.fts, fts)
            idx.scan_folder("g1", self.g1)
            idx.scan_folder("g2", self.g2)
            hits = idx.search("보스 약점 불꽃", top_k=3)
            self.assertEqual(hits[0]["file"], "boss-guide.md", fts)
            self.assertIn("불꽃", hits[0]["text"])
            # 조사가 붙은 질문도 매칭
            self.assertEqual(idx.search("요리 레시피는 어떻게 만들어", source="g1")[0]["file"], "sub/cooking.txt")
            self.assertEqual(idx.search("숨겨진 방 어디", source="g1")[0]["file"], "page.html")
            self.assertEqual(idx.search("보스", source="g2")[0]["file"], "other.md")
            self.assertEqual(idx.search("전혀없는단어쿠쿠"), [])
            idx.close()

    def test_incremental_update_and_removal(self):
        for fts in self.modes():
            db = os.path.join(self.root, "idx-%s.sqlite" % fts)
            write(os.path.join(self.g1, "sub", "cooking.txt"), "요리 레시피: 감자와 소금으로 수프를 만든다.\n")
            if os.path.exists(os.path.join(self.g1, "new.md")):
                os.remove(os.path.join(self.g1, "new.md"))
            idx = WikiIndex(db, use_fts=fts)
            self.assertEqual(idx.scan_folder("g1", self.g1)["added"], 3)
            self.assertEqual(idx.scan_folder("g1", self.g1)["added"], 0)
            os.remove(os.path.join(self.g1, "sub", "cooking.txt"))
            write(os.path.join(self.g1, "new.md"), "# 신규\n\n보라색 열쇠는 지하실에 있다.\n")
            st = idx.scan_folder("g1", self.g1)
            self.assertEqual((st["added"], st["removed"]), (1, 1))
            self.assertEqual(idx.search("보라색 열쇠", source="g1")[0]["file"], "new.md")
            self.assertEqual(idx.search("감자 수프", source="g1"), [])
            idx.close()
            idx = WikiIndex(db, use_fts=fts)  # 디스크에 남은 색인 재사용
            self.assertEqual(idx.scan_folder("g1", self.g1)["added"], 0)
            idx.close()

    def test_symlink_outside_root_skipped(self):
        outside = os.path.join(self.root, "secret.txt")
        write(outside, "비밀 파일 내용 불꽃")
        try:
            os.symlink(outside, os.path.join(self.g2, "link.txt"))
        except (OSError, NotImplementedError):
            self.skipTest("symlink 불가")
        idx = WikiIndex()
        idx.scan_folder("g2", self.g2)
        self.assertEqual(idx.search("비밀 파일"), [])
        idx.close()


class HandlerTest(NotesCase):
    def llm_opener(self, text, seen=None):
        def opener(req, timeout=0):
            if seen is not None:
                seen.append(json.loads(req.data.decode("utf-8")))
            return Resp(json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8"))
        return opener

    def test_payload_validation(self):
        h = WikiAskHandler(self.cfg())
        for p in ({}, {"question": ""}, {"question": "x", "game": "nope"}, {"question": "x", "game": self.g1},
                  {"question": "x", "path": self.g1}, {"question": "x", "folder": "/etc"},
                  {"question": 5}, {"question": "x" * 600}):
            with self.assertRaises(PermanentError, msg=str(p)):
                h.run({"payload": p})

    def test_passages_without_llm(self):
        h = WikiAskHandler(self.cfg())
        r = self.ask(h, "첫 보스 약점이 뭐야", "g1")
        self.assertEqual(r["mode"], "passages")
        self.assertEqual(r["citations"][0], "boss-guide.md")
        self.assertIn("boss-guide.md", r["answer"])
        self.assertIn("불꽃", r["answer"])
        self.assertEqual(r["passages"][0]["game"], "g1")
        self.assertTrue(all(p["game"] == "g1" for p in r["passages"]))

    def test_game_scope_and_all(self):
        h = WikiAskHandler(self.cfg())
        self.assertEqual(self.ask(h, "보스 약점", "g2")["citations"], ["other.md"])
        self.assertEqual({p["game"] for p in self.ask(h, "보스 약점")["passages"]}, {"g1", "g2"})

    def test_no_results(self):
        r = self.ask(WikiAskHandler(self.cfg()), "쿠쿠루삥뽕", "g1")
        self.assertEqual((r["mode"], r["citations"]), ("none", []))

    def test_llm_answer_with_citation(self):
        seen = []
        h = WikiAskHandler(self.cfg(llm={"base_url": "http://127.0.0.1:8080/v1", "model": "m"}),
                           opener=self.llm_opener("불꽃에 약합니다. [boss-guide.md]", seen))
        r = self.ask(h, "첫 보스 약점", "g1")
        self.assertEqual(r["mode"], "llm")
        self.assertIn("boss-guide.md", r["answer"])
        self.assertNotIn("출처:", r["answer"])
        prompt = seen[0]["messages"][1]["content"]
        self.assertIn("[boss-guide.md]", prompt)
        self.assertNotIn("other.md", prompt)

    def test_llm_without_citation_gets_appended(self):
        h = WikiAskHandler(self.cfg(llm={"base_url": "http://127.0.0.1:8080/v1"}),
                           opener=self.llm_opener("불꽃에 약합니다."))
        r = self.ask(h, "첫 보스 약점", "g1")
        self.assertEqual(r["mode"], "llm")
        self.assertTrue(r["answer"].endswith("출처: " + ", ".join(r["citations"])))
        self.assertIn("boss-guide.md", r["answer"])

    def test_llm_failures_fall_back(self):
        errs = [urllib.error.URLError("down"),
                urllib.error.HTTPError("http://x", 500, "err", {}, None),
                ConnectionRefusedError("no")]
        for e in errs:
            def opener(req, timeout=0, e=e):
                raise e
            h = WikiAskHandler(self.cfg(llm={"base_url": "http://127.0.0.1:8080/v1"}), opener=opener)
            r = self.ask(h, "첫 보스 약점", "g1")
            self.assertEqual(r["mode"], "passages")
            self.assertIn("boss-guide.md", r["answer"])
        bad = lambda req, timeout=0: Resp(b"not json")  # noqa: E731
        r = self.ask(WikiAskHandler(self.cfg(llm={"base_url": "http://127.0.0.1:1/v1"}), opener=bad), "보스 약점", "g1")
        self.assertEqual(r["mode"], "passages")

    def test_non_local_llm_refused_without_calling(self):
        def opener(req, timeout=0):
            raise AssertionError("웹으로 나가면 안 됨")
        h = WikiAskHandler(self.cfg(llm={"base_url": "https://api.example.com/v1"}), opener=opener)
        r = self.ask(h, "첫 보스 약점", "g1")
        self.assertEqual(r["mode"], "passages")

    def test_is_local_url(self):
        for u in ("http://127.0.0.1:8080/v1", "http://localhost:8080", "http://192.168.0.5/v1",
                  "http://100.64.0.10:8080/v1", "http://main-pc:8080/v1", "http://box.ts.net/v1"):
            self.assertTrue(is_local_url(u), u)
        for u in ("https://api.openai.com/v1", "http://8.8.8.8/v1", "ftp://localhost", "http://example.com"):
            self.assertFalse(is_local_url(u), u)

    def test_fallback_index_mode_in_handler(self):
        h = WikiAskHandler(self.cfg(force_fallback_index=True))
        r = self.ask(h, "첫 보스 약점", "g1")
        self.assertEqual(r["index"], "bigram")
        self.assertEqual(r["citations"][0], "boss-guide.md")


class WikiIntentTest(unittest.TestCase):
    GAMES = {"g1": ["엘든링", "엘든"]}

    def test_parse(self):
        for t, q in (("자비스, 엘든링 보스 약점 위키에서 찾아줘", "보스 약점"),
                     ("자비스 위키에서 엘든 보스 약점 찾아줘", "보스 약점"),
                     ("자비스 엘든 보스 약점 위키 검색해줘", "보스 약점")):
            it = parse(t, games=self.GAMES).intents[0]
            self.assertEqual((it.name, it.args["game"], it.args["question"]), ("wiki", "g1", q), t)
        it = parse("자비스 불꽃 약점 위키에서 찾아줘", games=self.GAMES).intents[0]
        self.assertEqual((it.args["game"], it.args["question"]), (None, "불꽃 약점"))

    def test_router_submits_job(self):
        hub = make_hub({"router": {"games": self.GAMES}})
        r = hub.router.handle("s21", "자비스, 엘든링 보스 약점 위키에서 찾아줘")
        job = hub.store.get(r["jobs"][0])
        self.assertEqual(job["type"], "wiki.ask")
        self.assertEqual(job["payload"], {"question": "보스 약점", "game": "g1"})
        self.assertNotEqual(job["status"], "waiting_approval")


if __name__ == "__main__":
    unittest.main()
