import unittest

from tests.helpers import Base, parse


class PsuParsing(Base):
    def test_watt_variants(self):
        self.assertEqual(parse("파워 750W").val("psu_watt"), 750)
        self.assertEqual(parse("정격 700w 파워").val("psu_watt"), 700)
        self.assertEqual(parse("파워 650 와트").val("psu_watt"), 650)
        self.assertEqual(parse("80PLUS Bronze 650W").val("psu_watt"), 650)

    def test_cpu_tdp_is_not_psu(self):
        p = parse("i5 12400f 65w 쿨러")
        self.assertIsNone(p.val("psu_watt"))

    def test_brand_and_grade(self):
        p = parse("시소닉 포커스 GX-850 골드 파워")
        self.assertEqual(p.val("psu_brand"), "Seasonic")
        self.assertEqual(p.val("psu_watt"), 850)
        self.assertEqual(p.val("psu_grade"), "Gold")
        self.assertEqual(p.fields["psu_brand"].meta["tier"], "A")
        p = parse("파워 마이크로닉스 클래식2 600W 브론즈")
        self.assertEqual(p.val("psu_brand"), "마이크로닉스")
        self.assertEqual(p.val("psu_grade"), "Bronze")
        self.assertEqual(p.fields["psu_brand"].meta["tier"], "B")

    def test_80plus_notation(self):
        self.assertEqual(parse("파워 80+ 골드 750W").val("psu_grade"), "Gold")
        self.assertEqual(parse("파워 80플러스 실버 600w").val("psu_grade"), "Silver")
        self.assertEqual(parse("파워 80PLUS Platinum 850W").val("psu_grade"), "Platinum")
        self.assertEqual(parse("시소닉 prime tx-1000 80+ 티타늄 1000W 파워").val("psu_grade"), "Titanium")

    def test_model_number_watts(self):
        p = parse("파워 커세어 RM750x")
        self.assertEqual(p.val("psu_watt"), 750)
        self.assertEqual(p.val("psu_brand"), "Corsair")

    def test_multi_category_brand_needs_psu_context(self):
        # MSI 보드/그래픽카드 브랜드는 파워 문맥이 없으면 파워 브랜드가 아니다
        p = parse("MSI 박격포 B660M 보드")
        self.assertFalse(p.has("psu_brand"))
        p = parse("MSI 파워 750W")
        self.assertEqual(p.val("psu_brand"), "MSI")

    def test_psu_missing(self):
        p = parse("i5 12400f 3060 본체")
        self.assertFalse(p.has("psu_watt"))
        self.assertFalse(p.has("psu_brand"))


class BoardParsing(Base):
    def test_chipsets(self):
        cases = {
            "B660M 박격포": "B660",
            "메인보드 Z790-P": "Z790",
            "x670e 보드": "X670",
            "ASUS ROG STRIX B650-A GAMING WIFI": "B650",
            "A620M 보드": "A620",
            "h610m 보드": "H610",
            "z370 보드": "Z370",
            "b550 토마호크": "B550",
            "B860 보드": "B860",
        }
        for t, e in cases.items():
            self.assertEqual(parse(t).val("board"), e, t)

    def test_model_snippet(self):
        p = parse("보드 b660m 박격포 ddr4, 나머지 생략")
        self.assertIn("박격포", p.fields["board"].meta["model"])

    def test_not_a_board(self):
        self.assertIsNone(parse("RTX 4070 램 32g").val("board"))
        self.assertIsNone(parse("시소닉 gx-650").val("board"))


class CaseCoolerMonitor(Base):
    def test_case(self):
        self.assertIn("프랙탈", parse("케이스: 프랙탈 노스").val("case"))
        self.assertIn("다크플래쉬", parse("케이스 다크플래쉬 DLX21").val("case"))

    def test_cooler_kinds(self):
        c = parse("쿨러: 아이스 360mm 수랭").val("cooler")
        self.assertEqual((c["kind"], c["size"]), ("aio", 360))
        self.assertEqual(parse("공랭 AK620").val("cooler")["kind"], "air")
        self.assertEqual(parse("정품쿨러 사용").val("cooler")["kind"], "stock")
        c = parse("수랭 240").val("cooler")
        self.assertEqual((c["kind"], c["size"]), ("aio", 240))

    def test_monitor_included(self):
        m = parse("27인치 144hz 모니터 포함 팝니다").val("monitor")
        self.assertTrue(m["included"])
        self.assertEqual((m["inch"], m["hz"]), (27, 144))

    def test_monitor_excluded_and_body_only(self):
        self.assertFalse(parse("본체 60만 모니터 별도").val("monitor")["included"])
        p = parse("i5 12400f 3060 본체만 판매")
        self.assertTrue(p.val("body_only"))
        self.assertFalse(p.val("monitor")["included"])

    def test_peripherals(self):
        p = parse("키보드 마우스 포함해서 드려요")
        self.assertEqual(p.val("peripherals"), ["마우스", "키보드"])
        self.assertIsNone(parse("키보드 별도 판매").val("peripherals"))


class ExclusionsAndKind(Base):
    def test_exclusions(self):
        p = parse("본체 램 제외 파워 별도 판매 쿨러 없이 ssd 제외")
        for part in ("ram", "psu", "cooler", "ssd"):
            self.assertIn(part, p.excluded)

    def test_kind(self):
        self.assertEqual(parse("삼성 갤럭시북 i7 노트북 판매 70만").val("kind"), "laptop")
        self.assertEqual(parse("그래픽카드 단품 RTX 3070 8G 30만").val("kind"), "single_part")
        self.assertEqual(parse("RX 6800 XT 35만").val("kind"), "single_part")
        self.assertEqual(parse("i5 12400f 3060 램 16 ssd 500 파워 600w 본체 60만").val("kind"), "full_pc")

    def test_brand_pc(self):
        self.assertTrue(parse("삼성 데스크탑 i5 8400 본체").val("brand_pc"))


class WarrantyAge(Base):
    def test_warranty_until(self):
        w = parse("2027년 3월까지 무상 AS 남음").val("warranty")
        self.assertTrue(w["has"])
        self.assertEqual(w["until"], "2027-03")
        self.assertEqual(w["months_left"], 4)   # 2026-11 -> 2027-03
        w = parse("제조사 보증 27년 5월까지").val("warranty")
        self.assertEqual(w["until"], "2027-05")
        w = parse("무상AS 2027.03 까지").val("warranty")
        self.assertEqual(w["until"], "2027-03")

    def test_warranty_remaining_period(self):
        w = parse("AS 1년 남음").val("warranty")
        self.assertEqual(w["months_left"], 12)
        w = parse("보증 6개월 남아있음").val("warranty")
        self.assertEqual(w["months_left"], 6)

    def test_warranty_expired(self):
        self.assertFalse(parse("as 만료됨").val("warranty")["has"])
        self.assertFalse(parse("무보증 판매").val("warranty")["has"])
        # 날짜가 이미 지난 경우
        w = parse("무상 AS 2025년 3월까지").val("warranty")
        self.assertFalse(w["has"])
        self.assertEqual(w["months_left"], 0)

    def test_warranty_without_period(self):
        w = parse("삼성 정품 보증 남아있어요").val("warranty")
        self.assertTrue(w["has"])
        self.assertIsNone(w["months_left"])

    def test_receipt(self):
        self.assertTrue(parse("영수증 있음").val("warranty")["receipt"])
        self.assertFalse(parse("영수증 없음 보증 남음").val("warranty")["receipt"])

    def test_age(self):
        self.assertAlmostEqual(parse("2023년 구매").val("age_years"), 3.4, delta=0.7)
        self.assertEqual(parse("3년 사용").val("age_years"), 3.0)
        self.assertAlmostEqual(parse("6개월 사용").val("age_years"), 0.5, places=1)
        self.assertEqual(parse("작년 구매").val("age_years"), 1.0)
        self.assertIsNone(parse("2027년까지 보증").val("age_years"))


if __name__ == "__main__":
    unittest.main()


class SloppyListings(Base):
    """대충 쓴 글: 단위/라벨이 빠진 숫자들."""

    def test_all_bare_slash_list(self):
        p = parse("i5 12400F / 3060 12G / 16G / 500G 55")
        self.assertEqual(p.val("cpu"), "i5-12400F")
        self.assertEqual(p.val("gpu"), "RTX 3060 12GB")
        self.assertEqual(p.val("ram_gb"), 16)
        self.assertEqual([(i["kind"], i["gb"]) for i in p.val("storage")], [("ssd", 500)])
        self.assertLess(p.fields["storage"].conf, 0.7)
        self.assertEqual(p.val("price"), 550000)

    def test_psu_watt_without_w(self):
        p = parse("램 16 ssd 500 파워 600 본체 40만")
        self.assertEqual(p.val("psu_watt"), 600)
        self.assertEqual(p.val("price"), 400000)
        p = parse("5800X3D 3080 10G 램 32 (16*2) 2TB 파워 850 시소닉 골드 160 에 팝니다")
        self.assertEqual(p.val("psu_watt"), 850)
        self.assertEqual(p.val("psu_brand"), "Seasonic")
        self.assertEqual(p.val("price"), 1600000)

    def test_unknown_old_gpu_leaves_a_note(self):
        p = parse("롤 오버워치 되는 컴퓨터 20만원 i5 4590 gtx 750ti")
        self.assertIsNone(p.val("gpu"))
        self.assertTrue(any("인식하지 못한 그래픽카드" in n for n in p.notes))
        self.assertEqual(p.val("price"), 200000)

    def test_ram_label_number_at_title_end_is_not_price(self):
        self.assertIsNone(parse("i5 12400f 3060 본체 램 16").val("price"))
        self.assertIsNone(parse("i5 12400f 3060 본체 ssd 500").val("price"))
