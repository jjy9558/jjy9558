# -*- coding: utf-8 -*-
import datetime as dt
import unittest

import samples as S
from paperbutler.advice import ACTION, DISCARD, KEEP, advise
from paperbutler.extract import analyze

T = dt.date(2026, 11, 2)


def adv(text, today=T, **kw):
    ext = analyze(text, today)
    return ext, advise(ext, today, **kw)


class TestAdvice(unittest.TestCase):
    def test_example_from_spec_autodebit(self):
        _, a = adv("[한국전력] 2026년 10월분 전기요금 청구서\n청구금액: 12,340원\n납부기한: 2026.11.25\n자동이체 되고 있습니다.")
        self.assertEqual(a.todo, "11/25까지 12,340원 납부 — 자동이체라 할 일 없음")
        self.assertEqual(a.verdict, DISCARD)
        self.assertTrue(a.reasons)

    def test_manual_payment_needs_action(self):
        e, a = adv(S.GAS)
        self.assertEqual(a.todo, "11/20까지 38,900원 납부 — 직접 납부 필요 (D-18)")
        self.assertEqual(a.verdict, ACTION)
        self.assertIn("한 줄", "한 줄")  # 요약은 한 줄
        self.assertNotIn("\n", a.summary)
        self.assertIn("서울도시가스 가스요금 38,900원", a.summary)

    def test_deadline_urgency_wording(self):
        base = "[서울도시가스] 도시가스 요금 청구서 청구금액 10,000원 납부기한 %s"
        _, a = adv(base % "2026.11.04")
        self.assertIn("D-2, 서두르세요", a.todo)
        _, a = adv(base % "2026.11.02")
        self.assertIn("오늘이 마지막 날", a.todo)
        _, a = adv(base % "2026.10.30")
        self.assertIn("기한이 3일 지남", a.todo)
        self.assertEqual(a.verdict, ACTION)

    def test_autodebit_but_overdue(self):
        _, a = adv("[한국전력] 전기요금 청구서 청구금액 10,000원 납부기한 2026.10.30 자동이체")
        self.assertIn("자동이체지만 기한이 3일 지남", a.todo)

    def test_tax_unpaid_action_with_retention(self):
        e, a = adv(S.CAR_TAX)
        self.assertEqual(a.verdict, ACTION)
        self.assertEqual(a.retention_years, 5)
        self.assertTrue(any("5년" in r for r in a.reasons))

    def test_tax_done_keep_5_years(self):
        e = analyze(S.CAR_TAX, T)
        a = advise(e, T, done=True)
        self.assertEqual((a.verdict, a.retention_years), (KEEP, 5))
        self.assertEqual(a.verdict_display, "보관 5년")

    def test_insurance_autodebit_keep(self):
        e, a = adv(S.PENSION)
        self.assertEqual((a.verdict, a.retention_years), (KEEP, 5))
        self.assertIn("자동이체라 할 일 없음", a.todo)

    def test_health_insurance_unpaid(self):
        _, a = adv(S.HEALTH)
        self.assertEqual(a.verdict, ACTION)
        self.assertEqual(a.retention_years, 5)

    def test_fine_action_and_warns_surcharge(self):
        _, a = adv(S.FINE)
        self.assertEqual(a.verdict, ACTION)
        self.assertTrue(any("가산금" in r for r in a.reasons))

    def test_utility_autodebit_can_discard(self):
        _, a = adv(S.TELECOM)
        self.assertEqual(a.verdict, DISCARD)

    def test_ads_discard(self):
        for name in ("AD_SMS", "AD_FLYER", "AD_LOAN"):
            with self.subTest(name=name):
                _, a = adv(getattr(S, name))
                self.assertEqual(a.verdict, DISCARD)
                self.assertTrue(any("광고" in r for r in a.reasons))

    def test_ad_with_important_words_kept(self):
        e, a = adv("[삼성생명] 납부 이벤트 안내 쿠폰 증정")
        self.assertEqual(e.doc_type, "ad")
        self.assertEqual(a.verdict, KEEP)

    def test_unknown_is_kept_never_discarded(self):
        _, a = adv(S.UNKNOWN)
        self.assertEqual(a.verdict, KEEP)

    def test_card_sms(self):
        _, a = adv(S.SMS_SAMSUNG, category="식비")
        self.assertEqual(a.verdict, DISCARD)
        self.assertIn("스타벅스 강남점 12,340원 일시불 승인", a.summary)
        self.assertIn("11/2 14:32", a.summary)
        _, a = adv(S.SMS_KB)
        self.assertIn("3개월 할부", a.summary)
        _, a = adv(S.SMS_HYUNDAI_CANCEL)
        self.assertIn("취소", a.summary)
        self.assertIn("환불", a.todo)
        _, a = adv(S.SMS_SAMSUNG, pay_status="duplicate")
        self.assertIn("중복", a.todo)

    def test_receipts(self):
        e = analyze(S.RECEIPT_CAFE, T)
        self.assertEqual(advise(e, T).verdict, DISCARD)
        e = analyze(S.RECEIPT_WARRANTY, T)
        a = advise(e, T)
        self.assertEqual(a.verdict, KEEP)
        e = analyze(S.RECEIPT_ONLINE, T)
        a = advise(e, T, refund_deadline=dt.date(2026, 11, 9), refund_days=7)
        self.assertEqual(a.verdict, KEEP)
        self.assertIn("11/9까지 환불/반품 가능", a.todo)
        a = advise(e, T, refund_deadline=dt.date(2026, 10, 1), refund_days=7)
        self.assertEqual(a.verdict, DISCARD)       # 기한 지나면 폐기 가능
        e = analyze("영수증\n상호: 가전몰\n2026-11-01\n합계 800,000원\n", T)
        a = advise(e, T)
        self.assertEqual((a.verdict, a.retention_years), (KEEP, 1))
        e = analyze("영수증\n상호: 컨설팅\n사업자등록번호 123-45-67890\n지출증빙용 세금계산서 발행\n합계 55,000원\n2026-11-01", T)
        a = advise(e, T)
        self.assertEqual((a.verdict, a.retention_years), (KEEP, 5))

    def test_notices(self):
        _, a = adv(S.NOTICE_INSURANCE)
        self.assertEqual(a.verdict, ACTION)
        _, a = adv(S.NOTICE_BANK)
        self.assertEqual((a.verdict, a.retention_years), (KEEP, 1))
        _, a = adv(S.NOTICE_DUNNING)
        self.assertEqual((a.verdict, a.retention_years), (ACTION, 5))
        self.assertTrue(any("법적" in r for r in a.reasons))

    def test_parcel(self):
        _, a = adv(S.PARCEL_DONE)
        self.assertEqual(a.verdict, DISCARD)
        _, a = adv(S.PARCEL_MISSED)
        self.assertEqual(a.verdict, ACTION)

    def test_low_confidence_bill_is_kept(self):
        e = analyze(S.ELECTRIC, T)
        e.confidence = 0.3
        self.assertEqual(advise(e, T).verdict, KEEP)

    def test_outputs_masked(self):
        e = analyze("[삼성카드] 승인 5,000원 일시불 11/02 10:00 010-1234-5678", T)
        a = advise(e, T)
        self.assertNotIn("1234-5678", a.summary)

    def test_other_year_shows_year(self):
        _, a = adv("[한국전력] 12월분 전기요금 청구서 청구금액 10,000원 납부기한 1월 10일까지", dt.date(2026, 12, 28))
        self.assertIn("2027/1/10까지", a.todo)


if __name__ == "__main__":
    unittest.main()
