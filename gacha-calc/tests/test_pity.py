import random
import statistics
import unittest

from gachacalc.banner import RULE_GUARANTEE, Banner, hit_probability
from gachacalc.currency import Package
from gachacalc.pity import build_pity_report, pity_distribution, verdict_line
from gachacalc.validation import ValidationError


def make_banner(**overrides) -> Banner:
    data = dict(
        name="테스트 배너",
        base_rate_pct=1.0,
        soft_pity_start=10,
        soft_pity_increase_pct=5.0,
        hard_pity=20,
        cost_per_pull=100,
    )
    data.update(overrides)
    return Banner(**data)


def _simulate(banner, pity_count, guaranteed, trials, seed):
    """DP 와 독립적으로 뽑기 한 번씩 흉내 낸다 (고정 시드)."""
    rng = random.Random(seed)
    base = banner.base_rate_pct / 100
    inc = banner.soft_pity_increase_pct / 100
    results = []
    for _ in range(trials):
        k, sure, pulls = pity_count, guaranteed, 0
        while True:
            pulls += 1
            j = k + 1
            if j >= banner.hard_pity:
                hit = True
            elif j < banner.soft_pity_start:
                hit = rng.random() < base
            else:
                hit = rng.random() < min(1.0, base + (j - banner.soft_pity_start + 1) * inc)
            if not hit:
                k = j
                continue
            if sure or banner.rule == RULE_GUARANTEE or rng.random() < 0.5:
                break
            sure, k = True, 0
        results.append(pulls)
    return results


class HitProbabilityTest(unittest.TestCase):
    def test_base_soft_and_hard_pity(self):
        b = make_banner()
        self.assertAlmostEqual(hit_probability(b, 1), 0.01)
        self.assertAlmostEqual(hit_probability(b, 9), 0.01)
        self.assertAlmostEqual(hit_probability(b, 10), 0.06)
        self.assertAlmostEqual(hit_probability(b, 11), 0.11)
        self.assertAlmostEqual(hit_probability(b, 20), 1.0)

    def test_probability_is_capped_at_one(self):
        b = make_banner(base_rate_pct=1, soft_pity_start=5, soft_pity_increase_pct=60, hard_pity=20)
        self.assertEqual(hit_probability(b, 6), 1.0)


class DistributionTest(unittest.TestCase):
    def test_pmf_sums_to_one(self):
        dist = pity_distribution(make_banner(), 0)
        self.assertAlmostEqual(sum(dist.pmf), 1.0, places=9)

    def test_guarantee_rule_matches_closed_form(self):
        b = make_banner(base_rate_pct=10, soft_pity_start=50, soft_pity_increase_pct=0,
                        hard_pity=50, rule=RULE_GUARANTEE)
        dist = pity_distribution(b)
        for n in range(1, 50):
            self.assertAlmostEqual(dist.cdf(n), 1 - 0.9 ** n, places=12)
        self.assertAlmostEqual(dist.cdf(50), 1.0, places=12)

    def test_quantile(self):
        b = make_banner(base_rate_pct=10, soft_pity_start=50, soft_pity_increase_pct=0,
                        hard_pity=50, rule=RULE_GUARANTEE)
        dist = pity_distribution(b)
        self.assertEqual(dist.quantile(0.5), 7)  # 1 - 0.9^7 = 0.522 >= 0.5

    def test_matches_monte_carlo(self):
        banner = make_banner(base_rate_pct=5.0, soft_pity_start=20,
                             soft_pity_increase_pct=10.0, hard_pity=30, cost_per_pull=1)
        for pity_count, guaranteed in ((0, False), (12, False), (0, True)):
            with self.subTest(pity_count=pity_count, guaranteed=guaranteed):
                dp = pity_distribution(banner, pity_count, guaranteed)
                samples = _simulate(banner, pity_count, guaranteed, trials=60_000, seed=20260315)
                self.assertAlmostEqual(dp.expected_pulls, statistics.fmean(samples), delta=0.6)
                for n in (3, 8, 15, 25):
                    empirical = sum(1 for t in samples if t <= n) / len(samples)
                    self.assertAlmostEqual(dp.cdf(n), empirical, delta=0.01)


class HardPityTest(unittest.TestCase):
    def test_hard_pity_guarantees_probability_one_at_n_for_guarantee_rule(self):
        b = make_banner(base_rate_pct=0.1, soft_pity_start=50, soft_pity_increase_pct=0,
                        hard_pity=50, rule=RULE_GUARANTEE)
        dist = pity_distribution(b, 0)
        self.assertAlmostEqual(dist.cdf(50), 1.0, places=12)
        self.assertLess(dist.cdf(49), 1.0)

    def test_hard_pity_guarantees_probability_one_when_guaranteed_flag_set(self):
        b = make_banner(base_rate_pct=0.1, soft_pity_start=50, soft_pity_increase_pct=0,
                        hard_pity=50)
        dist = pity_distribution(b, pity_count=12, guaranteed=True)
        self.assertAlmostEqual(dist.cdf(50 - 12), 1.0, places=12)
        self.assertEqual(dist.worst_case_pulls, 38)

    def test_fifty_fifty_needs_at_most_two_hard_pity_cycles(self):
        b = make_banner(base_rate_pct=0.1, soft_pity_start=50, soft_pity_increase_pct=0,
                        hard_pity=50)
        dist = pity_distribution(b, 0)
        self.assertAlmostEqual(dist.cdf(100), 1.0, places=12)
        self.assertLess(dist.cdf(99), 1.0)
        self.assertEqual(dist.worst_case_pulls, 100)


class FiftyFiftyTest(unittest.TestCase):
    def test_fifty_fifty_expected_hits_are_one_and_a_half(self):
        # 적중 확률 100%: 매 뽑기가 5성. 50/50 이면 첫 적중 픽업 0.5, 아니면 다음 적중 확정.
        b = make_banner(base_rate_pct=100, soft_pity_start=5, soft_pity_increase_pct=0, hard_pity=10)
        dist = pity_distribution(b, 0)
        self.assertAlmostEqual(dist.pmf[1], 0.5)
        self.assertAlmostEqual(dist.pmf[2], 0.5)
        self.assertAlmostEqual(dist.expected_pulls, 1.5)

    def test_fifty_fifty_doubles_worst_case_compared_to_guarantee(self):
        b_5050 = make_banner(base_rate_pct=100, soft_pity_start=5, soft_pity_increase_pct=0, hard_pity=10)
        b_guar = make_banner(base_rate_pct=100, soft_pity_start=5, soft_pity_increase_pct=0,
                             hard_pity=10, rule=RULE_GUARANTEE)
        self.assertEqual(pity_distribution(b_guar).worst_case_pulls, 1)
        self.assertEqual(pity_distribution(b_5050).worst_case_pulls, 2)

    def test_worst_case_from_fresh_is_twice_hard_pity(self):
        b = make_banner(hard_pity=20)
        self.assertEqual(pity_distribution(b, 0).worst_case_pulls, 40)
        self.assertEqual(pity_distribution(b, 0, guaranteed=True).worst_case_pulls, 20)

    def test_carried_pity_count_shortens_worst_case(self):
        b = make_banner(hard_pity=20)
        self.assertEqual(pity_distribution(b, 5).worst_case_pulls, 35)
        self.assertEqual(pity_distribution(b, 19).worst_case_pulls, 21)

    def test_pity_count_out_of_range_is_rejected(self):
        b = make_banner(hard_pity=20)
        with self.assertRaises(ValidationError):
            pity_distribution(b, 20)
        with self.assertRaises(ValidationError):
            pity_distribution(b, -1)


class ReportTest(unittest.TestCase):
    def test_verdict_line_format(self):
        self.assertEqual(
            verdict_line(230_000, 0.41),
            "천장까지 약 23만원, 지금 있는 재화로 확률 41% — 존버 추천",
        )

    def test_report_budget_and_verdict(self):
        banner = Banner(name="예시", base_rate_pct=0.6, soft_pity_start=74,
                        soft_pity_increase_pct=6.0, hard_pity=90, cost_per_pull=160)
        package = Package(price_krw=6480, units=300, unit_name="젬")

        empty = build_pity_report(banner, package, currency_units=0)
        self.assertEqual(empty.budget_pulls, 0)
        self.assertEqual(empty.p_within_budget, 0.0)
        self.assertIn("존버 추천", empty.verdict)

        rich = build_pity_report(banner, package, currency_units=160 * 1000)
        self.assertEqual(rich.budget_pulls, 1000)
        self.assertAlmostEqual(rich.p_within_budget, 1.0, places=9)
        self.assertIn("사실상 확정", rich.verdict)

    def test_report_worst_cost_uses_package_rounding(self):
        banner = Banner(name="예시", base_rate_pct=0.6, soft_pity_start=74,
                        soft_pity_increase_pct=6.0, hard_pity=90, cost_per_pull=160)
        package = Package(price_krw=6480, units=300, unit_name="젬")
        report = build_pity_report(banner, package, currency_units=0)
        # 최악 180회 x 160젬 = 28800젬 -> 300젬 패키지 96개 -> 6480 * 96
        self.assertEqual(report.worst_pulls, 180)
        self.assertEqual(report.worst_krw, 6480 * 96)


if __name__ == "__main__":
    unittest.main()
