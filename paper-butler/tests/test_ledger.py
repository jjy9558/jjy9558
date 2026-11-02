# -*- coding: utf-8 -*-
import csv
import io
import os
import unittest

import samples as S
from helpers import TmpCase, read_bytes, read_text


def sms(kind, amount, when, merchant, card="신한카드"):
    return "[%s] %s %s원 일시불 %s %s" % (card, kind, "{:,}".format(amount), when, merchant)


class TestLedger(TmpCase):
    def add(self, b, text):
        res = b.add_text(text, "t.txt")
        self.assertEqual(len(res), 1)
        return res[0]

    def test_basic_add_and_month_total(self):
        b = self.butler()
        r = self.add(b, S.SMS_SAMSUNG)
        self.assertEqual((r.status, r.payment_status), ("added", "inserted"))
        led = b.ledger("2026-11")
        self.assertEqual((led["total"], led["count"]), (12340, 1))
        self.assertEqual(led["totals"][0][0], "식비")
        self.assertEqual(b.ledger("2026-10")["count"], 0)

    def test_same_file_twice_is_duplicate_file(self):
        b = self.butler()
        self.add(b, S.SMS_SAMSUNG)
        r = self.add(b, S.SMS_SAMSUNG)
        self.assertEqual(r.status, "duplicate")
        self.assertEqual(b.ledger("2026-11")["count"], 1)
        self.assertEqual(b.db.count_documents(), 1)

    def test_dedupe_same_merchant_amount_within_minutes(self):
        b = self.butler()
        self.add(b, sms("승인", 12340, "11/02 14:32", "스타벅스 강남점"))
        r = self.add(b, sms("승인", 12340, "11/02 14:35", "스타벅스강남점", "삼성카드"))
        self.assertEqual(r.payment_status, "duplicate")
        self.assertTrue(any("중복" in n for n in r.notes))
        self.assertIn("중복", r.advice.todo)
        self.assertEqual(b.ledger("2026-11")["count"], 1)

    def test_not_duplicate_cases(self):
        b = self.butler()
        self.add(b, sms("승인", 12340, "11/02 14:32", "스타벅스 강남점"))
        far = self.add(b, sms("승인", 12340, "11/02 14:50", "스타벅스 강남점"))       # 18분 뒤
        other_amt = self.add(b, sms("승인", 12350, "11/02 14:33", "스타벅스 강남점"))
        other_shop = self.add(b, sms("승인", 12340, "11/02 14:33", "이디야 역삼점"))
        for r in (far, other_amt, other_shop):
            self.assertEqual(r.payment_status, "inserted")
        self.assertEqual(b.ledger("2026-11")["count"], 4)

    def test_dedupe_window_configurable(self):
        b = self.butler(dedupe_minutes=30)
        self.add(b, sms("승인", 5000, "11/02 10:00", "가나다식당"))
        r = self.add(b, sms("승인", 5000, "11/02 10:25", "가나다식당", "삼성카드"))
        self.assertEqual(r.payment_status, "duplicate")

    def test_dedupe_can_be_disabled(self):
        b = self.butler(dedupe_minutes=0)
        self.add(b, sms("승인", 5000, "11/02 10:00", "가나다식당"))
        r = self.add(b, sms("승인", 5000, "11/02 10:01", "가나다식당", "삼성카드"))
        self.assertEqual(r.payment_status, "inserted")

    def test_receipt_and_sms_for_same_purchase_dedupe(self):
        b = self.butler()
        r1 = self.add(b, S.RECEIPT_CAFE)
        r2 = self.add(b, sms("승인", 4500, "11/02 08:56", "스타벅스", "삼성카드"))
        self.assertEqual((r1.payment_status, r2.payment_status), ("inserted", "duplicate"))
        self.assertEqual(b.ledger("2026-11")["total"], 4500)

    def test_cancel_matches_approval(self):
        b = self.butler()
        self.add(b, S.SMS_SAMSUNG)
        r = self.add(b, S.SMS_HYUNDAI_CANCEL)
        self.assertEqual(r.payment_status, "canceled_match")
        led = b.ledger("2026-11")
        self.assertEqual((led["total"], led["count"]), (0, 0))
        self.assertEqual(len(led["rows"]), 2)          # 원장에는 두 줄이 남아 있음(투명성)
        self.assertEqual([x["counted"] for x in led["rows"]], [False, False])

    def test_cancel_before_approval_processing_order(self):
        b = self.butler()
        r1 = self.add(b, S.SMS_HYUNDAI_CANCEL)           # 취소 문자가 먼저 처리됨(인박스 정렬 순서)
        self.assertEqual(r1.payment_status, "unmatched_cancel")
        self.assertEqual(b.ledger("2026-11")["total"], -12340)
        r2 = self.add(b, S.SMS_SAMSUNG)
        self.assertEqual(r2.payment_status, "canceled_match")
        self.assertEqual(b.ledger("2026-11")["total"], 0)

    def test_cancel_in_later_month_matches_and_removes_original(self):
        b = self.butler()
        self.add(b, sms("승인", 50000, "10/31 21:00", "무신사"))
        r = self.add(b, sms("취소", 50000, "11/02 09:00", "무신사"))
        self.assertEqual(r.payment_status, "canceled_match")
        self.assertEqual(b.ledger("2026-10")["total"], 0)
        self.assertEqual(b.ledger("2026-11")["total"], 0)

    def test_unmatched_cancel_counts_as_refund(self):
        b = self.butler()
        r = self.add(b, sms("취소", 7000, "11/02 09:00", "어딘가"))
        self.assertEqual(r.payment_status, "unmatched_cancel")
        led = b.ledger("2026-11")
        self.assertEqual(led["total"], -7000)

    def test_cancel_requires_same_amount_and_merchant(self):
        b = self.butler()
        self.add(b, sms("승인", 12340, "11/02 14:32", "스타벅스 강남점"))
        r = self.add(b, sms("취소", 12000, "11/02 14:40", "스타벅스 강남점"))
        self.assertEqual(r.payment_status, "unmatched_cancel")
        r = self.add(b, sms("취소", 12340, "11/02 14:41", "이디야"))
        self.assertEqual(r.payment_status, "unmatched_cancel")

    def test_reapproval_after_cancel_counts(self):
        b = self.butler()
        self.add(b, sms("승인", 9000, "11/02 14:00", "서점"))
        self.add(b, sms("취소", 9000, "11/02 14:05", "서점"))
        r = self.add(b, sms("승인", 9000, "11/02 15:10", "서점"))
        self.assertEqual(r.payment_status, "inserted")
        self.assertEqual(b.ledger("2026-11")["total"], 9000)

    def test_duplicate_cancel_message(self):
        b = self.butler()
        self.add(b, sms("승인", 9000, "11/02 14:00", "서점"))
        self.add(b, sms("취소", 9000, "11/02 14:05", "서점"))
        r = self.add(b, sms("취소", 9000, "11/02 14:06", "서점", "삼성카드"))
        self.assertEqual(r.payment_status, "duplicate")
        self.assertEqual(b.ledger("2026-11")["total"], 0)

    def test_installment_amount_is_total_once(self):
        b = self.butler()
        self.add(b, S.SMS_KB)
        led = b.ledger("2026-11")
        self.assertEqual(led["total"], 120000)
        self.assertEqual(led["rows"][0]["installment"], 3)

    def test_monthly_category_totals(self):
        b = self.butler()
        for t in (S.SMS_SAMSUNG, S.SMS_NH, S.SMS_KB_CHECK, S.SMS_KB, S.SMS_NETFLIX):
            self.add(b, t)
        led = b.ledger("2026-11")
        totals = {k: tuple(v) for k, v in led["totals"]}
        self.assertEqual(totals["식비"], (27340, 2))
        self.assertEqual(totals["교통"], (7500, 1))
        self.assertEqual(totals["쇼핑"], (120000, 1))
        self.assertEqual(totals["구독"], (17000, 1))
        self.assertEqual(led["total"], 27340 + 7500 + 120000 + 17000)
        amounts = [v[0] for _, v in led["totals"]]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_missing_datetime_uses_today_and_notes(self):
        b = self.butler()
        r = self.add(b, S.SMS_PAY)
        self.assertTrue(any("오늘 날짜" in n for n in r.notes))
        self.assertEqual(b.ledger("2026-11")["total"], 4500)

    def test_overseas_not_in_ledger(self):
        b = self.butler()
        r = self.add(b, S.SMS_OVERSEAS)
        self.assertEqual(r.status, "added")
        self.assertEqual(r.payment_status, "")
        self.assertEqual(len(b.ledger("2026-11")["rows"]), 0)

    def test_csv_export_bom_and_content(self):
        b = self.butler()
        self.add(b, S.SMS_SAMSUNG)
        self.add(b, S.SMS_HYUNDAI_CANCEL)
        self.add(b, S.SMS_KB)
        out = os.path.join(self.tmp, "l.csv")
        n = b.export_ledger_csv(out, "2026-11")
        self.assertEqual(n, 3)
        raw = read_bytes(out)
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", raw)
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        self.assertEqual(rows[0][:4], ["일시", "가맹점", "구분", "금액"])
        body = {r[3] + r[2]: r for r in rows[1:]}
        self.assertEqual(body["12340승인"][7], "N")      # 취소됨 -> 집계 제외
        self.assertEqual(body["-12340취소"][7], "N")     # 매칭된 취소
        self.assertEqual(body["120000승인"][5], "3개월")
        self.assertEqual(body["120000승인"][7], "Y")

    def test_csv_formula_injection_escaped(self):
        b = self.butler()
        self.add(b, "[신한카드] 승인 1,000원 일시불 11/02 10:00 =1+1가게")
        out = os.path.join(self.tmp, "l.csv")
        b.export_ledger_csv(out)
        text = read_text(out, "utf-8-sig")
        self.assertIn("'=1+1가게", text)

    def test_docs_csv(self):
        b = self.butler()
        self.add(b, S.ELECTRIC)
        out = os.path.join(self.tmp, "d.csv")
        self.assertEqual(b.export_docs_csv(out), 1)
        text = read_text(out, "utf-8-sig")
        self.assertIn("한국전력공사", text)
        self.assertNotIn("0123-4567-89", text)

    def test_original_stored_by_year_month_and_private(self):
        b = self.butler()
        r = self.add(b, S.SMS_SAMSUNG)
        p = r.stored_path
        self.assertIn(os.path.join("docs", "2026", "11"), p)
        self.assertEqual(read_text(p), S.SMS_SAMSUNG)
        if os.name == "posix":  # Windows has no POSIX permission bits
            self.assertEqual(os.stat(p).st_mode & 0o077, 0)

    def test_db_text_is_masked_but_customer_no_kept(self):
        b = self.butler()
        r = self.add(b, S.ELECTRIC + "\n환불계좌 110-123-456789 카드 1234-5678-9012-3456")
        row = b.db.get_document(r.doc_id)
        self.assertNotIn("9012-3456", row["text"])
        self.assertNotIn("456789", row["text"].replace("***", ""))
        self.assertEqual(row["customer_no"], "0123-4567-89")      # 저장은 원본
        self.assertNotIn("0123-4567-89", row["text"])              # 본문 사본은 마스킹
        d = b.show(r.doc_id)
        self.assertEqual(d["customer_no_masked"], "****-***7-89")

    def test_find(self):
        b = self.butler()
        self.add(b, S.GAS)
        self.add(b, S.ELECTRIC)
        self.add(b, S.SMS_SAMSUNG)
        hits = b.find("가스")
        self.assertEqual(len(hits), 1)
        self.assertIn("가스", hits[0]["snippet"] + hits[0]["summary"])
        self.assertEqual(len(b.find("스타벅스")), 1)
        self.assertEqual(len(b.find("서울도시가스 요금")), 1)       # AND 검색
        self.assertEqual(b.find("없는단어zzz"), [])
        self.assertEqual(b.find("%"), [])                           # LIKE 와일드카드 이스케이프
        self.assertEqual(b.find("   "), [])

    def test_find_snippet_is_masked(self):
        b = self.butler()
        self.add(b, "[삼성카드] 승인 5,000원 일시불 11/02 10:00 가게 문의 010-1234-5678")
        h = b.find("문의")[0]
        self.assertNotIn("1234-5678", h["snippet"])


if __name__ == "__main__":
    unittest.main()
