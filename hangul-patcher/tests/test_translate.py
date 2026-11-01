import json
import unittest

from hpatch.glossary import Glossary
from hpatch.llm import LLMUnreachable
from hpatch.masking import rpgmaker_masker
from hpatch.tm import TranslationMemory
from hpatch.translate import Translator, build_messages, parse_batch_response, parse_single_fallback, Item
from hpatch.units import Unit
from helpers import FakeLLM


class ParseTests(unittest.TestCase):
    ids = [1, 2, 3]

    def test_clean_json(self):
        raw = '[{"id":1,"text":"가"},{"id":2,"text":"나"},{"id":3,"text":"다"}]'
        self.assertEqual(parse_batch_response(raw, self.ids), {1: "가", 2: "나", 3: "다"})

    def test_code_fence_and_chatter(self):
        raw = 'Sure!\n```json\n[{"id": 1, "text": "가"}, {"id": 2, "text": "나"}]\n```\nDone.'
        self.assertEqual(parse_batch_response(raw, self.ids), {1: "가", 2: "나"})

    def test_think_block_removed(self):
        raw = '<think>[{"id":1,"text":"WRONG"}]</think>[{"id":1,"text":"가"}]'
        self.assertEqual(parse_batch_response(raw, [1]), {1: "가"})

    def test_truncated_json_salvages_complete_pairs(self):
        raw = '[{"id": 1, "text": "가\\n둘째 줄"}, {"id": 2, "text": "나"}, {"id": 3, "te'
        self.assertEqual(parse_batch_response(raw, self.ids), {1: "가\n둘째 줄", 2: "나"})

    def test_dict_form_and_string_list(self):
        self.assertEqual(parse_batch_response('{"1": "가", "2": "나"}', self.ids), {1: "가", 2: "나"})
        self.assertEqual(parse_batch_response('["가","나","다"]', self.ids), {1: "가", 2: "나", 3: "다"})
        self.assertEqual(parse_batch_response('{"translations":[{"id":2,"translation":"나"}]}', self.ids), {2: "나"})

    def test_numbered_lines(self):
        raw = "1. 가\n2) 나\n3: 다"
        self.assertEqual(parse_batch_response(raw, self.ids), {1: "가", 2: "나", 3: "다"})

    def test_unknown_ids_and_garbage(self):
        self.assertEqual(parse_batch_response('[{"id":9,"text":"x"}]', self.ids), {})
        self.assertEqual(parse_batch_response("I cannot do that.", self.ids), {})
        self.assertEqual(parse_batch_response("", self.ids), {})
        self.assertEqual(parse_batch_response("[1, 2]", self.ids), {})

    def test_single_fallback(self):
        self.assertEqual(parse_single_fallback('"안녕"'), "안녕")
        self.assertEqual(parse_single_fallback("1. 안녕"), "안녕")
        self.assertIsNone(parse_single_fallback("[{broken"))

    def test_prompt_contains_context_and_only_relevant_glossary(self):
        m = rpgmaker_masker()
        g = Glossary(terms={"Alice": "앨리스", "Zebra": "얼룩말"}, keep=["HP"])
        it = Item("Hi Alice", "dialogue", m.mask("Hi Alice"), speaker="Bob", prev="Previous line")
        msgs = build_messages([it], g)
        user = msgs[1]["content"]
        self.assertIn("Alice => 앨리스", user)
        self.assertNotIn("Zebra", user)
        self.assertNotIn("HP =>", user)
        payload = json.loads(user.split("Items:\n")[1])
        self.assertEqual(payload[0]["speaker"], "Bob")
        self.assertEqual(payload[0]["prev"], "Previous line")


def units(*texts, kind="dialogue"):
    return [Unit(file="f.json", kind=kind, source=t) for t in texts]


class TranslatorTests(unittest.TestCase):
    def make(self, client, **kw):
        self.tm = TranslationMemory(":memory:")
        return Translator(client, self.tm, rpgmaker_masker(), log=lambda s: None, **kw)

    def test_batches_and_translation_memory_reuse(self):
        llm = FakeLLM()
        tr = self.make(llm, batch_size=2)
        us = units("One", "Two", "Three", "One")
        rep = tr.run(us)
        self.assertEqual(rep.translated, 3)
        self.assertEqual(rep.batches, 2)
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(self.tm.get("Two"), "KO:Two")
        # 두 번째 실행: 호출 0회
        llm2 = FakeLLM()
        tr2 = Translator(llm2, self.tm, rpgmaker_masker(), log=lambda s: None)
        rep2 = tr2.run(us + units("Four"))
        self.assertEqual(rep2.cached, 3)
        self.assertEqual(rep2.translated, 1)
        self.assertEqual(len(llm2.calls), 1)

    def test_control_codes_survive_pipeline(self):
        tr = self.make(FakeLLM())
        tr.run(units("\\C[2]Hello\\C[0] \\V[1]"))
        self.assertEqual(self.tm.get("\\C[2]Hello\\C[0] \\V[1]"), "KO:\\C[2]Hello\\C[0] \\V[1]")

    def test_mismatch_in_batch_retries_individually(self):
        llm = FakeLLM("drop_placeholder_in_batch")  # 배치일 때만 플레이스홀더를 떨굼
        tr = self.make(llm, batch_size=4)
        rep = tr.run(units("a \\V[1]", "b \\V[2]"))
        self.assertEqual(rep.failed, 0)
        self.assertEqual(rep.translated, 2)
        self.assertEqual(len(llm.calls), 3)  # 배치 1 + 개별 2
        self.assertEqual(self.tm.get("a \\V[1]"), "KO:a \\V[1]")

    def test_missing_item_retried_per_line(self):
        llm = FakeLLM("skip_second")
        tr = self.make(llm, batch_size=3)
        rep = tr.run(units("a", "b", "c"))
        self.assertEqual(rep.translated, 3)
        self.assertEqual(rep.failed, 0)

    def test_persistent_placeholder_loss_falls_back_to_original(self):
        tr = self.make(FakeLLM("always_drop"), retries=2)
        rep = tr.run(units("keep \\V[1]", "plain"))
        self.assertEqual(rep.failed, 1)
        self.assertEqual(rep.failed_sources, ["keep \\V[1]"])
        self.assertIsNone(self.tm.get("keep \\V[1]"))  # 저장되지 않음 -> 적용 시 원문 유지
        self.assertEqual(self.tm.get("plain"), "KO:plain")

    def test_malformed_but_parsable_output(self):
        tr = self.make(FakeLLM("malformed"))
        rep = tr.run(units("a", "b"))
        self.assertEqual(rep.translated, 2)

    def test_unreachable_stops_gracefully_with_progress_saved(self):
        llm = FakeLLM(fail_after=1)
        tr = self.make(llm, batch_size=1)
        rep = tr.run(units("a", "b", "c"))
        self.assertTrue(rep.aborted)
        self.assertIn("connection refused", rep.abort_reason)
        self.assertEqual(rep.translated, 1)
        self.assertEqual(self.tm.get("a"), "KO:a")
        self.assertIsNone(self.tm.get("b"))

    def test_limit_and_dry_run(self):
        llm = FakeLLM()
        tr = self.make(llm, batch_size=10)
        rep = tr.run(units("a", "b", "c"), limit=2)
        self.assertEqual(rep.translated, 2)
        self.assertEqual(rep.pending, 3)
        llm2 = FakeLLM()
        tm_count = self.tm.count()
        tr2 = Translator(llm2, self.tm, rpgmaker_masker(), log=lambda s: None)
        rep2 = tr2.run(units("a", "b", "c", "d", "e"), dry_run=True)
        self.assertEqual(llm2.calls, [])
        self.assertEqual(rep2.to_translate, 3)
        self.assertEqual(self.tm.count(), tm_count)

    def test_dry_run_with_no_client(self):
        tm = TranslationMemory(":memory:")
        rep = Translator(None, tm, rpgmaker_masker(), log=lambda s: None).run(units("a"), dry_run=True)
        self.assertEqual(rep.to_translate, 1)

    def test_glossary_exact_match_skips_llm_and_manual_not_overwritten(self):
        llm = FakeLLM()
        g = Glossary(terms={"Potion": "물약"}, keep=["HP"])
        tr = self.make(llm, glossary=g)
        self.tm.put("Fixed", "고정", origin="manual")
        rep = tr.run(units("Potion", "HP", "Fixed", "Other"))
        self.assertEqual(self.tm.get("Potion"), "물약")
        self.assertEqual(self.tm.get("HP"), "HP")
        self.assertEqual(self.tm.get("Fixed"), "고정")
        self.assertEqual(rep.glossary, 2)
        self.assertEqual(len(llm.calls), 1)
        self.assertFalse(self.tm.put("Fixed", "LLM", origin="llm", overwrite=True))
        self.assertEqual(self.tm.get("Fixed"), "고정")

    def test_non_text_strings_skipped(self):
        llm = FakeLLM()
        tr = self.make(llm)
        rep = tr.run(units("100", "\\V[1]", "이미 한글"))
        self.assertEqual(rep.skipped_nontext, 3)
        self.assertEqual(llm.calls, [])

    def test_speaker_uses_translated_name_from_memory(self):
        llm = FakeLLM()
        tr = self.make(llm)
        self.tm.put("Alice", "앨리스", origin="manual")
        us = [Unit(file="f", kind="dialogue", source="Hello", speaker="Alice", prev="Earlier")]
        tr.run(us)
        payload = json.loads(llm.calls[0][1]["content"].split("Items:\n")[1])
        self.assertEqual(payload[0]["speaker"], "앨리스")
        self.assertEqual(payload[0]["prev"], "Earlier")


if __name__ == "__main__":
    unittest.main()
