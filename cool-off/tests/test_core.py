import datetime as dt
import unittest

from cool_off import core
from cool_off.core import Ledger, LedgerError
from tests.fakes import at


class CoolingOffTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.t0 = at(2026, 11, 2, 10)

    def test_default_cooling_period_is_three_days(self):
        wish = self.ledger.add_wish("에어팟 프로", 359000, self.t0)
        self.assertEqual(wish["cool_until"], "2026-11-05T10:00")
        self.assertEqual(wish["status"], core.STATUS_COOLING)

    def test_due_only_at_or_after_cool_until(self):
        self.ledger.add_wish("에어팟 프로", 359000, self.t0)
        self.assertEqual(self.ledger.due_wishes(at(2026, 11, 5, 9, 59)), [])
        self.assertEqual(len(self.ledger.due_wishes(at(2026, 11, 5, 10, 0))), 1)

    def test_yes_moves_to_approved_and_is_not_due_again(self):
        wish = self.ledger.add_wish("에어팟 프로", 359000, self.t0)
        status = self.ledger.decide_wish(wish, "y", at(2026, 11, 5))
        self.assertEqual(status, core.STATUS_APPROVED)
        self.assertEqual(self.ledger.due_wishes(at(2026, 12, 1)), [])
        self.assertEqual(self.ledger.approved_wishes(), [wish])

    def test_no_moves_to_skipped_and_adds_to_saved_total(self):
        first = self.ledger.add_wish("키보드", 120000, self.t0)
        second = self.ledger.add_wish("모니터", 250000, self.t0)
        self.ledger.decide_wish(first, "n", at(2026, 11, 5))
        self.ledger.decide_wish(second, "n", at(2026, 11, 5))
        self.assertEqual(self.ledger.saved_total(), (2, 370000))
        self.assertEqual(self.ledger.due_wishes(at(2026, 12, 1)), [])

    def test_later_postpones_one_day_and_stays_cooling(self):
        wish = self.ledger.add_wish("에어팟 프로", 359000, self.t0)
        now = at(2026, 11, 5, 10)
        status = self.ledger.decide_wish(wish, "later", now)
        self.assertEqual(status, core.STATUS_COOLING)
        self.assertEqual(wish["postponed"], 1)
        self.assertEqual(wish["cool_until"], "2026-11-06T10:00")
        self.assertEqual(self.ledger.due_wishes(at(2026, 11, 5, 23)), [])
        self.assertEqual(len(self.ledger.due_wishes(at(2026, 11, 6, 10))), 1)

    def test_negative_days_and_bad_price_rejected(self):
        with self.assertRaises(LedgerError):
            self.ledger.add_wish("x", 1000, self.t0, days=-1)
        with self.assertRaises(LedgerError):
            self.ledger.add_wish("x", 0, self.t0)
        with self.assertRaises(LedgerError):
            self.ledger.add_wish("   ", 1000, self.t0)

    def test_due_items_sorted_by_end_time(self):
        late = self.ledger.add_wish("나중에 담은 것", 1000, at(2026, 11, 3))
        early = self.ledger.add_wish("먼저 담은 것", 1000, at(2026, 11, 1))
        due = self.ledger.due_wishes(at(2026, 11, 10))
        self.assertEqual([w["name"] for w in due], ["먼저 담은 것", "나중에 담은 것"])
        self.assertIs(due[0], early)
        self.assertIs(due[1], late)


class PurchaseAndFollowupTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()

    def test_bought_links_approved_wish(self):
        wish = self.ledger.add_wish("에어팟 프로", 359000, at(2026, 11, 2))
        self.ledger.decide_wish(wish, "y", at(2026, 11, 5))
        purchase, linked = self.ledger.record_purchase("에어팟 프로", 329000, dt.date(2026, 11, 6))
        self.assertIs(linked, wish)
        self.assertEqual(wish["status"], core.STATUS_BOUGHT)
        self.assertEqual(wish["purchase_id"], purchase["id"])
        self.assertFalse(purchase["during_cooling"])
        self.assertEqual(purchase["price"], 329000)

    def test_buying_during_cooling_is_flagged_as_impulse(self):
        self.ledger.add_wish("에어팟 프로", 359000, at(2026, 11, 2))
        purchase, _ = self.ledger.record_purchase("에어팟 프로", None, dt.date(2026, 11, 2))
        self.assertTrue(purchase["during_cooling"])
        self.assertEqual(purchase["price"], 359000)

    def test_zero_day_wish_is_not_impulse_once_clock_passed(self):
        self.ledger.add_wish("키보드", 120000, at(2026, 11, 2, 10), days=0)
        purchase, _ = self.ledger.record_purchase("키보드", None, dt.date(2026, 11, 2), at(2026, 11, 2, 10, 1))
        self.assertFalse(purchase["during_cooling"])

    def test_name_match_is_case_insensitive(self):
        self.ledger.add_wish("AirPods", 300000, at(2026, 11, 2))
        _, linked = self.ledger.record_purchase("airpods", None, dt.date(2026, 11, 2))
        self.assertIsNotNone(linked)

    def test_unknown_item_without_price_rejected(self):
        with self.assertRaises(LedgerError):
            self.ledger.record_purchase("모르는 것", None, dt.date(2026, 11, 2))

    def test_followup_due_exactly_thirty_days_later(self):
        self.ledger.record_purchase("에어팟 프로", 329000, dt.date(2026, 10, 1))
        self.assertEqual(self.ledger.due_followups(dt.date(2026, 10, 30)), [])
        due = self.ledger.due_followups(dt.date(2026, 10, 31))
        self.assertEqual(len(due), 1)

    def test_answered_followup_is_not_due_again(self):
        purchase, _ = self.ledger.record_purchase("에어팟 프로", 329000, dt.date(2026, 10, 1))
        self.ledger.record_followup(purchase, 4, "주1-2", at(2026, 10, 31))
        self.assertEqual(self.ledger.due_followups(dt.date(2027, 1, 1)), [])
        self.assertEqual(purchase["satisfaction"], 4)
        self.assertEqual(purchase["usage"], core.USAGE_WEEKLY)

    def test_followup_validation(self):
        purchase, _ = self.ledger.record_purchase("가방", 90000, dt.date(2026, 10, 1))
        with self.assertRaises(LedgerError):
            self.ledger.record_followup(purchase, 6, "매일", at(2026, 10, 31))
        with self.assertRaises(LedgerError):
            self.ledger.record_followup(purchase, 3, "가끔", at(2026, 10, 31))
        self.assertIsNone(purchase["satisfaction"])

    def test_usage_aliases(self):
        self.assertEqual(core.normalize_usage("매일"), core.USAGE_DAILY)
        self.assertEqual(core.normalize_usage("주 1-2"), core.USAGE_WEEKLY)
        self.assertEqual(core.normalize_usage("주1~2회"), core.USAGE_WEEKLY)
        self.assertEqual(core.normalize_usage("거의 안 씀"), core.USAGE_RARE)
        self.assertEqual(core.normalize_usage("3"), core.USAGE_RARE)

    def test_parse_day(self):
        today = dt.date(2026, 11, 2)
        self.assertEqual(core.parse_day("오늘", today), today)
        self.assertEqual(core.parse_day("어제", today), dt.date(2026, 11, 1))
        self.assertEqual(core.parse_day("2026-10-20", today), dt.date(2026, 10, 20))
        with self.assertRaises(LedgerError):
            core.parse_day("2026-11-03", today)
        with self.assertRaises(LedgerError):
            core.parse_day("엊그제", today)

    def test_parse_money(self):
        self.assertEqual(core.parse_money("359,000"), 359000)
        self.assertEqual(core.parse_money("359000원"), 359000)
        with self.assertRaises(LedgerError):
            core.parse_money("0")
        with self.assertRaises(LedgerError):
            core.parse_money("비싸요")


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.today = dt.date(2026, 11, 2)

    def test_totals_match_the_example_line(self):
        self.ledger.add_subscription("넷플릭스", 13500, "monthly", 15, None, self.today)
        self.ledger.add_subscription("정기 배송", 34300, "monthly", 1, None, self.today)
        totals = self.ledger.subscription_totals()
        self.assertEqual(totals["monthly"], 47800)
        self.assertEqual(totals["yearly"], 573600)
        self.assertEqual(totals["chickens"], 28)

    def test_yearly_subscription_counts_once_per_year(self):
        self.ledger.add_subscription("도메인", 120000, "yearly", 3, 5, self.today)
        self.ledger.add_subscription("음악", 10000, "monthly", 2, None, self.today)
        totals = self.ledger.subscription_totals()
        self.assertEqual(totals["yearly"], 240000)
        self.assertEqual(totals["monthly"], 20000)

    def test_monthly_rounding_is_half_up(self):
        self.ledger.add_subscription("a", 100, "yearly", 1, 1, self.today)
        self.assertEqual(self.ledger.subscription_totals()["monthly"], 8)  # 100/12 = 8.33
        self.ledger.add_subscription("b", 5, "yearly", 1, 1, self.today)
        self.assertEqual(self.ledger.subscription_totals()["monthly"], 9)  # 105/12 = 8.75

    def test_chicken_is_floor_of_yearly_total(self):
        self.ledger.add_subscription("a", 19999, "yearly", 1, 1, self.today)
        self.assertEqual(self.ledger.subscription_totals()["chickens"], 0)
        self.ledger.add_subscription("b", 1, "yearly", 1, 1, self.today)
        self.assertEqual(self.ledger.subscription_totals()["chickens"], 1)

    def test_month_end_renewal_clamps_to_last_day(self):
        sub = {"cycle": "monthly", "day": 31, "month": None}
        self.assertEqual(core.next_renewal(sub, dt.date(2027, 1, 31)), dt.date(2027, 1, 31))
        self.assertEqual(core.next_renewal(sub, dt.date(2027, 2, 1)), dt.date(2027, 2, 28))
        self.assertEqual(core.next_renewal(sub, dt.date(2027, 2, 28)), dt.date(2027, 2, 28))
        self.assertEqual(core.next_renewal(sub, dt.date(2027, 3, 1)), dt.date(2027, 3, 31))
        self.assertEqual(core.next_renewal(sub, dt.date(2027, 4, 30)), dt.date(2027, 4, 30))
        self.assertEqual(core.next_renewal(sub, dt.date(2027, 5, 1)), dt.date(2027, 5, 31))
        self.assertEqual(core.next_renewal(sub, dt.date(2027, 12, 31)), dt.date(2027, 12, 31))

    def test_leap_year_month_end(self):
        sub = {"cycle": "monthly", "day": 30, "month": None}
        self.assertEqual(core.next_renewal(sub, dt.date(2028, 2, 10)), dt.date(2028, 2, 29))
        sub31 = {"cycle": "monthly", "day": 31, "month": None}
        self.assertEqual(core.next_renewal(sub31, dt.date(2028, 2, 1)), dt.date(2028, 2, 29))

    def test_yearly_feb_29_renewal(self):
        sub = {"cycle": "yearly", "day": 29, "month": 2}
        self.assertEqual(core.next_renewal(sub, dt.date(2027, 3, 1)), dt.date(2028, 2, 29))
        self.assertEqual(core.next_renewal(sub, dt.date(2028, 3, 1)), dt.date(2029, 2, 28))
        self.assertEqual(core.next_renewal(sub, dt.date(2028, 2, 29)), dt.date(2028, 2, 29))

    def test_renewal_reminder_window_is_three_days(self):
        self.ledger.add_subscription("넷플릭스", 13500, "monthly", 15, None, dt.date(2026, 11, 1))
        def due_on(day):
            return [(s["name"], left) for s, _r, left in self.ledger.due_renewals(day)]
        self.assertEqual(due_on(dt.date(2026, 11, 11)), [])            # 4일 전
        self.assertEqual(due_on(dt.date(2026, 11, 12)), [("넷플릭스", 3)])
        self.assertEqual(due_on(dt.date(2026, 11, 14)), [("넷플릭스", 1)])
        self.assertEqual(due_on(dt.date(2026, 11, 15)), [("넷플릭스", 0)])  # 당일
        self.assertEqual(due_on(dt.date(2026, 11, 16)), [])            # 지나면 다음 달 기준

    def test_reminder_for_month_end_day_31(self):
        self.ledger.add_subscription("클라우드", 9900, "monthly", 31, None, dt.date(2027, 1, 1))
        due = self.ledger.due_renewals(dt.date(2027, 2, 25))
        self.assertEqual([(r, left) for _s, r, left in due], [(dt.date(2027, 2, 28), 3)])

    def test_remove_by_id_or_name(self):
        sub = self.ledger.add_subscription("넷플릭스", 13500, "monthly", 15, None, self.today)
        self.ledger.add_subscription("왓챠", 8900, "monthly", 3, None, self.today)
        self.assertEqual(self.ledger.remove_subscription(str(sub["id"]))["name"], "넷플릭스")
        self.assertEqual(self.ledger.remove_subscription("왓챠")["name"], "왓챠")
        with self.assertRaises(LedgerError):
            self.ledger.remove_subscription("없음")

    def test_invalid_subscription_inputs(self):
        with self.assertRaises(LedgerError):
            self.ledger.add_subscription("x", 1000, "weekly", 1, None, self.today)
        with self.assertRaises(LedgerError):
            self.ledger.add_subscription("x", 1000, "monthly", 32, None, self.today)
        with self.assertRaises(LedgerError):
            self.ledger.add_subscription("x", 1000, "yearly", 1, 13, self.today)

    def test_default_day_is_today(self):
        sub = self.ledger.add_subscription("x", 1000, "monthly", None, None, self.today)
        self.assertEqual(sub["day"], 2)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.p1, _ = self.ledger.record_purchase("이어폰", 100000, dt.date(2026, 9, 5))
        self.ledger.record_followup(self.p1, 5, "매일", at(2026, 10, 5))
        self.p2, _ = self.ledger.record_purchase("키링", 30000, dt.date(2026, 9, 20))
        self.ledger.record_followup(self.p2, 2, "주1-2", at(2026, 10, 20))
        self.p3, _ = self.ledger.record_purchase("러닝화", 50000, dt.date(2026, 10, 3))
        self.ledger.record_followup(self.p3, 4, "거의 안 씀", at(2026, 11, 2))
        self.p4, _ = self.ledger.record_purchase("간식", 20000, dt.date(2026, 10, 10))  # 평가 전
        for amount, name in ((10000, "A"), (25000, "B")):
            wish = self.ledger.add_wish(name, amount, at(2026, 9, 1))
            self.ledger.decide_wish(wish, "n", at(2026, 9, 5))

    def test_saved_money(self):
        rep = self.ledger.report()
        self.assertEqual(rep["saved_count"], 2)
        self.assertEqual(rep["saved_total"], 35000)

    def test_average_satisfaction_only_counts_rated(self):
        rep = self.ledger.report()
        self.assertEqual(rep["rated_count"], 3)
        self.assertAlmostEqual(rep["avg_satisfaction"], (5 + 2 + 4) / 3)
        self.assertEqual(rep["purchase_count"], 4)
        self.assertEqual(rep["spent_total"], 200000)

    def test_regrets_low_satisfaction_or_rarely_used(self):
        names = [p["name"] for p in self.ledger.report()["regrets"]]
        self.assertEqual(names, ["키링", "러닝화"])

    def test_per_won_ranking_is_satisfaction_per_ten_thousand_won(self):
        self.assertAlmostEqual(core.value_per_man(self.p1), 0.5)
        self.assertAlmostEqual(core.value_per_man(self.p2), 2 * 10000 / 30000)
        self.assertAlmostEqual(core.value_per_man(self.p3), 0.8)
        ranking = [p["name"] for p in self.ledger.report()["ranking"]]
        self.assertEqual(ranking, ["러닝화", "키링", "이어폰"])

    def test_monthly_spend(self):
        self.assertEqual(self.ledger.report()["monthly"],
                         [("2026-09", 130000), ("2026-10", 70000)])

    def test_empty_report(self):
        rep = Ledger().report()
        self.assertIsNone(rep["avg_satisfaction"])
        self.assertEqual(rep["regrets"], [])
        self.assertEqual(rep["monthly"], [])
        self.assertEqual(rep["saved_total"], 0)


if __name__ == "__main__":
    unittest.main()
