import unittest
from datetime import datetime, timedelta

from steam_buddy import core
from steam_buddy.cli import parse_duration
from steam_buddy.steam import korean_support

NOW = datetime(2026, 11, 1, 12, 0, 0)


def game(days_ago=0.0, minutes=0, **kw):
    g = {"name": "G", "purchased_at": (NOW - timedelta(days=days_ago)).isoformat(), "minutes": minutes}
    g.update(kw)
    return g


class RefundTests(unittest.TestCase):
    def state(self, **kw):
        return core.refund_status(game(**kw), NOW)["state"]

    def test_comfortable(self):
        self.assertEqual(self.state(days_ago=1, minutes=10), "여유")

    def test_urgent_by_days(self):
        # 마감까지 정확히 2일 남음 -> 급함, 2일 초과 남음 -> 여유
        self.assertEqual(self.state(days_ago=12, minutes=0), "급함")
        self.assertEqual(self.state(days_ago=11.9, minutes=0), "여유")

    def test_urgent_by_minutes(self):
        self.assertEqual(self.state(days_ago=1, minutes=90), "급함")   # 30분 남음
        self.assertEqual(self.state(days_ago=1, minutes=89), "여유")   # 31분 남음

    def test_expired_by_days_boundary(self):
        self.assertEqual(self.state(days_ago=14, minutes=0), "기한지남")  # 정확히 14일 = 마감 시각
        self.assertEqual(self.state(days_ago=13.99, minutes=0), "급함")

    def test_expired_by_minutes_boundary(self):
        self.assertEqual(self.state(days_ago=1, minutes=120), "기한지남")  # 2시간 "미만"만 가능
        self.assertEqual(self.state(days_ago=1, minutes=119), "급함")

    def test_kept_overrides(self):
        self.assertEqual(self.state(days_ago=1, minutes=10, kept=True), "보유확정")
        self.assertEqual(self.state(days_ago=30, minutes=500, kept=True), "보유확정")

    def test_details(self):
        st = core.refund_status(game(days_ago=4, minutes=45), NOW)
        self.assertEqual(st["deadline"], NOW + timedelta(days=10))
        self.assertEqual(st["minutes_left"], 75)
        self.assertTrue(st["refundable"])


class FormatTests(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(core.fmt_won(12345), "₩12,345")
        self.assertEqual(core.fmt_minutes(0), "0분")
        self.assertEqual(core.fmt_minutes(90), "1시간 30분")
        self.assertEqual(core.fmt_minutes(45), "45분")
        self.assertEqual(core.fmt_timedelta(timedelta(days=2, hours=5)), "2일 5시간")
        self.assertEqual(core.fmt_timedelta(timedelta(hours=3, minutes=20)), "3시간 20분")
        self.assertEqual(core.fmt_timedelta(timedelta(seconds=-5)), "지남")


class ParseDurationTests(unittest.TestCase):
    def test_formats(self):
        cases = {"1h30m": 90, "90m": 90, "1.5": 90, "1:30": 90, "2h": 120, "45분": 45,
                 "1시간30분": 90, "1H 30M": 90, "0.5": 30, "1.5h": 90, "10": 600}
        for s, want in cases.items():
            with self.subTest(s=s):
                self.assertEqual(parse_duration(s), want)

    def test_invalid(self):
        for s in ("", "abc", "h", "1x"):
            with self.subTest(s=s):
                with self.assertRaises(ValueError):
                    parse_duration(s)


class KoreanSupportTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(korean_support("English<strong>*</strong>, Korean, Japanese"), "자막")
        self.assertEqual(korean_support("English, Korean<strong>*</strong>, Japanese"), "자막+음성")
        self.assertEqual(korean_support("English, Japanese<strong>*</strong>"), "없음")
        self.assertEqual(korean_support(""), "없음")
        self.assertEqual(korean_support(None), "없음")


def ts(days_ago):
    return int((NOW - timedelta(days=days_ago)).timestamp())


class EarlyAccessTests(unittest.TestCase):
    def risk(self, details=None, reviews=None, upd=None):
        d = {"early_access": True, "release_date": "1 Jan, 2026"}
        d.update(details or {})
        return core.early_access_risk(d, reviews, upd, NOW)

    def test_not_early_access(self):
        self.assertIsNone(core.early_access_risk({"early_access": False}, None, None, NOW))

    def test_healthy(self):
        r = self.risk(reviews={"ratio": 0.9, "total": 1000}, upd={"date": ts(5)})
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["level"], "낮음")

    def test_update_age_tiers(self):
        rv = {"ratio": 0.9, "total": 1000}
        for days, want in ((20, 0), (31, 10), (91, 30), (181, 45)):
            with self.subTest(days=days):
                self.assertEqual(self.risk(reviews=rv, upd={"date": ts(days)})["score"], want)

    def test_no_news(self):
        r = self.risk(reviews={"ratio": 0.9, "total": 1000}, upd=None)
        self.assertEqual(r["score"], 35)
        self.assertEqual(r["level"], "보통")

    def test_worst_case_high(self):
        r = self.risk({"release_date": "1 Jan, 2020"}, {"ratio": 0.4, "total": 10}, {"date": ts(400)})
        self.assertEqual(r["score"], 45 + 25 + 20 + 10)
        self.assertEqual(r["level"], "높음")
        self.assertTrue(r["reasons"])

    def test_release_and_review_tiers(self):
        rv = {"ratio": 0.7, "total": 1000}
        r = self.risk({"release_date": "1 Jan, 2024"}, rv, {"date": ts(1)})  # 2.8년 -> 15, 리뷰 70% -> 10
        self.assertEqual(r["score"], 25)

    def test_score_capped(self):
        r = self.risk({"release_date": "1 Jan, 2015"}, {"ratio": 0.1, "total": 1}, None)
        self.assertEqual(r["score"], 35 + 25 + 20 + 10)
        self.assertLessEqual(r["score"], 100)


class ValueRowTests(unittest.TestCase):
    def test_per_hour(self):
        r = core.value_row({"name": "A", "price_paid": 30000, "minutes": 600})
        self.assertAlmostEqual(r["per_hour"], 3000)

    def test_unplayed(self):
        self.assertIsNone(core.value_row({"name": "A", "price_paid": 30000, "minutes": 3})["per_hour"])


if __name__ == "__main__":
    unittest.main()


class WilsonTests(unittest.TestCase):
    def test_zero_total(self):
        self.assertEqual(core.wilson_lower_bound(0, 0), 0.0)

    def test_known_value(self):
        self.assertAlmostEqual(core.wilson_lower_bound(950, 1000), 0.9344, places=3)

    def test_small_sample_penalized(self):
        self.assertLess(core.wilson_lower_bound(30, 30), core.wilson_lower_bound(950, 1000))

    def test_gem_score_prefers_big_sample(self):
        self.assertGreater(core.gem_score(950, 1000), core.gem_score(30, 30))

    def test_bundle_value(self):
        items = [{"price": 10000, "owned": True}, {"price": 20000}, {"price": 5000, "skipped": True}]
        r = core.bundle_value(items, 25000)
        self.assertEqual((r["real"], r["full_sum"], r["new_count"]), (20000, 30000, 1))
        self.assertIn("개별 구매가 더 싸요", r["verdict"])
        self.assertIn("이득", core.bundle_value([{"price": 30000}], 20000)["verdict"])
        self.assertIn("필요 없어요", core.bundle_value([{"price": 1, "owned": True}], 5)["verdict"])
