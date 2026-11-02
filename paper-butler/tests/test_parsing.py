# -*- coding: utf-8 -*-
import datetime as dt
import unittest

from paperbutler.parsing import (
    find_billing_month, find_customer_no, find_datetime, iter_amounts, normalize_text, parse_amount_str,
    resolve_date, scan_dates,
)

D = dt.date


class TestAmounts(unittest.TestCase):
    def test_amount_strings(self):
        cases = {
            "12,340원": 12340, "12340원": 12340, "12,340 원": 12340, "1,234,567원": 1234567,
            "₩5,000": 5000, "₩ 5,000": 5000, "KRW 12,000": 12000, "5천원": 5000, "5만원": 50000,
            "1만2천원": 12000, "3만 5천원": 35000, "12만 3,400원": 123400, "1.5만원": 15000,
            "2천만원": 20000000, "1억 2천만원": 120000000, "1억2,345만 6,789원": 123456789,
            "12,340": 12340, "금액 7,500원정": 7500, "-12,340원": 12340,
        }
        for s, want in cases.items():
            with self.subTest(s=s):
                self.assertEqual(parse_amount_str(s), want)

    def test_not_amounts(self):
        for s in ("100 원본", "원래 가격", "2026.11.25", "11:30"):
            with self.subTest(s=s):
                self.assertEqual(iter_amounts(s), [])

    def test_scan_in_sentence(self):
        toks = iter_amounts("11월 25일까지 12,340원을 납부, 연체 시 13,000원")
        self.assertEqual([t.value for t in toks], [12340, 13000])

    def test_fullwidth_normalized(self):
        t = normalize_text("１２，３４０원 ㈜가나다")
        self.assertIn("12,340원", t)
        self.assertIn("(주)가나다", t)

    def test_garbage(self):
        self.assertIsNone(parse_amount_str("없음"))
        self.assertIsNone(parse_amount_str(None))


class TestDates(unittest.TestCase):
    def kinds(self, s):
        return [(t.kind, t.year, t.month, t.day) for t in scan_dates(s)]

    def test_formats(self):
        cases = {
            "2026.11.25": [("ymd", 2026, 11, 25)],
            "2026-11-25": [("ymd", 2026, 11, 25)],
            "2026/11/25": [("ymd", 2026, 11, 25)],
            "2026년 11월 25일": [("ymd", 2026, 11, 25)],
            "2026.11.25.(수)": [("ymd", 2026, 11, 25)],
            "26.11.25": [("ymd", 2026, 11, 25)],
            "11월 25일까지": [("md", None, 11, 25)],
            "11/02 14:32": [("md", None, 11, 2)],
            "25일 자동이체": [("d", None, None, 25)],
            "매월 25일": [("d", None, None, 25)],
            "11-02 14:32": [("md", None, 11, 2)],
        }
        for s, want in cases.items():
            with self.subTest(s=s):
                self.assertEqual(self.kinds(s), want)

    def test_not_dates(self):
        for s in ("7일 이내 환불", "3일 전", "14일간", "2026.13.45", "2026-02-30", "금액 1,234,567원"):
            with self.subTest(s=s):
                self.assertEqual(self.kinds(s), [])

    def test_resolve_future_rollover(self):
        tok = scan_dates("1월 10일까지")[0]
        self.assertEqual(resolve_date(tok, D(2026, 12, 28), "future"), D(2027, 1, 10))
        tok = scan_dates("11월 25일까지")[0]
        self.assertEqual(resolve_date(tok, D(2026, 11, 2), "future"), D(2026, 11, 25))
        # 막 지난 기한(60일 이내)은 올해 그대로
        tok = scan_dates("10월 25일까지")[0]
        self.assertEqual(resolve_date(tok, D(2026, 11, 2), "future"), D(2026, 10, 25))

    def test_resolve_past_for_payments(self):
        tok = scan_dates("12/05 09:12")[0]
        self.assertEqual(resolve_date(tok, D(2026, 11, 2), "past"), D(2025, 12, 5))
        self.assertEqual(resolve_date(tok, D(2026, 12, 6), "past"), D(2026, 12, 5))

    def test_resolve_day_only(self):
        tok = scan_dates("25일 자동이체")[0]
        self.assertEqual(resolve_date(tok, D(2026, 11, 2), "future", tol_days=3), D(2026, 11, 25))
        self.assertEqual(resolve_date(tok, D(2026, 11, 29), "future", tol_days=3), D(2026, 12, 25))
        self.assertEqual(resolve_date(tok, D(2026, 12, 29), "future", tol_days=3), D(2027, 1, 25))
        tok31 = scan_dates("31일")[0]
        self.assertEqual(resolve_date(tok31, D(2026, 2, 3), "future", tol_days=3), D(2026, 2, 28))

    def test_datetime(self):
        ref = D(2026, 11, 2)
        self.assertEqual(find_datetime("삼성 11/02 14:32 가게", ref), (dt.datetime(2026, 11, 2, 14, 32), True))
        self.assertEqual(find_datetime("2026-11-02 08:55:10", ref), (dt.datetime(2026, 11, 2, 8, 55), True))
        self.assertEqual(find_datetime("11/02 오후 3:20", ref), (dt.datetime(2026, 11, 2, 15, 20), True))
        self.assertEqual(find_datetime("11/02 오전 12:05", ref), (dt.datetime(2026, 11, 2, 0, 5), True))
        self.assertEqual(find_datetime("14:32 11/02", ref), (dt.datetime(2026, 11, 2, 14, 32), True))
        self.assertEqual(find_datetime("2026.11.01 구매", ref), (dt.datetime(2026, 11, 1, 0, 0), False))
        self.assertEqual(find_datetime("날짜 없음", ref), (None, False))

    def test_billing_month(self):
        ref = D(2026, 11, 2)
        cases = {
            "2026년 10월분 전기요금": "2026-10",
            "10월 전기요금 청구서": "2026-10",
            "청구년월 : 2026.09": "2026-09",
            "청구월: 202608": "2026-08",
            "11월분 건강보험료": "2026-11",
            "2026.10월분": "2026-10",
        }
        for s, want in cases.items():
            with self.subTest(s=s):
                self.assertEqual(find_billing_month(s, ref), want)
        self.assertEqual(find_billing_month("12월분 관리비", D(2026, 1, 5)), "2025-12")  # 연초 고지서의 12월분
        self.assertIsNone(find_billing_month("그냥 글", ref))

    def test_customer_no(self):
        self.assertEqual(find_customer_no("고객번호: 0123-4567-89"), "0123-4567-89")
        self.assertEqual(find_customer_no("납부자번호 1234567890"), "1234567890")
        self.assertEqual(find_customer_no("번호 없음"), "")


if __name__ == "__main__":
    unittest.main()
