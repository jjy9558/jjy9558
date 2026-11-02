# -*- coding: utf-8 -*-
import datetime as dt
import unittest

import samples as S
from helpers import TmpCase
from paperbutler.advice import KEEP, REFUND_DISCLAIMER


class TestRefunds(TmpCase):
    def refunds(self, b):
        return [dict(r) for r in b.db.refunds_between("2000-01-01", "2100-01-01", include_done=True)]

    def test_online_receipt_default_7_days(self):
        b = self.butler()
        r = b.add_text(S.RECEIPT_ONLINE, "r.txt")[0]
        rows = self.refunds(b)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["purchase_date"], rows[0]["policy_days"], rows[0]["deadline"], rows[0]["source"]),
                         ("2026-11-02", 7, "2026-11-09", "default_online"))
        self.assertEqual(r.refund_deadline, "2026-11-09")
        self.assertEqual(r.advice.verdict, KEEP)
        self.assertIn("11/9까지 환불/반품 가능", r.advice.todo)

    def test_policy_text_overrides_default(self):
        b = self.butler()
        r = b.add_text(S.RECEIPT_RETAIL_POLICY, "r.txt")[0]
        rows = self.refunds(b)
        self.assertEqual((rows[0]["policy_days"], rows[0]["deadline"], rows[0]["source"]),
                         (14, "2026-11-15", "text"))
        self.assertEqual(r.advice.verdict, KEEP)

    def test_no_refund_text_not_tracked(self):
        b = self.butler()
        r = b.add_text(S.RECEIPT_NO_REFUND, "r.txt")[0]
        self.assertEqual(self.refunds(b), [])
        self.assertTrue(any("불가" in n for n in r.notes))

    def test_offline_without_policy_not_tracked(self):
        b = self.butler()
        b.add_text(S.RECEIPT_CAFE, "r.txt")
        self.assertEqual(self.refunds(b), [])

    def test_food_delivery_not_tracked(self):
        b = self.butler()
        b.add_text(S.RECEIPT_RESTAURANT_ONLINE, "r.txt")
        self.assertEqual(self.refunds(b), [])

    def test_configurable_default_days(self):
        b = self.butler(default_refund_days=14)
        b.add_text(S.RECEIPT_ONLINE, "r.txt")
        self.assertEqual(self.refunds(b)[0]["deadline"], "2026-11-16")

    def test_duplicate_payment_does_not_double_track(self):
        b = self.butler()
        b.add_text(S.RECEIPT_ONLINE, "r.txt")
        b.add_text(S.RECEIPT_ONLINE.replace("20:15", "20:17"), "r2.txt")
        self.assertEqual(len(self.refunds(b)), 1)

    def test_due_report_includes_refund_window(self):
        b = self.butler()
        b.add_text(S.RECEIPT_ONLINE, "r.txt")
        rep = b.due(days=14)
        self.assertEqual(len(rep["refunds"]), 1)
        self.assertEqual(len(b.due(days=3)["refunds"]), 0)         # 11/9 는 7일 뒤

    def test_done_hides_refund(self):
        b = self.butler()
        r = b.add_text(S.RECEIPT_ONLINE, "r.txt")[0]
        b.mark_done(r.doc_id)
        self.assertEqual(len(b.due(days=14)["refunds"]), 0)

    def test_disclaimer_text(self):
        self.assertIn("법률 자문이 아닙니다", REFUND_DISCLAIMER)
        self.assertIn("가이드라인", REFUND_DISCLAIMER)
        self.assertIn("7일", REFUND_DISCLAIMER)


if __name__ == "__main__":
    unittest.main()
