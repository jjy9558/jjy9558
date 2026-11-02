import unittest

from tests.helpers import Base, make_table, parse, TODAY
from usedpc.judge import cpu_has_igpu, judge

# 모든 부품이 명시된 완본체 (가격 줄은 테스트마다 붙임)
FULL = """i5 12400F, RTX 3060 12G, 램 16GB DDR4 3200, NVMe 500G, B660M,
파워 시소닉 650W 골드, 정품쿨러, 케이스 다크플래쉬 DLX21
"""
# 테스트 가격표 기준 부품 합계:
#  CPU 100000 + GPU 200000 + RAM 16*2500=40000 + NVMe 500*80=40000 + 보드 100000
#  + 파워 40000(650W)*1.25(골드)*1.25(시소닉 A등급)=62500 + 케이스 30000 + 정품쿨러 0 = 572500
PARTS_SUM = 572500


def run(text, table=None, **kw):
    table = table or make_table(**kw)
    p = parse(text)
    return judge(p, table, TODAY)


class FairValueMath(Base):
    def test_parts_sum_and_assembled_discount(self):
        j = run(FULL + "가격 60만원")
        self.assertEqual(j.parts_sum, PARTS_SUM)
        adj = {a.label.split(" (")[0]: a.amount for a in j.adjustments}
        self.assertEqual(adj["완본체 보정"], -40075)         # 572500 * (0.93 - 1)
        self.assertEqual(j.fair_mid, 530000)               # 532425 -> 5천원 단위 반올림
        self.assertEqual(j.fair_low, 490000)               # *0.92
        self.assertEqual(j.fair_high, 575000)              # *1.08
        self.assertEqual(j.confidence, "높음")

    def test_items_are_itemised(self):
        j = run(FULL + "가격 60만원")
        amounts = {i.category: i.amount for i in j.items}
        self.assertEqual(amounts["cpu"], 100000)
        self.assertEqual(amounts["gpu"], 200000)
        self.assertEqual(amounts["ram"], 40000)
        self.assertEqual(amounts["storage"], 40000)
        self.assertEqual(amounts["board"], 100000)
        self.assertEqual(amounts["psu"], 62500)
        self.assertEqual(amounts["case"], 30000)
        self.assertEqual(amounts["cooler"], 0)

    def test_assembled_factor_is_editable(self):
        j = run(FULL + "가격 60만원", assembled_factor=1.0)
        self.assertEqual(j.fair_mid, 570000)   # 572500 -> 5천원 반올림

    def test_age_discount(self):
        j = run(FULL + "5년 사용 가격 60만원")
        # (5-3)*0.03 = 6% 를 완본체 보정 뒤 금액에서 차감: 532425 * 0.94
        self.assertAlmostEqual(j.fair_mid, 500480, delta=2500)
        self.assertTrue(any("사용 연수" in a.label for a in j.adjustments))
        # 3년 이하는 감가 없음
        j3 = run(FULL + "3년 사용 가격 60만원")
        self.assertFalse(any("사용 연수" in a.label for a in j3.adjustments))

    def test_age_discount_is_capped(self):
        j = run(FULL + "12년 사용 가격 60만원")
        a = [x for x in j.adjustments if "사용 연수" in x.label][0]
        self.assertAlmostEqual(a.amount, -532425 * 0.15, delta=2)

    def test_warranty_bonus(self):
        j = run(FULL + "2027년 3월까지 무상 AS 남음 가격 60만원")
        # 4개월 * 0.4% = 1.6%
        self.assertAlmostEqual(j.fair_mid, 532425 * 1.016, delta=2500)
        self.assertTrue(any("보증" in a.label for a in j.adjustments))

    def test_warranty_bonus_capped_and_receipt(self):
        j = run(FULL + "AS 5년 남음 영수증 있음 가격 60만원")
        a = [x for x in j.adjustments if "보증" in x.label][0]
        # 상한 6% + 영수증 1%
        self.assertAlmostEqual(a.amount, 532425 * 0.07, delta=2)

    def test_expired_warranty_gives_no_bonus(self):
        j = run(FULL + "AS 만료됨 가격 60만원")
        self.assertFalse(any("보증" in a.label for a in j.adjustments))

    def test_mining_discount_applies_to_gpu_only(self):
        j = run(FULL + "채굴하던 카드 가격 60만원")
        a = [x for x in j.adjustments if "채굴" in x.label][0]
        self.assertEqual(a.amount, -24000)   # GPU 200000 * 12%
        self.assertAlmostEqual(j.fair_mid, (PARTS_SUM - 24000) * 0.93, delta=2500)

    def test_refurb_discount(self):
        j = run(FULL + "리퍼 제품 가격 60만원")
        a = [x for x in j.adjustments if "리퍼" in x.label][0]
        self.assertAlmostEqual(a.amount, -PARTS_SUM * 0.06, delta=2)

    def test_defect_discount_and_not_called_scam(self):
        j = run(FULL + "부팅 안됨 부품용 가격 25만원")
        a = [x for x in j.adjustments if "불량" in x.label][0]
        self.assertAlmostEqual(a.amount, -PARTS_SUM * 0.45, delta=2)
        self.assertNotEqual(j.verdict, "의심")   # 고장품은 싸도 사기 의심이 아니라 불량 가정으로 계산

    def test_unstable_discount(self):
        j = run(FULL + "가끔 블루스크린 가격 60만원")
        self.assertTrue(any("불안정" in a.label for a in j.adjustments))

    def test_flat_deductions(self):
        j = run(FULL + "쿨러 소음 있음 박스 없음 가격 60만원")
        labels = {a.label: a.amount for a in j.adjustments}
        self.assertEqual([v for k, v in labels.items() if "소음" in k], [-20000])
        box = [v for k, v in labels.items() if "박스" in k][0]
        self.assertAlmostEqual(box, -(PARTS_SUM * 0.93) * 0.02, delta=2)

    def test_unknown_psu_replacement_cost(self):
        j = run("i5 12400F 3060 12G 램 16GB DDR4 NVMe 500G B660M 파워 무명 가격 55만")
        self.assertTrue(any("무명" in a.label and a.amount == -50000 for a in j.adjustments))
        self.assertIn("psu", j.missing)

    def test_monitor_and_peripherals_are_added_after_assembled_factor(self):
        base = run(FULL + "가격 60만원")
        j = run(FULL + "27인치 144hz 모니터 포함, 키보드 마우스 포함 가격 60만원")
        # 모니터 70000*1.4*0.9 = 88200, 키보드+마우스 18000
        self.assertEqual([a.amount for a in j.extras], [88200, 18000])
        self.assertAlmostEqual(j.fair_mid - base.fair_mid, 106200, delta=5000)

    def test_brand_pc_factor(self):
        j = run("삼성 데스크탑 i7 8700K GTX 1060 6G 램 16GB DDR4 SSD 500G Z370 가격 20만원")
        self.assertTrue(any("브랜드" in a.label for a in j.adjustments))

    def test_single_part_has_no_assembled_factor_or_assumed_parts(self):
        j = run("그래픽카드 단품 RTX 3070 8G 팝니다 30만")
        self.assertEqual([i.category for i in j.items], ["gpu"])
        self.assertEqual(j.adjustments, [])
        self.assertEqual(j.fair_mid, 330000)
        self.assertEqual(j.fair_low, 305000)
        self.assertEqual(j.fair_high, 355000)
        self.assertEqual(j.verdict, "싸다")

    def test_spread_grows_with_assumptions(self):
        full = run(FULL + "가격 60만원")
        sparse = run("i5 12400F RTX 3060 12G 본체 가격 60만원")
        self.assertGreater(sparse.spread, full.spread)
        self.assertLessEqual(sparse.spread, 0.30)
        self.assertEqual(sparse.confidence, "보통")

    def test_ssd_generic_and_hdd_priced(self):
        j = run(FULL.replace("NVMe 500G", "ssd 256g hdd 1t") + "가격 60만원")
        st = [i.amount for i in j.items if i.category == "storage"]
        self.assertEqual(sorted(st), [15360, 20000])   # 256*60, 1TB hdd


class Verdicts(Base):
    def verdict(self, price_text, extra=""):
        return run(FULL + extra + price_text).verdict

    def test_range_boundaries(self):
        self.assertEqual(self.verdict("가격 60만원"), "비싸다")      # > 57.5만
        self.assertEqual(self.verdict("가격 54만원"), "적정")
        self.assertEqual(self.verdict("가격 49만원"), "적정")        # == 하한
        self.assertEqual(self.verdict("가격 48만원"), "싸다")
        self.assertEqual(self.verdict("가격 35만원"), "싸다")        # 하한*0.65=31.85만 이상
        self.assertEqual(self.verdict("가격 30만원"), "의심")        # 너무 쌈

    def test_too_cheap_message(self):
        j = run(FULL + "가격 30만원")
        self.assertTrue(any("너무 쌉니다" in r for r in j.reasons))

    def test_scam_flags_override_fair_price(self):
        j = run(FULL + "직거래만, 예약금 먼저 입금해주세요 가격 54만원")
        self.assertEqual(j.verdict, "의심")
        self.assertTrue(any("사기 의심" in r for r in j.reasons))

    def test_multiple_moderate_scam_signals(self):
        j = run(FULL + "안전결제 불가, 텔레그램으로 연락, 시세보다 너무 싸게 가격 54만원")
        self.assertEqual(j.verdict, "의심")

    def test_too_good_phrase_plus_cheap_price(self):
        j = run(FULL + "시세보다 너무 싸게 올려요 가격 40만원")
        self.assertEqual(j.verdict, "의심")

    def test_no_price(self):
        j = run(FULL)
        self.assertEqual(j.verdict, "판단불가")
        self.assertIsNotNone(j.fair_mid)       # 범위는 계산됨
        self.assertTrue(any("판매가" in r for r in j.reasons))

    def test_missing_core_parts(self):
        self.assertEqual(run("램 16g ssd 500 본체 50만").verdict, "판단불가")             # CPU 없음
        j = run("i5 12400F 램 16GB DDR4 ssd 500G B660 본체 50만")                       # F 모델: 내장그래픽 없음
        self.assertEqual(j.verdict, "판단불가")
        self.assertIn("gpu", j.missing)

    def test_igpu_office_pc_is_priced(self):
        j = run("i5 12400 사무용 본체 램 16GB DDR4 ssd 500g B660 파워 500w 25만")
        self.assertNotEqual(j.verdict, "판단불가")
        self.assertTrue(any("내장그래픽" in a for a in j.assumptions))

    def test_gpu_excluded(self):
        j = run("i5 12400 본체 램 16GB DDR4 ssd 500g B660 파워 500w 그래픽카드 제외 25만")
        self.assertFalse(any(i.category == "gpu" for i in j.items))
        self.assertNotEqual(j.verdict, "판단불가")

    def test_laptop_is_not_judged(self):
        j = run("삼성 갤럭시북 i7 노트북 판매 70만")
        self.assertEqual(j.verdict, "판단불가")
        self.assertTrue(any("노트북" in r for r in j.reasons))

    def test_unpriced_part_is_reported(self):
        t = make_table()
        del t.data["gpu"]["RTX 3070"]
        j = run("그래픽카드 단품 RTX 3070 8G 30만", table=t)
        self.assertIn("GPU RTX 3070", j.unpriced)
        self.assertEqual(j.verdict, "판단불가")

    def test_reasons_are_line_items(self):
        j = run(FULL + "쿨러 소음 있음 급처 가격 54만원")
        self.assertGreaterEqual(len(j.reasons), 3)
        self.assertTrue(all(isinstance(r, str) and r for r in j.reasons))
        self.assertTrue(any("주의" in r and "소음" in r for r in j.reasons))

    def test_diff_pct(self):
        j = run(FULL + "가격 60만원")
        self.assertAlmostEqual(j.diff_pct, (600000 - 530000) / 530000.0 * 100, places=3)


class PriceTableWarnings(Base):
    def test_placeholder_warning_is_shown(self):
        t = make_table()
        t.data["placeholder"] = True
        j = run(FULL + "가격 60만원", table=t)
        self.assertTrue(any("임시 추정치" in w for w in j.warnings))

    def test_stale_warning(self):
        t = make_table()
        t.data["as_of"] = "2026-01-01"
        j = run(FULL + "가격 60만원", table=t)
        self.assertTrue(any("전" in w and "갱신" in w for w in j.warnings))

    def test_fresh_table_has_no_warning(self):
        self.assertEqual(run(FULL + "가격 60만원").warnings, [])


class ValueScore(Base):
    def test_cheaper_scores_higher_and_flags_penalise(self):
        cheap = run(FULL + "가격 49만원")
        fair = run(FULL + "가격 53만원")
        flagged = run(FULL + "박스 없음 쿨러 소음 가격 49만원")
        self.assertGreater(cheap.value_score, fair.value_score)
        self.assertLess(flagged.value_score, cheap.value_score)

    def test_suspicious_is_capped(self):
        j = run(FULL + "가격 30만원")
        self.assertEqual(j.verdict, "의심")
        self.assertLessEqual(j.value_score, 40)

    def test_perf_index(self):
        j = run(FULL + "가격 54만원")
        self.assertIsNotNone(j.perf_index)


class IgpuHelper(Base):
    def test_igpu(self):
        self.assertFalse(cpu_has_igpu("i5-12400F"))
        self.assertTrue(cpu_has_igpu("i5-12400"))
        self.assertFalse(cpu_has_igpu("Ryzen 5 5600"))
        self.assertTrue(cpu_has_igpu("Ryzen 5 5600G"))
        self.assertTrue(cpu_has_igpu("Ryzen 5 7600"))
        self.assertFalse(cpu_has_igpu("Ryzen 5 7500F"))
        self.assertFalse(cpu_has_igpu("Ultra 5 225F"))


if __name__ == "__main__":
    unittest.main()
