# -*- coding: utf-8 -*-
"""분류기/추출기: 고지서 종류별, 문자 형식별, 영수증, 광고, 안내문."""
import datetime as dt
import unittest

import samples as S
from paperbutler.categories import categorize
from paperbutler.extract import analyze

T = dt.date(2026, 11, 2)


def A(text, today=T, **kw):
    return analyze(text, today, **kw)


class TestBills(unittest.TestCase):
    def test_electric(self):
        e = A(S.ELECTRIC)
        self.assertEqual((e.doc_type, e.subtype, e.issuer), ("bill", "전기", "한국전력공사"))
        self.assertEqual(e.amount, 45230)                 # 납기후 금액(46,590)이 아님
        self.assertEqual(e.due_date, "2026-11-25")        # 납기후기한(12/10)이 아님
        self.assertEqual(e.billing_month, "2026-10")
        self.assertEqual(e.customer_no, "0123-4567-89")
        self.assertTrue(e.auto_debit)

    def test_gas(self):
        e = A(S.GAS)
        self.assertEqual((e.doc_type, e.subtype, e.issuer, e.amount, e.due_date),
                         ("bill", "가스", "서울도시가스", 38900, "2026-11-20"))
        self.assertFalse(e.auto_debit)
        self.assertEqual(e.customer_no, "7001234567")

    def test_water(self):
        e = A(S.WATER)
        self.assertEqual((e.doc_type, e.subtype, e.amount, e.due_date), ("bill", "수도", 21450, "2026-11-30"))
        self.assertIn("상수도사업소", e.issuer)

    def test_telecom(self):
        e = A(S.TELECOM)
        self.assertEqual((e.doc_type, e.subtype, e.issuer, e.amount, e.due_date),
                         ("bill", "통신", "KT", 58300, "2026-11-15"))
        self.assertTrue(e.auto_debit)
        self.assertEqual(e.auto_debit_day, 15)

    def test_management_fee_not_auto_when_only_promo(self):
        e = A(S.MGMT)
        self.assertEqual((e.doc_type, e.subtype, e.amount, e.due_date), ("bill", "관리비", 187450, "2026-11-10"))
        self.assertIn("관리사무소", e.issuer)
        self.assertFalse(e.auto_debit, "'자동이체 신청하시면 할인' 은 자동이체가 아님")

    def test_health_insurance(self):
        e = A(S.HEALTH)
        self.assertEqual((e.doc_type, e.subtype, e.issuer, e.amount, e.due_date, e.billing_month),
                         ("bill", "건강보험", "국민건강보험공단", 156780, "2026-11-10", "2026-11"))
        self.assertEqual(e.customer_no, "1234567890")

    def test_pension_auto_debit_day(self):
        e = A(S.PENSION)
        self.assertEqual((e.doc_type, e.subtype, e.amount, e.due_date), ("bill", "국민연금", 213300, "2026-11-10"))
        self.assertTrue(e.auto_debit)
        self.assertEqual(e.auto_debit_day, 10)

    def test_car_tax(self):
        e = A(S.CAR_TAX)
        self.assertEqual((e.doc_type, e.subtype, e.amount, e.due_date), ("bill", "자동차세", 154320, "2026-12-31"))
        self.assertEqual(e.issuer, "강남구청")

    def test_property_tax_range_end(self):
        e = A(S.PROPERTY_TAX, today=dt.date(2026, 9, 20))
        self.assertEqual((e.subtype, e.amount, e.due_date), ("재산세", 452100, "2026-09-30"))

    def test_resident_tax_month_day_until(self):
        e = A(S.RESIDENT_TAX, today=dt.date(2026, 8, 1))
        self.assertEqual((e.subtype, e.amount, e.due_date, e.issuer), ("주민세", 6000, "2026-08-31", "강동구청"))

    def test_fine(self):
        e = A(S.FINE)
        self.assertEqual((e.subtype, e.amount, e.due_date), ("과태료", 40000, "2026-11-20"))  # 의견진술 기한 아님

    def test_card_statement(self):
        e = A(S.CARD_BILL)
        self.assertEqual((e.doc_type, e.subtype, e.issuer, e.amount, e.due_date),
                         ("bill", "카드대금", "삼성카드", 1234560, "2026-11-25"))
        self.assertTrue(e.auto_debit)

    def test_auto_debit_day_only_becomes_due(self):
        e = A("[한국전력] 10월분 전기요금 12,340원 청구. 25일 자동이체 예정입니다.")
        self.assertEqual(e.due_date, "2026-11-25")
        self.assertTrue(e.auto_debit)
        self.assertEqual(e.auto_debit_day, 25)

    def test_due_formats(self):
        base = "[한국전력] 10월분 전기요금 청구서 청구금액 12,340원 "
        cases = {
            "납부기한 2026.11.25": "2026-11-25",
            "납부기한 2026-11-25": "2026-11-25",
            "11월 25일까지 납부": "2026-11-25",
            "납부기한 : 11/25": "2026-11-25",
            "납부기한 11.25": "2026-11-25",
            "납부 마감일 2026년 11월 25일": "2026-11-25",
            "2026.11.25.(수)까지 납부하세요": "2026-11-25",
            "납기내 2026.11.25 납기후 2026.12.10": "2026-11-25",
        }
        for tail, want in cases.items():
            with self.subTest(tail=tail):
                self.assertEqual(A(base + tail).due_date, want)

    def test_due_year_rollover(self):
        e = A("[한국전력] 12월분 전기요금 12,340원 납부기한 1월 10일까지", today=dt.date(2026, 12, 28))
        self.assertEqual(e.due_date, "2027-01-10")

    def test_late_fee_date_not_used(self):
        e = A("[한국전력] 전기요금 청구서 청구금액 10,000원 납기후 12월 10일 연체료 부과")
        self.assertIsNone(e.due_date)

    def test_generic_bill_when_unknown_issuer(self):
        e = A("○○물산 이용요금 청구서\n납부기한 2026.11.20\n청구금액 33,000원")
        self.assertEqual((e.doc_type, e.amount, e.due_date), ("bill", 33000, "2026-11-20"))


class TestCardSMS(unittest.TestCase):
    def check(self, text, issuer, merchant, amount, ev, inst=0, cancel=False, today=T):
        e = A(text, today)
        self.assertEqual(e.doc_type, "card_sms", text)
        self.assertEqual(e.issuer, issuer)
        self.assertEqual(e.merchant, merchant)
        self.assertEqual(e.amount, amount)
        self.assertEqual(e.event_dt, ev)
        self.assertEqual(e.installment, inst)
        self.assertEqual(e.is_cancel, cancel)
        return e

    def test_samsung_multiline(self):
        e = self.check(S.SMS_SAMSUNG, "삼성카드", "스타벅스 강남점", 12340, "2026-11-02 14:32")
        self.assertEqual(e.card, "삼성카드(1234)")
        self.assertTrue(e.time_known)

    def test_shinhan_single_line(self):
        self.check(S.SMS_SHINHAN, "신한카드", "GS25역삼점", 12340, "2026-11-02 14:32")   # 누적금액은 무시

    def test_kb_installment(self):
        self.check(S.SMS_KB, "KB국민카드", "쿠팡(주)", 120000, "2026-11-02 14:32", inst=3)

    def test_hyundai_cancel(self):
        self.check(S.SMS_HYUNDAI_CANCEL, "현대카드", "스타벅스 강남점", 12340, "2026-11-02 14:35", cancel=True)

    def test_lotte_cancel_date_first(self):
        self.check(S.SMS_LOTTE_CANCEL, "롯데카드", "커피빈 서초점", 8900, "2026-12-05 09:12",
                   cancel=True, today=dt.date(2026, 12, 6))

    def test_woori_full_date(self):
        self.check(S.SMS_WOORI, "우리카드", "교보문고 광화문점", 38000, "2026-11-02 18:47")

    def test_nh_lines(self):
        self.check(S.SMS_NH, "NH농협카드", "맛나김밥", 15000, "2026-11-02 12:30")

    def test_check_card_with_balance(self):
        self.check(S.SMS_KB_CHECK, "국민은행", "GS칼텍스", 7500, "2026-11-02 08:12")   # 잔액 523,000원 무시

    def test_pay_app_without_datetime(self):
        e = A(S.SMS_PAY)
        self.assertEqual((e.doc_type, e.merchant, e.amount, e.issuer), ("card_sms", "스타벅스", 4500, "카카오페이"))
        self.assertIsNone(e.event_dt)
        self.assertIn("event_dt", e.missing_fields())

    def test_toss_bank(self):
        self.check(S.SMS_TOSS, "토스뱅크", "쿠팡", 29900, "2026-11-02 20:15")

    def test_hana_taxi(self):
        e = self.check(S.SMS_TAXI, "하나카드", "카카오T택시", 9800, "2026-11-03 23:10")
        self.assertEqual(e.card, "하나카드(4321)")

    def test_overseas_has_no_krw_amount(self):
        e = A(S.SMS_OVERSEAS)
        self.assertEqual(e.doc_type, "card_sms")
        self.assertIsNone(e.amount)
        self.assertTrue(any("외화" in n for n in e.notes))

    def test_year_end_payment_date_in_past(self):
        e = A("[신한카드] 승인 5,000원 일시불 12/31 23:50 편의점", today=dt.date(2027, 1, 2))
        self.assertEqual(e.event_dt, "2026-12-31 23:50")

    def test_categories(self):
        cats = {S.SMS_SAMSUNG: "식비", S.SMS_KB: "쇼핑", S.SMS_KB_CHECK: "교통", S.SMS_NETFLIX: "구독",
                S.SMS_TAXI: "교통", S.SMS_WOORI: "문화", S.SMS_NH: "식비"}
        for text, want in cats.items():
            with self.subTest(want=want):
                self.assertEqual(categorize(A(text).merchant), want)
        self.assertEqual(categorize("쿠팡이츠"), "식비")
        self.assertEqual(categorize("쿠팡와우멤버십"), "구독")
        self.assertEqual(categorize("한국전력"), "공과금")
        self.assertEqual(categorize("미지의가게"), "기타")
        self.assertEqual(categorize(""), "기타")
        self.assertEqual(categorize("CUBE 카페"), "식비")  # 'CU' 가 CUBE 에 걸리지 않고 카페로 분류
        self.assertEqual(categorize("미지의가게", {"취미": ["미지의"]}), "취미")


class TestReceiptsAdsNotices(unittest.TestCase):
    def test_cafe_receipt_not_card_sms(self):
        e = A(S.RECEIPT_CAFE)
        self.assertEqual((e.doc_type, e.merchant, e.amount, e.event_dt),
                         ("receipt", "스타벅스 강남역점", 4500, "2026-11-02 08:55"))
        self.assertFalse(e.online)

    def test_online_receipt(self):
        e = A(S.RECEIPT_ONLINE)
        self.assertEqual((e.doc_type, e.merchant, e.amount, e.event_dt), ("receipt", "쿠팡", 29900, "2026-11-02 20:15"))
        self.assertTrue(e.online)

    def test_pharmacy(self):
        e = A(S.RECEIPT_PHARMACY)
        self.assertEqual((e.doc_type, e.merchant, e.amount), ("receipt", "행복약국", 8500))

    def test_policy_days_parsed(self):
        e = A(S.RECEIPT_RETAIL_POLICY)
        self.assertEqual((e.merchant, e.amount, e.refund_days), ("모던가구 서초점", 189000, 14))
        self.assertEqual(e.event_dt, "2026-11-01 15:20")

    def test_no_refund_text(self):
        self.assertEqual(A(S.RECEIPT_NO_REFUND).refund_days, 0)

    def test_receipt_amount_label_priority(self):
        e = A("영수증\n상호: 가게\n공급가액 10,000원\n부가세 1,000원\n합계 11,000원\n받은금액 20,000원\n2026-11-01")
        self.assertEqual(e.amount, 11000)

    def test_ads(self):
        for name in ("AD_SMS", "AD_FLYER", "AD_LOAN"):
            with self.subTest(name=name):
                self.assertEqual(A(getattr(S, name)).doc_type, "ad")

    def test_ad_not_mistaken_for_bill_or_card(self):
        e = A("(광고) 삼성카드 신규 발급 이벤트! 승인 즉시 10,000원 캐시백 쿠폰 지급 무료수신거부 080-111-2222")
        self.assertEqual(e.doc_type, "ad")

    def test_parcel(self):
        e = A(S.PARCEL_DONE)
        self.assertEqual((e.doc_type, e.subtype, e.issuer), ("parcel", "배송완료", "CJ대한통운"))
        e = A(S.PARCEL_MISSED)
        self.assertEqual((e.doc_type, e.subtype), ("parcel", "수령필요"))

    def test_notice_insurance(self):
        e = A(S.NOTICE_INSURANCE)
        self.assertEqual((e.doc_type, e.subtype, e.issuer, e.due_date), ("notice", "보험", "삼성화재", "2026-12-10"))

    def test_notice_bank(self):
        e = A(S.NOTICE_BANK)
        self.assertEqual((e.doc_type, e.subtype, e.issuer), ("notice", "은행", "신한은행"))
        self.assertIsNone(e.due_date)

    def test_notice_dunning(self):
        e = A(S.NOTICE_DUNNING)
        self.assertEqual((e.doc_type, e.subtype, e.due_date), ("notice", "관공서", "2026-11-30"))
        self.assertIn("legal", e.flags)

    def test_unknown(self):
        e = A(S.UNKNOWN)
        self.assertEqual(e.doc_type, "unknown")
        self.assertLess(e.confidence, 0.5)

    def test_force_type(self):
        e = A("스타벅스 4,500원", force_type="receipt")
        self.assertEqual(e.doc_type, "receipt")
        self.assertEqual(e.amount, 4500)

    def test_empty_text(self):
        e = A("")
        self.assertEqual(e.doc_type, "unknown")


class TestTrickyInputs(unittest.TestCase):
    def test_management_fee_total_label(self):
        e = A("OO아파트 관리사무소\n2026년 10월 관리비 고지서\n당월 부과액 150,000원\n미납액 0원\n납부할 총액 150,000원\n납부기한 2026.11.10")
        self.assertEqual((e.subtype, e.amount, e.due_date), ("관리비", 150000, "2026-11-10"))

    def test_autodebit_day_label_and_late_amount_ignored(self):
        e = A("KT 11월 요금 청구서\n총 청구금액 62,500원\n자동이체일 25일\n납기후 금액 63,100원")
        self.assertEqual((e.subtype, e.amount, e.due_date, e.auto_debit_day), ("통신", 62500, "2026-11-25", 25))

    def test_amount_without_won(self):
        e = A("[한국전력] 10월분 전기요금\n청구금액 : 12340\n납부기한 11월 25일")
        self.assertEqual((e.amount, e.due_date), (12340, "2026-11-25"))

    def test_space_before_won_and_slash_date(self):
        e = A("[국민건강보험] 12월분 건강보험료\n납부 금액 1,234,560 원\n납부기한: 12/10")
        self.assertEqual((e.amount, e.due_date), (1234560, "2026-12-10"))

    def test_korean_unit_amount_in_bill(self):
        e = A("[서울도시가스] 도시가스 요금 청구서 청구금액 3만 8천900원 납부기한 2026.11.20")
        self.assertEqual(e.amount, 38900)

    def test_am_pm_time_in_sms(self):
        e = A("[신한카드] 11/02 오후 3:20 승인 8,000원 일시불 교촌치킨")
        self.assertEqual((e.event_dt, e.merchant), ("2026-11-02 15:20", "교촌치킨"))

    def test_ad_about_car_tax_is_ad(self):
        e = A("자동차세 연납 신청 안내 (광고) 할인 혜택 최대 4.57% 연납 신청하세요 무료수신거부")
        self.assertEqual(e.doc_type, "ad")

    def test_pathological_inputs_are_fast(self):
        import time
        t0 = time.time()
        for s in ("1" * 30000, "12,345," * 5000, "1,000" * 3000 + "원", "가나다 11/02 14:32 " * 3000, "\n" * 20000):
            A(s)
        self.assertLess(time.time() - t0, 5.0)

    def test_huge_numbers_do_not_crash(self):
        e = A("[한국전력] 전기요금 청구금액 " + "9" * 5000 + "원 납부기한 2026.11.25")
        self.assertEqual(e.doc_type, "bill")


if __name__ == "__main__":
    unittest.main()
