# -*- coding: utf-8 -*-
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime

from vjournal import llm as L
from vjournal.report import export_month
from vjournal.server import load_or_create_token, make_server
from vjournal.store import Journal
from tests.helpers import Base, at

TOKEN = "test-token-12345"


class ApiTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.db = os.path.join(d, "j.db")
        self.srv = make_server(self.db, TOKEN, "127.0.0.1", 0, cors_origin="https://pc.example",
                               now=lambda: datetime(2026, 11, 2, 21, 0))
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.t.join(5)

    def call(self, method, path, body=None, token=TOKEN, headers=None, raw=None):
        h = dict(headers or {})
        if token:
            h["Authorization"] = "Bearer " + token
        data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
        if data is not None:
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8")), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8")), e.headers

    def test_binds_loopback(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        self.assertNotEqual(self.port, 0)

    def test_auth_required(self):
        self.assertEqual(self.call("POST", "/entry", {"text": "x"}, token=None)[0], 401)
        self.assertEqual(self.call("POST", "/entry", {"text": "x"}, token="wrong-token-123")[0], 401)
        self.assertEqual(self.call("GET", "/morning", token=None)[0], 401)
        code, _, _ = self.call("GET", "/health", token=None)
        self.assertEqual(code, 200)
        code, _, _ = self.call("POST", "/entry", {"text": "ok"}, token=None, headers={"X-VJ-Token": TOKEN})
        self.assertEqual(code, 201)

    def test_post_entry_and_search(self):
        code, res, _ = self.call("POST", "/entry", {"text": "이번 주에 블로그 2개 쓴다 #다짐", "mood": 4})
        self.assertEqual(code, 201)
        self.assertEqual(res["tags"], ["다짐"])
        self.assertEqual(res["goals_created"][0]["target"], 2)
        code, res, _ = self.call("GET", "/search?q=" + urllib.request.quote("블로그"))
        self.assertEqual(code, 200)
        self.assertEqual(len(res["results"]), 1)
        j = Journal(self.db)
        self.addCleanup(j.close)
        self.assertEqual(j.entries()[0]["mood"], 4)

    def test_plain_text_body(self):
        code, res, _ = self.call("POST", "/entry", raw="평문 본문 #메모".encode("utf-8"),
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
        self.assertEqual(code, 201)
        self.assertEqual(res["tags"], ["메모"])

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/entry", {"text": "  "})[0], 400)
        self.assertEqual(self.call("POST", "/entry", {"text": "x", "mood": 9})[0], 400)
        self.assertEqual(self.call("POST", "/entry", raw=b"not json")[0], 400)
        self.assertEqual(self.call("POST", "/entry", raw=b"[1]")[0], 400)
        self.assertEqual(self.call("POST", "/entry", {"text": "x" * 70000})[0], 413)
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        self.assertEqual(self.call("GET", "/search")[0], 400)

    def test_checkout_and_morning(self):
        code, res, _ = self.call("GET", "/checkout/questions")
        self.assertEqual([q["key"] for q in res["questions"]], ["win", "blocked", "next"])
        code, res, _ = self.call("POST", "/checkout", {"answers": {"next": "PR 올리기", "win": "배포"}})
        self.assertEqual((code, res["saved"]), (201, 2))
        # 서버 시계가 11-02 로 고정이라 같은 날 답변은 '어제'가 아니다 -> source_day 없음
        code, res, _ = self.call("GET", "/morning")
        self.assertEqual(code, 200)
        self.assertIn("text", res)
        self.assertIsNone(res["source_day"])

    def test_cors_preflight(self):
        req = urllib.request.Request("http://127.0.0.1:%d/entry" % self.port, method="OPTIONS")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 204)
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], "https://pc.example")
            self.assertIn("X-VJ-Token", r.headers["Access-Control-Allow-Headers"])

    def test_short_token_rejected(self):
        with self.assertRaises(ValueError):
            make_server(self.db, "abc", port=0)

    def test_token_file(self):
        p = os.path.join(tempfile.mkdtemp(), "sub", "token")
        t, created = load_or_create_token(p)
        self.assertTrue(created)
        t2, created2 = load_or_create_token(p)
        self.assertEqual((t, False), (t2, created2))
        if os.name == "posix":  # Windows has no POSIX permission bits
            self.assertEqual(os.stat(p).st_mode & 0o077, 0)


class FakeClient(object):
    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.calls = reply, exc, 0

    def chat(self, messages, **kw):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.reply


class LLMTests(Base):
    def test_llm_adds_declarations_rules_missed(self):
        reply = '```json\n[{"title":"기타 연습 20분","period":"day","target":20,"unit":"분","deadline":null}]\n```'
        j = self.mk(llm=FakeClient(reply))
        r = j.add_entry("앞으로는 틈날 때마다 기타를 잡아보려 한다")   # 규칙으론 못 잡는 표현
        self.assertEqual([g["title"] for g in r["goals_created"]], ["기타 연습 20분"])
        self.assertEqual(j.goals()[0]["period"], "day")

    def test_llm_does_not_duplicate_rule_hits(self):
        reply = '[{"title":"블로그 2개 쓴다","period":"once","target":2,"unit":"개","deadline":"2026-11-08"}]'
        j = self.mk(llm=FakeClient(reply))
        r = j.add_entry("이번 주에 블로그 2개 쓴다")
        self.assertEqual(len(r["goals_created"]), 1)

    def test_fallback_on_failure_or_garbage(self):
        for client in (FakeClient(exc=RuntimeError("down")), FakeClient("완전 엉뚱한 말"),
                       FakeClient('{"not":"a list"}'), FakeClient('[{"title": 5}, "x", null]')):
            j = self.mk(llm=client)
            r = j.add_entry("금요일까지 보고서 끝낸다")
            self.assertEqual(len(r["goals_created"]), 1)   # 규칙 기반은 그대로 동작
            self.assertEqual(client.calls, 1)
        self.assertEqual(L.llm_extract_goals(None, "x", date(2026, 11, 2)), [])

    def test_summarize_lines_validation(self):
        self.assertEqual(L.llm_summarize_lines(FakeClient("1. 가\n2) 나\n- 다"), "ctx"), ["가", "나", "다"])
        self.assertIsNone(L.llm_summarize_lines(FakeClient("가\n나"), "ctx"))
        self.assertIsNone(L.llm_summarize_lines(FakeClient(exc=OSError()), "ctx"))
        self.assertIsNone(L.llm_summarize_lines(None, "ctx"))

    def test_parse_json_block(self):
        self.assertEqual(L.parse_json_block('잡설 [1,2] 끝'), [1, 2])
        self.assertIsNone(L.parse_json_block("no json"))
        self.assertIsNone(L.parse_json_block(None))

    def test_client_refuses_remote_and_env(self):
        with self.assertRaises(L.LLMError):
            L.LLMClient("http://example.com/v1")
        self.assertIsNotNone(L.LLMClient("http://127.0.0.1:8080/v1"))
        self.assertIsNone(L.from_env({}))
        self.assertIsNotNone(L.from_env({"VJ_LLM_BASE_URL": "http://localhost:1/v1"}))

    def test_client_unreachable_is_llmerror(self):
        c = L.LLMClient("http://127.0.0.1:1/v1", timeout=1)
        with self.assertRaises(L.LLMError):
            c.chat([{"role": "user", "content": "hi"}])


class ExportTests(Base):
    def test_monthly_export(self):
        j = self.mk()
        at(j, 2026, 10, 30, 22)
        j.add_entry("10월 마지막 기록", auto_goals=False)
        at(j, 2026, 11, 2, 21)
        j.add_entry("이번 주에 블로그 2개 쓴다 #다짐", mood=4)
        j.add_entry("막힌 건 CI", kind="checkout", qkey="blocked")
        at(j, 2026, 12, 1)
        j.add_entry("12월 기록", auto_goals=False)
        md = export_month(j, "2026-11")
        self.assertIn("# 2026년 11월 기록", md)
        self.assertIn("## 2026-11-02", md)
        self.assertIn("기분 4/5", md)
        self.assertIn("**막힌 것**", md)
        self.assertIn("#다짐(1)", md)
        self.assertIn("이 달의 선언", md)
        self.assertNotIn("10월 마지막", md)
        self.assertNotIn("12월 기록", md)
        with self.assertRaises(ValueError):
            export_month(j, "2026/11")


if __name__ == "__main__":
    unittest.main()
