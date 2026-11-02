import unittest

from sellstudio import config as cfg
from sellstudio import lintcheck as lc


def fake_phone():
    return "010-" + "1234" + "-" + "5678"


def fake_account():
    return "국민은행 " + "123456" + "789012"


class LintTests(unittest.TestCase):
    def setUp(self):
        self.rules = cfg.load_rules()

    def rules_of(self, text):
        return {(f.rule, f.severity) for f in lc.lint_text(text, self.rules)}

    def test_banned_word(self):
        self.assertIn(("금칙어", "error"), self.rules_of("전국 최저가로 내놓습니다"))

    def test_exaggeration_is_warning(self):
        got = self.rules_of("무조건 만족하실 완벽한 상태, 100% 정상")
        self.assertIn(("과장 표현", "warn"), got)
        self.assertNotIn(("금칙어", "error"), got)

    def test_phone_detected_and_masked(self):
        f = lc.lint_text("연락 %s 주세요" % fake_phone(), self.rules)
        pii = [x for x in f if x.rule == "개인정보"]
        self.assertTrue(pii)
        self.assertNotIn("5678", pii[0].snippet)
        self.assertEqual(pii[0].severity, "error")

    def test_account_detected(self):
        self.assertIn(("개인정보", "error"), self.rules_of("입금은 %s 로" % fake_account()))

    def test_address_and_invoice(self):
        got = self.rules_of("서울 강남구 테헤란로 123 / 송장번호 123456789012")
        self.assertIn(("주소", "error"), got)
        self.assertIn(("송장", "error"), got)

    def test_clean_text(self):
        self.assertEqual(lc.lint_text("깨끗하게 쓴 이어폰입니다. 플랫폼 채팅으로 문의 주세요.", self.rules), [])

    def test_repeated_punct(self):
        self.assertIn(("과장 표현", "warn"), self.rules_of("싸게 팝니다!!!"))

    def test_user_rules_extend(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "r.json"
            p.write_text(json.dumps({"banned": {"떨이": "내 규칙"}}, ensure_ascii=False), encoding="utf-8")
            rules = cfg.load_rules(str(p))
        self.assertTrue(lc.lint_text("떨이 처분", rules))
        self.assertTrue(lc.lint_text("최저가", rules))  # 기본 규칙도 유지

    def test_title_too_long_flagged(self):
        d = {"market": "daangn", "title": "가" * 50, "title_max": 40, "description": "설명", "todo": []}
        self.assertIn(("제목 길이", "error"), {(f.rule, f.severity) for f in lc.lint_draft(d, self.rules)})

    def test_html_text_extraction(self):
        text = lc.html_to_text("<style>.a{color:red}</style><p>안녕</p><img alt='연락 %s'>" % fake_phone())
        self.assertIn("안녕", text)
        self.assertNotIn("color", text)
        self.assertTrue(lc.lint_text(text, self.rules))


if __name__ == "__main__":
    unittest.main()
