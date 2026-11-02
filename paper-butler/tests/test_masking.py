# -*- coding: utf-8 -*-
import io
import logging
import unittest

import samples as S
from paperbutler.masking import MaskingFilter, mask_id, mask_text, redact_document


class TestMasking(unittest.TestCase):
    def test_pii_sample_is_masked(self):
        out = mask_text(S.PII)
        for raw in ("900101-1234567", "1234567", "010-1234-5678", "02-123-4567", "1234-5678-9012-3456",
                    "5678-9012", "123456-04-123456", "110-123-456789", "0123-4567-89"):
            self.assertNotIn(raw, out, raw)

    def test_public_info_kept(self):
        out = mask_text(S.PII)
        self.assertIn("1588-1234", out)          # 대표번호
        self.assertIn("123-45-67890", out)       # 사업자등록번호

    def test_formats(self):
        self.assertIn("******-*******", mask_text("주민번호 900101-1234567"))
        self.assertIn("010-****-5678", mask_text("전화 010-1234-5678"))
        self.assertIn("010-****-5678", mask_text("전화 01012345678"))
        self.assertIn("010-****-5678", mask_text("전화 010.1234.5678"))
        self.assertIn("02-****-4567", mask_text("전화 02-123-4567"))
        self.assertIn("+82-**-****-5678", mask_text("+82-10-1234-5678"))
        self.assertIn("****-****-****-3456", mask_text("1234-5678-9012-3456"))
        self.assertIn("**** **** **** 3456", mask_text("1234 5678 9012 3456"))
        # Luhn 을 통과하는 16자리는 마스킹
        self.assertEqual(mask_text("4111111111111111"), "************1111")
        self.assertEqual(mask_text("계좌번호: 1234567896789"), "계좌번호: *********6789")

    def test_account_context_without_hyphen(self):
        out = mask_text("입금계좌 국민은행 12345678901234")
        self.assertNotIn("12345678901234", out)
        self.assertTrue(out.endswith("1234"))

    def test_rrn_without_hyphen_needs_valid_date(self):
        self.assertNotIn("9001011234567", mask_text("9001011234567"))
        self.assertEqual(mask_text("9913451234567"), "9913451234567")   # 월=13 이면 주민번호 아님

    def test_non_pii_untouched(self):
        for s in ("납부기한 2026-11-25", "2026-11-02 14:32", "12,340원", "11/02 14:32", "청구금액 1,234,567원",
                  "승인번호 12345678", "1234승인", "2026.11.25", "사용량 312kWh"):
            self.assertEqual(mask_text(s), s, s)

    def test_labeled_ids(self):
        out = mask_text("고객번호: 0123-4567-89 납부자번호 1234567890 계약번호:ABC-12345-67")
        self.assertNotIn("0123-4567-89", out)
        self.assertNotIn("1234567890", out)
        self.assertNotIn("12345-67", out)
        self.assertTrue(out.startswith("고객번호: "))

    def test_mask_id(self):
        self.assertEqual(mask_id("0123-4567-89"), "****-***7-89")
        self.assertEqual(mask_id("1234567890"), "*******890")
        self.assertEqual(mask_id(""), "")
        self.assertNotIn("1234", mask_id("12345"))

    def test_idempotent(self):
        once = mask_text(S.PII)
        self.assertEqual(mask_text(once), once)
        for s in (S.ELECTRIC, S.TELECOM, S.CARD_BILL):
            self.assertEqual(mask_text(mask_text(s)), mask_text(s))

    def test_extra_secrets(self):
        self.assertNotIn("ABCDEF123456", mask_text("번호 ABCDEF123456 입니다", ["ABCDEF123456"]))

    def test_redact_document_header(self):
        out = redact_document(S.PII)
        self.assertTrue(out.startswith("[개인정보 마스킹 사본"))
        self.assertNotIn("900101", out)

    def test_logging_filter_masks(self):
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.addFilter(MaskingFilter())
        lg = logging.getLogger("pbtest.mask")
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
        self.addCleanup(lg.removeHandler, h)
        lg.info("처리 중 %s 전화 %s", "카드 1234-5678-9012-3456", "010-9999-8888")
        text = buf.getvalue()
        self.assertNotIn("9012", text)
        self.assertNotIn("9999", text)
        self.assertIn("010-****-8888", text)


if __name__ == "__main__":
    unittest.main()
