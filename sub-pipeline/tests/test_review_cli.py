import contextlib
import io
import json
import os
import re
import tempfile
import unittest

from subpipe import cli, review, srt
from subpipe.llm import LLMUnavailable, OpenAIClient
from subpipe.qa import Limits
from subpipe.srt import Cue
from subpipe.translate import Glossary
from tests.helpers import SAMPLE_SRT, FakeLLM, mkcues


def make_doc(cues, drafts, gl=None):
    return review.build_doc(cues, drafts, Limits(), gl, {"title": "t", "base": "t"})


class FlagTests(unittest.TestCase):
    def flags(self, src, draft, dur=3000, gl=None):
        d = make_doc([Cue(1, 0, dur, src)], [draft], gl)
        return d["entries"][0]["flags"]

    def test_each_flag(self):
        self.assertEqual(self.flags("Hello there", ""), ["untranslated"])
        self.assertIn("untranslated", self.flags("Hello there", "Hello there"))
        self.assertIn("cps", self.flags("Hi", "가" * 60, dur=2000))
        self.assertIn("long", self.flags("Hi", "가" * 30, dur=10000))
        self.assertIn("number", self.flags("We sold 3,000 units", "우리는 이천 개를 팔았다"))
        self.assertNotIn("number", self.flags("We sold 3,000 units", "3000개 판매"))
        self.assertNotIn("number", self.flags("We sold 3,000 units", "3,000개 판매"))
        self.assertIn("name", self.flags("I met John today", "오늘 만났다"))
        self.assertNotIn("name", self.flags("Hello there", "안녕"))  # sentence-initial not a name
        g = Glossary([("GPU", "지피유")])
        self.assertIn("glossary", self.flags("The GPU is hot", "그래픽카드가 뜨겁다", gl=g))
        self.assertEqual(self.flags("The GPU is hot", "지피유가 뜨겁다", gl=g), [])

    def test_clean_has_no_flags(self):
        self.assertEqual(self.flags("Hello there", "안녕하세요"), [])


class HtmlTests(unittest.TestCase):
    EVIL = '</script><script>alert(1)</script> & "q" <b>'

    def test_escaping_and_self_contained(self):
        cues = [Cue(1, 0, 2000, self.EVIL)]
        doc = review.build_doc(cues, [self.EVIL], Limits(), None, {"title": "<img src=x onerror=1>", "base": "t"})
        h = review.render_html(doc)
        self.assertNotIn("<img", h)
        self.assertIn("&lt;img", h)
        self.assertEqual(h.count("<script"), 2)  # data + code only; payload cannot add one
        self.assertEqual(h.count("</script>"), 2)
        self.assertNotIn("alert(1)</script>", h)
        for bad in ("http://", "https://", "<link", "@import"):
            self.assertNotIn(bad, h)
        self.assertIsNone(re.search(r"<script[^>]+src=", h))
        # embedded data round-trips
        m = re.search(r'<script type="application/json" id="data">(.*?)</script>', h, re.S)
        self.assertEqual(json.loads(m.group(1))["entries"][0]["src"], self.EVIL)

    def test_placeholder_in_user_text_not_substituted(self):
        doc = make_doc([Cue(1, 0, 2000, "__TITLE__ __DATA__")], ["x"])
        m = re.search(r'id="data">(.*?)</script>', review.render_html(doc), re.S)
        self.assertEqual(json.loads(m.group(1))["entries"][0]["src"], "__TITLE__ __DATA__")

    def test_has_export_buttons_and_textarea_logic(self):
        h = review.render_html(make_doc(mkcues(["a"]), ["가"]))
        for s in ("dl-json", "dl-srt", "el('textarea')", "Blob"):
            self.assertIn(s, h)


class ApplyTests(unittest.TestCase):
    def test_apply_review(self):
        src = srt.parse_cues(SAMPLE_SRT)
        doc = make_doc(src, ["가", "나\n다", "라"])
        doc["entries"][0]["draft"] = "  수정됨  "
        cues = review.apply_review(doc, src)
        self.assertEqual([c.text for c in cues], ["수정됨", "나\n다", "라"])
        self.assertEqual([(c.start, c.end) for c in cues], [(c.start, c.end) for c in src])
        self.assertEqual([c.index for c in cues], [1, 2, 3])

    def test_apply_rejects_empty_and_mismatch(self):
        src = srt.parse_cues(SAMPLE_SRT)
        doc = make_doc(src, ["가", "", "라"])
        with self.assertRaises(ValueError):
            review.apply_review(doc, src)
        self.assertEqual(len(review.apply_review(doc, src, allow_empty=True)), 3)
        doc2 = make_doc(src, ["가", "나", "다"])
        with self.assertRaises(ValueError):
            review.apply_review(doc2, src[:2])
        doc2["entries"][0]["start_ms"] += 1
        with self.assertRaises(ValueError):
            review.apply_review(doc2, src)
        with self.assertRaises(ValueError):
            review.apply_review({"x": 1})

    def test_exported_minimal_json_works(self):  # shape produced by the HTML download button
        doc = {"version": 1, "meta": {}, "entries": [
            {"index": 2, "start_ms": 2000, "end_ms": 3000, "src": "b", "draft": "나", "reviewed": True},
            {"index": 1, "start_ms": 0, "end_ms": 1000, "src": "a", "draft": "가", "reviewed": False}]}
        self.assertEqual([c.text for c in review.apply_review(doc)], ["가", "나"])


class CliTests(unittest.TestCase):
    def run_cli(self, argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv, **kw)
        return code, out.getvalue(), err.getvalue()

    def test_full_flow(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "talk.srt")
            open(src, "w", encoding="utf-8").write(SAMPLE_SRT)
            code, out, err = self.run_cli(["translate", src, "--to", "ko", "--tm", os.path.join(d, "tm.json")],
                                          client=FakeLLM())
            self.assertEqual(code, 0, err)
            doc = json.load(open(os.path.join(d, "talk.ko.review.json"), encoding="utf-8"))
            self.assertEqual(len(doc["entries"]), 3)
            self.assertTrue(os.path.exists(os.path.join(d, "talk.ko.review.html")))
            draft = srt.load(os.path.join(d, "talk.ko.draft.srt"))
            orig = srt.load(src)
            self.assertEqual([(c.start, c.end) for c in draft], [(c.start, c.end) for c in orig])
            # reviewer edits and exports JSON
            for e in doc["entries"]:
                e["draft"] = "검수완료 %d" % e["index"]
            ed = os.path.join(d, "talk.edited.json")
            json.dump(doc, open(ed, "w", encoding="utf-8"), ensure_ascii=False)
            final = os.path.join(d, "final.srt")
            code, out, err = self.run_cli(["apply-review", ed, "--src", src, "-o", final])
            self.assertEqual(code, 0, err)
            self.assertEqual([c.text for c in srt.load(final)], ["검수완료 1", "검수완료 2", "검수완료 3"])
            code, out, _ = self.run_cli(["qa", final, "--cps", "50"])
            self.assertEqual(code, 0, out)

    def test_unreachable_server(self):
        class Down:
            def chat(self, *a, **k):
                raise LLMUnavailable("connection refused")
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "t.srt")
            open(src, "w", encoding="utf-8").write(SAMPLE_SRT)
            code, out, err = self.run_cli(["translate", src, "--no-tm"], client=Down())
            self.assertEqual(code, 2)
            self.assertIn("connection refused", err)
            self.assertIn("이어서", err)
            self.assertTrue(os.path.exists(os.path.join(d, "t.ko.review.json")))  # progress saved

    def test_real_client_unreachable_message(self):
        c = OpenAIClient("http://127.0.0.1:9/v1", timeout=1)
        with self.assertRaises(LLMUnavailable) as cm:
            c.chat([{"role": "user", "content": "x"}])
        self.assertIn("llama-server", str(cm.exception))

    def test_qa_exit_code_and_price(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "b.srt")
            srt.save_srt(bad, [Cue(1, 0, 2000, "가"), Cue(2, 1000, 3000, "나")])
            code, out, _ = self.run_cli(["qa", bad])
            self.assertEqual(code, 1)
            self.assertIn("overlap", out)
            code, out, _ = self.run_cli(["price", bad, "--rate", "5000", "--round", "none"])
            self.assertEqual(code, 0)
            self.assertIn("250", out)  # 3s = 0.05min * 5000 = 250
            code, out, _ = self.run_cli(["price", bad, "--minutes", "10"])
            self.assertIn("50,000원", out)
            self.assertIn("100,000원", out)

    def test_transcribe_with_fake_whisper(self):
        class Info: language = "en"
        class FakeModel:
            def transcribe(self, path, **kw):
                self.kw = kw
                w = [{"word": " Hello", "start": 0.0, "end": 0.4}, {"word": " world.", "start": 0.5, "end": 1.2}]
                return iter([{"start": 0.0, "end": 1.2, "text": "Hello world.", "words": w}]), Info()
        with tempfile.TemporaryDirectory() as d:
            media = os.path.join(d, "v.mp4")
            open(media, "wb").write(b"x")
            fm = FakeModel()
            code, out, err = self.run_cli(["transcribe", media], whisper_factory=lambda *a: fm)
            self.assertEqual(code, 0, err)
            self.assertTrue(fm.kw["word_timestamps"])
            self.assertIsNone(fm.kw["language"])
            cues = srt.load(os.path.join(d, "v.en.srt"))
            self.assertEqual(cues[0].text, "Hello world.")

    def test_transcribe_missing_faster_whisper_hint(self):
        import sys
        if "faster_whisper" in sys.modules:
            self.skipTest("faster_whisper installed")
        try:
            import faster_whisper  # noqa
            self.skipTest("faster_whisper installed")
        except ImportError:
            pass
        code, out, err = self.run_cli(["transcribe", "x.mp4"])
        self.assertEqual(code, 3)
        self.assertIn("pip install faster-whisper", err)


if __name__ == "__main__":
    unittest.main()
