import json
import os
import tempfile
import unittest

from subpipe.llm import LLMUnavailable
from subpipe.qa import Limits
from subpipe.translate import (Glossary, TranslationMemory, Translator, format_draft,
                               parse_batch_output)
from tests.helpers import FakeLLM, mkcues


class ParseTests(unittest.TestCase):
    ids = [1, 2, 3]

    def test_clean_json(self):
        r = parse_batch_output('[{"id":1,"text":"a"},{"id":2,"text":"b"},{"id":3,"text":"c"}]', self.ids)
        self.assertEqual(r, {1: "a", 2: "b", 3: "c"})

    def test_fenced_with_prose(self):
        raw = 'Sure! Here you go:\n```json\n[{"id":1,"text":"가"},{"id":2,"text":"나"}]\n```\nHope it helps'
        self.assertEqual(parse_batch_output(raw, self.ids), {1: "가", 2: "나"})

    def test_wrapped_object_and_mapping(self):
        self.assertEqual(parse_batch_output('{"translations":[{"id":2,"text":"x"}]}', self.ids), {2: "x"})
        self.assertEqual(parse_batch_output('{"1":"a","3":"c"}', self.ids), {1: "a", 3: "c"})

    def test_truncated_json_salvaged(self):
        raw = '[{"id":1,"text":"하나"},{"id":2,"text":"둘"},{"id":3,"text":"세'
        self.assertEqual(parse_batch_output(raw, self.ids), {1: "하나", 2: "둘"})

    def test_trailing_comma_and_escapes(self):
        raw = '[{"id":1,"text":"say \\"hi\\""},{"id":2,"text":"b"},]'
        self.assertEqual(parse_batch_output(raw, self.ids), {1: 'say "hi"', 2: "b"})

    def test_numbered_lines_fallback(self):
        self.assertEqual(parse_batch_output("1. 가\n2) 나\n3: 다", self.ids), {1: "가", 2: "나", 3: "다"})

    def test_unknown_ids_and_dupes_ignored(self):
        raw = '[{"id":9,"text":"z"},{"id":1,"text":"a"},{"id":1,"text":"again"}]'
        self.assertEqual(parse_batch_output(raw, self.ids), {1: "a"})

    def test_garbage_and_empty(self):
        self.assertEqual(parse_batch_output("I cannot do that", self.ids), {})
        self.assertEqual(parse_batch_output("", self.ids), {})
        self.assertEqual(parse_batch_output('[{"id":1,"text":"  "}]', self.ids), {})

    def test_positional_string_list(self):
        self.assertEqual(parse_batch_output('["a","b","c"]', self.ids), {1: "a", 2: "b", 3: "c"})


class GlossaryTests(unittest.TestCase):
    def test_load_and_match(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# c\nGPU = 지피유\nOpenAI => 오픈에이아이\nLlama\t라마\n")
            g = Glossary.load(p)
        self.assertEqual(len(g.pairs), 3)
        self.assertEqual(g.relevant("new gpus are fast"), [("GPU", "지피유")])
        self.assertEqual(g.relevant("gpuless"), [])
        self.assertEqual(g.violations("Use the GPU", "그래픽카드를 쓴다"), [("GPU", "지피유")])
        self.assertEqual(g.violations("Use the GPU", "지피유를 쓴다"), [])


class TranslatorTests(unittest.TestCase):
    def make(self, llm, **kw):
        return Translator(llm, "en", "ko", limits=Limits(), **kw)

    def test_one_to_one_and_batching(self):
        cues = mkcues(["line %d" % i for i in range(1, 8)])
        llm = FakeLLM()
        out = self.make(llm, batch_size=3).translate(cues)
        self.assertEqual(out, ["KO:line %d" % i for i in range(1, 8)])
        self.assertEqual(len(llm.calls), 3)  # 3 + 3 + 1

    def test_context_in_prompt_but_not_in_items(self):
        cues = mkcues(["aaa", "bbb", "ccc", "ddd", "eee"])
        llm = FakeLLM()
        self.make(llm, batch_size=1, context=1).translate(cues)
        msgs, items = llm.calls[2]
        self.assertEqual([i["text"] for i in items], ["ccc"])
        user = msgs[-1]["content"]
        self.assertIn("CONTEXT BEFORE", user)
        self.assertIn("bbb", user)
        self.assertIn("ddd", user)
        self.assertNotIn("aaa", user)
        self.assertIn("max_chars", user)

    def test_style_and_glossary_in_system_prompt(self):
        llm = FakeLLM()
        g = Glossary([("GPU", "지피유"), ("unused", "x")])
        self.make(llm, tone="반말", domain="예능", glossary=g).translate(mkcues(["my GPU"]))
        sysmsg = llm.calls[0][0][0]["content"]
        self.assertIn("GPU => 지피유", sysmsg)
        self.assertNotIn("unused", sysmsg)
        self.assertIn("반말", sysmsg)
        self.assertIn("Variety", sysmsg)

    def test_malformed_batch_triggers_per_line_retry(self):
        def responder(items, n):
            if len(items) > 1:  # batch: drop id 2 and return junk for 3
                return '[{"id":1,"text":"A"},{"id":3,"text":""}]'
            return json.dumps([{"id": 1, "text": "R:" + items[0]["text"]}])
        cues = mkcues(["one", "two", "three"])
        llm = FakeLLM(responder)
        out = self.make(llm).translate(cues)
        self.assertEqual(out, ["A", "R:two", "R:three"])
        self.assertEqual(len(out), len(cues))

    def test_permanent_failure_left_none(self):
        llm = FakeLLM(lambda items, n: "{nope}")
        out = self.make(llm, retries=2).translate(mkcues(["x", "y"]))
        self.assertEqual(out, [None, None])
        self.assertEqual(len(llm.calls), 1 + 2 * 2)  # batch + 2 retries each

    def test_single_line_plain_text_accepted(self):
        def responder(items, n):
            return "[bad" if len(items) > 1 else "그냥 텍스트"
        out = self.make(FakeLLM(responder)).translate(mkcues(["x", "y"]))
        self.assertEqual(out, ["그냥 텍스트", "그냥 텍스트"])

    def test_translation_memory_cache_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tm.json")
            llm = FakeLLM()
            self.make(llm, tm=TranslationMemory(path)).translate(mkcues(["a", "b", "a"]))
            n1 = len(llm.calls)
            llm2 = FakeLLM()
            out = self.make(llm2, tm=TranslationMemory(path)).translate(mkcues(["a", "b"]))
            self.assertEqual(out, ["KO:a", "KO:b"])
            self.assertEqual(llm2.calls, [])
            self.assertGreaterEqual(n1, 1)

    def test_tm_key_depends_on_tone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tm.json")
            self.make(FakeLLM(), tm=TranslationMemory(path), tone="formal").translate(mkcues(["a"]))
            llm = FakeLLM()
            self.make(llm, tm=TranslationMemory(path), tone="casual").translate(mkcues(["a"]))
            self.assertEqual(len(llm.calls), 1)

    def test_unreachable_saves_progress(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tm.json")

            def responder(items, n):
                if n == 2:
                    raise LLMUnavailable("server down")
                return json.dumps([{"id": i["id"], "text": "T" + i["text"]} for i in items])
            seen = []
            tr = self.make(FakeLLM(responder), tm=TranslationMemory(path), batch_size=2,
                           on_progress=lambda r: seen.append(list(r)))
            with self.assertRaises(LLMUnavailable):
                tr.translate(mkcues(["a", "b", "c", "d"]))
            self.assertEqual(seen[-1], ["Ta", "Tb", None, None])
            self.assertEqual(len(TranslationMemory(path).data), 2)
            # resume: only the remaining 2 lines hit the LLM
            llm = FakeLLM()
            out = self.make(llm, tm=TranslationMemory(path), batch_size=2).translate(mkcues(["a", "b", "c", "d"]))
            self.assertEqual(out[:2], ["Ta", "Tb"])
            self.assertEqual(sum(len(i) for _, i in llm.calls), 2)

    def test_condense_shortens_and_leaves_flag_when_not_possible(self):
        cues = mkcues(["x"], dur=1000)  # limit 14 chars
        drafts = ["이것은 매우 매우 긴 번역 문장입니다 정말로"]
        llm = FakeLLM(lambda items, n: json.dumps([{"id": 1, "text": "짧은 번역"}]))
        out = self.make(llm).condense(cues, drafts)
        self.assertEqual(out, ["짧은 번역"])
        llm2 = FakeLLM(lambda items, n: json.dumps([{"id": 1, "text": drafts[0] + "!!"}]))
        self.assertEqual(self.make(llm2).condense(cues, drafts), drafts)

    def test_format_draft_wraps(self):
        t = format_draft("이것은 조금 긴 한국어 자막 문장입니다 두 줄로 나뉘어야 합니다", Limits(max_chars=16))
        self.assertEqual(len(t.split("\n")), 2)
        self.assertEqual(format_draft(None, Limits()), "")


if __name__ == "__main__":
    unittest.main()
