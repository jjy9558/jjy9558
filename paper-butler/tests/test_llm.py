# -*- coding: utf-8 -*-
import datetime as dt
import io
import json
import os
import unittest
import urllib.error

import samples as S
from helpers import FakeLLM, TmpCase
from paperbutler.extract import Extraction, analyze
from paperbutler.llm import (
    LLMClient, LLMError, enrich_with_llm, is_local_url, merge_extraction, parse_json_object, sanitize_llm_fields,
)

T = dt.date(2026, 11, 2)


class TestMerge(unittest.TestCase):
    def test_llm_fills_missing_fields_rules_untouched(self):
        rule = analyze("[한국전력] 전기요금 청구서 납부기한 2026.11.25 자동이체 청구 안내", T)
        self.assertIsNone(rule.amount)
        fake = FakeLLM({"doc_type": "bill", "amount": 12340, "due_date": "2026-12-31", "issuer": "엉뚱한곳"})
        out = enrich_with_llm(rule, "본문", fake)
        self.assertEqual(out.amount, 12340)
        self.assertEqual(out.sources["amount"], "llm")
        self.assertEqual(out.due_date, "2026-11-25")          # 규칙 값 유지
        self.assertEqual(out.issuer, "한국전력공사")            # 규칙 값 유지
        self.assertTrue(any("LLM이 채운 항목" in n for n in out.notes))
        self.assertEqual(len(fake.calls), 1)

    def test_confident_complete_rules_skip_llm_entirely(self):
        rule = analyze(S.ELECTRIC, T)
        self.assertFalse(rule.needs_llm())
        fake = FakeLLM({"amount": 99999, "doc_type": "ad"})
        out = enrich_with_llm(rule, S.ELECTRIC, fake)
        self.assertEqual(fake.calls, [])
        self.assertEqual((out.amount, out.doc_type), (45230, "bill"))

    def test_rules_win_even_when_llm_disagrees(self):
        rule = analyze(S.SMS_PAY, T)                # 일시가 비어 LLM 호출됨
        self.assertTrue(rule.needs_llm())
        fake = FakeLLM({"doc_type": "receipt", "amount": 99999, "merchant": "다른곳",
                        "event_dt": "2026-11-02 10:11"})
        out = enrich_with_llm(rule, S.SMS_PAY, fake)
        self.assertEqual((out.doc_type, out.amount, out.merchant), ("card_sms", 4500, "스타벅스"))
        self.assertEqual(out.event_dt, "2026-11-02 10:11")
        self.assertTrue(out.time_known)

    def test_unknown_type_taken_from_llm(self):
        rule = analyze("ㅁㄴㅇㄹ 메모", T)
        out = merge_extraction(rule, {"doc_type": "receipt", "merchant": "동네마트", "amount": "12,340원",
                                      "event_dt": "2026-11-01"})
        self.assertEqual((out.doc_type, out.merchant, out.amount, out.event_dt),
                         ("receipt", "동네마트", 12340, "2026-11-01"))
        self.assertEqual(out.sources["doc_type"], "llm")
        self.assertGreater(out.confidence, rule.confidence)

    def test_type_override_only_when_rule_unsure(self):
        sure = Extraction(doc_type="bill", confidence=0.8, issuer="x", amount=1, due_date="2026-11-25")
        self.assertEqual(merge_extraction(sure, {"doc_type": "ad"}).doc_type, "bill")
        mid = Extraction(doc_type="bill", confidence=0.6)
        self.assertEqual(merge_extraction(mid, {"doc_type": "ad"}).doc_type, "bill")
        weak = Extraction(doc_type="bill", confidence=0.4)
        self.assertEqual(merge_extraction(weak, {"doc_type": "ad"}).doc_type, "ad")

    def test_booleans_only_when_rules_unsure(self):
        sure = Extraction(doc_type="bill", confidence=0.9, issuer="x", amount=1, due_date="2026-11-25")
        self.assertFalse(merge_extraction(sure, {"auto_debit": True}).auto_debit)
        unsure = Extraction(doc_type="bill", confidence=0.5)
        self.assertTrue(merge_extraction(unsure, {"auto_debit": True}).auto_debit)
        self.assertTrue(merge_extraction(Extraction(doc_type="card_sms", confidence=0.5),
                                         {"is_cancel": "true", "installment": 3}).is_cancel)

    def test_sanitize_rejects_garbage(self):
        bad = {"doc_type": "banana", "amount": "abc", "due_date": "2026-13-45", "event_dt": "어제",
               "billing_month": "2026-13", "auto_debit": "maybe", "installment": 999, "merchant": 123,
               "issuer": "정상\x00기관", "unknown_key": 1}
        out = sanitize_llm_fields(bad)
        self.assertEqual(out, {"issuer": "정상 기관"})
        self.assertEqual(sanitize_llm_fields("문자열"), {})
        self.assertEqual(sanitize_llm_fields({"amount": True}), {})
        self.assertEqual(sanitize_llm_fields({"amount": -5}), {})
        self.assertEqual(sanitize_llm_fields({"doc_type": "고지서", "amount": 1500.0}), {"doc_type": "bill", "amount": 1500})
        self.assertEqual(sanitize_llm_fields({"event_dt": "2026-11-02T14:32:00"}), {"event_dt": "2026-11-02 14:32"})

    def test_llm_error_keeps_rule_result(self):
        rule = analyze("ㅁㄴㅇㄹ", T)
        out = enrich_with_llm(rule, "ㅁㄴㅇㄹ", FakeLLM(error=LLMError("down")))
        self.assertEqual(out.doc_type, "unknown")
        self.assertTrue(any("LLM 보완 실패" in n for n in out.notes))

    def test_text_sent_to_llm_is_masked(self):
        text = "메모 카드 1234-5678-9012-3456 전화 010-1234-5678 주민 900101-1234567 계좌 110-123-456789"
        fake = FakeLLM({})
        enrich_with_llm(analyze(text, T), text, fake)
        sent = fake.calls[0]
        for raw in ("9012-3456", "010-1234-5678", "1234567", "456789"):
            self.assertNotIn(raw, sent)

    def test_none_client(self):
        rule = analyze("ㅁㄴㅇㄹ", T)
        self.assertIs(enrich_with_llm(rule, "x", None), rule)


class TestButlerWithLLM(TmpCase):
    def test_gap_filled_by_llm_in_pipeline(self):
        fake = FakeLLM({"event_dt": "2026-11-02 10:11"})
        b = self.butler(llm=fake)
        r = b.add_text(S.SMS_PAY, "t.txt")[0]
        self.assertEqual(len(fake.calls), 1)
        led = b.ledger("2026-11")
        self.assertEqual(led["rows"][0]["ts"], "2026-11-02 10:11")
        self.assertFalse(any("오늘 날짜" in n for n in r.notes))

    def test_use_llm_false(self):
        fake = FakeLLM({"event_dt": "2026-11-02 10:11"})
        b = self.butler(llm=fake)
        b.add_text(S.SMS_PAY, "t.txt", use_llm=False)
        self.assertEqual(fake.calls, [])

    def test_no_llm_configured_still_works(self):
        b = self.butler()
        self.assertIsNone(b.llm)
        self.assertEqual(b.add_text(S.SMS_PAY, "t.txt")[0].status, "added")

    def test_remote_llm_url_disabled_by_default(self):
        b = self.butler(llm_url="https://api.example.com/v1")
        self.assertIsNone(b.llm)
        self.home = self.home.parent / "h2"
        b2 = self.butler(llm_url="http://localhost:8080/v1", llm_model="m")
        self.assertIsNotNone(b2.llm)


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, content=None, exc=None):
        self.content, self.exc, self.requests = content, exc, []

    def open(self, req, timeout=None):
        self.requests.append(req)
        if self.exc:
            raise self.exc
        return FakeResp({"choices": [{"message": {"content": self.content}}]})


class TestClient(TmpCase):
    def test_is_local_url(self):
        for u in ("http://localhost:8080/v1", "http://127.0.0.1:1234", "http://192.168.0.5:8000/v1",
                  "http://10.0.0.2/v1", "http://[::1]:8000", "http://gpu.local:8000/v1"):
            self.assertTrue(is_local_url(u), u)
        for u in ("https://api.openai.com/v1", "http://8.8.8.8/v1", "http://example.com", ""):
            self.assertFalse(is_local_url(u), u)

    def test_remote_refused_unless_allowed(self):
        with self.assertRaises(LLMError):
            LLMClient("https://api.openai.com/v1", "m")
        LLMClient("https://api.openai.com/v1", "m", allow_remote=True)
        with self.assertRaises(LLMError):
            LLMClient("", "m")

    def test_chat_request_shape_and_extract(self):
        op = FakeOpener('```json\n{"doc_type": "bill", "amount": 1000}\n```')
        c = LLMClient("http://localhost:8080/v1/", "my-model", api_key="k", opener=op)
        out = c.extract_fields("본문 010-1234-5678")
        self.assertEqual(out, {"doc_type": "bill", "amount": 1000})
        req = op.requests[0]
        self.assertEqual(req.full_url, "http://localhost:8080/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer k")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "my-model")
        self.assertEqual(body["temperature"], 0.0)
        self.assertEqual(body["messages"][0]["role"], "system")

    def test_vision_transcribe_builds_data_uri(self):
        op = FakeOpener("한국전력 청구서\n12,340원")
        c = LLMClient("http://localhost:8080/v1", "txt", vision_model="vlm", opener=op)
        img = self.write("a.png", b"\x89PNG\r\n\x1a\n0000", binary=True)
        text = c.transcribe_image(str(img))
        self.assertIn("12,340원", text)
        body = json.loads(op.requests[0].data.decode("utf-8"))
        self.assertEqual(body["model"], "vlm")
        part = body["messages"][0]["content"][1]
        self.assertTrue(part["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_network_error_becomes_llmerror(self):
        c = LLMClient("http://localhost:8080/v1", "m", opener=FakeOpener(exc=urllib.error.URLError("x")))
        with self.assertRaises(LLMError):
            c.extract_fields("x")

    def test_parse_json_object(self):
        self.assertEqual(parse_json_object('앞말 {"a": 1} 뒷말'), {"a": 1})
        for bad in ("", "no json", "{broken", "[1,2]"):
            with self.assertRaises(LLMError):
                parse_json_object(bad)


if __name__ == "__main__":
    unittest.main()
