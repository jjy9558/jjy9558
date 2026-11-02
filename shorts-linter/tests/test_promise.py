import io
import json
import unittest
from unittest import mock

from shortslint import promise as pm
from shortslint.config import load_config

TRANSCRIPT = ("시어머니가 갑자기 집에 찾아왔다. 그날 나는 세 가지 비밀을 알게 됐다. "
              "첫째 통장, 둘째 반지, 셋째 편지. 결국 남편은 사과했다. 알고 보니 민수 씨가 전부 알고 있었다.")


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_keywords_strip_josa(self):
        self.assertIn("시어머니", pm.keywords("시어머니가 찾아온"))
        self.assertNotIn("쇼츠", pm.keywords("쇼츠 영상"))

    def test_good_match(self):
        s = pm.score_promise("시어머니가 숨긴 3가지 비밀 결말", "", [], TRANSCRIPT, self.cfg)
        self.assertGreaterEqual(s["score"], 0.6)
        self.assertEqual(s["numbers_missing"], [])
        self.assertEqual(s["words_missing"], [])

    def test_bad_match(self):
        s = pm.score_promise("회사 사장이 숨긴 7가지 진실, 충격 반전", "", [], "오늘은 날씨가 좋아서 산책을 했다", self.cfg)
        self.assertLess(s["score"], 0.2)
        self.assertEqual(s["numbers_missing"], [7])
        self.assertEqual(s["words_missing"], ["반전"])

    def test_names(self):
        s = pm.score_promise("'지훈'이 말한 진실 민수 씨 이야기", "", [], TRANSCRIPT, self.cfg)
        self.assertIn("지훈", s["names_missing"])
        self.assertNotIn("민수", s["names_missing"])

    def test_number_forms(self):
        self.assertTrue(pm.number_present(3, "세 가지"))
        self.assertTrue(pm.number_present(1000, "1,000원"))
        self.assertFalse(pm.number_present(3, "13명"))

    def test_results_status(self):
        s = pm.score_promise("회사 사장이 숨긴 7가지 진실", "", [], "오늘은 날씨가 좋아서 산책", self.cfg)
        rs = {r.name: r for r in pm.results_from_score(s, self.cfg)}
        self.assertEqual(rs["키워드 일치도"].status, "fail")
        self.assertEqual(rs["약속 항목"].status, "warn")

    def test_thumbnail_text_counts(self):
        s = pm.score_promise("제목", "결말 반전 충격", [], "결국 끝", self.cfg)
        self.assertIn("충격", s["missing"])

    def test_skip_no_transcript(self):
        r = pm.check_promise({"title": "시어머니 비밀"}, "", self.cfg)
        self.assertEqual(r[0].status, "skip")


class LlmTests(unittest.TestCase):
    def _opener(self, content):
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()

        class R(io.BytesIO):
            def __enter__(s): return s
            def __exit__(s, *a): return False
        return lambda req, timeout=0: R(body)

    def test_llm_used(self):
        cfg = load_config()
        cfg["llm"]["enabled"] = True
        op = self._opener('판정: {"score": 30, "missing": ["반전"], "verdict": "약속 불일치"}')
        rs = {r.name: r for r in pm.check_promise({"title": "시어머니 비밀 결말"}, TRANSCRIPT, cfg, op)}
        self.assertEqual(rs["LLM 판정"].status, "fail")
        self.assertIn("키워드 일치도", rs)

    def test_llm_fallback(self):
        cfg = load_config()
        cfg["llm"]["enabled"] = True

        def boom(req, timeout=0):
            raise OSError("connection refused")
        rs = {r.name: r for r in pm.check_promise({"title": "시어머니 비밀 결말"}, TRANSCRIPT, cfg, boom)}
        self.assertEqual(rs["LLM 판정"].status, "skip")
        self.assertIn("키워드 일치도", rs)

    def test_llm_off_by_default(self):
        rs = {r.name for r in pm.check_promise({"title": "시어머니 비밀 결말"}, TRANSCRIPT, load_config())}
        self.assertNotIn("LLM 판정", rs)


if __name__ == "__main__":
    unittest.main()
