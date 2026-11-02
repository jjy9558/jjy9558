"""합성 매물 25개를 끝까지(파싱 -> 판정 -> 질문 -> 리포트) 돌려 보는 통합 테스트."""

import unittest

from tests import fixtures as F
from tests.helpers import Base, make_table, parse, TODAY
from usedpc.judge import judge
from usedpc.questions import make_questions
from usedpc.report import render_judgment, render_judgment_md
from usedpc.upgrade import advise


def storage(p):
    return [(i["kind"], i["gb"]) for i in (p.val("storage") or [])]


class FullListings(Base):
    def p(self, name):
        return parse(getattr(F, name))

    def test_daangn_clean(self):
        p = self.p("L_DAANGN_CLEAN")
        self.assertEqual(
            (p.val("cpu"), p.val("gpu"), p.val("ram_gb"), p.val("ram_type"), p.val("ram_speed")),
            ("i5-12400F", "RTX 3060 12GB", 16, "DDR4", 3200))
        self.assertEqual(storage(p), [("nvme", 500)])
        self.assertEqual((p.val("psu_watt"), p.val("psu_grade"), p.val("psu_brand")), (600, "Bronze", "마이크로닉스"))
        self.assertEqual((p.val("board"), p.val("price"), p.val("negotiable")), ("B660", 650000, True))
        self.assertEqual(p.flags, [])

    def test_bunjang_slang(self):
        p = self.p("L_BUNJANG_SLANG")
        self.assertEqual((p.val("cpu"), p.val("gpu"), p.val("ram_gb"), p.val("price")),
                         ("i5-12400F", "RTX 3060 Ti", 16, 700000))
        self.assertEqual(storage(p), [("sata_ssd", 500)])
        self.assertIn("urgent", p.flag_codes())

    def test_quasar_highend(self):
        p = self.p("L_QUASAR_HIGHEND")
        self.assertEqual((p.val("cpu"), p.val("gpu")), ("Ryzen 7 7800X3D", "RTX 4080 Super"))
        self.assertEqual((p.val("ram_gb"), p.val("ram_type"), p.val("ram_speed")), (32, "DDR5", 6000))
        self.assertEqual(storage(p), [("nvme", 2000)])
        self.assertEqual((p.val("psu_watt"), p.val("psu_grade"), p.val("psu_brand")), (1000, "Titanium", "Seasonic"))
        self.assertEqual((p.val("board"), p.val("price")), ("B650", 2950000))
        self.assertEqual(p.val("cooler")["kind"], "aio")
        self.assertEqual(p.val("cooler")["size"], 360)
        self.assertEqual(p.val("warranty")["until"], "2027-05")
        self.assertIn("프랙탈", p.val("case"))

    def test_jungna_old(self):
        p = self.p("L_JUNGNA_OLD")
        self.assertEqual((p.val("cpu"), p.val("gpu"), p.val("ram_gb")), ("i7-8700K", "GTX 1060 6GB", 16))
        self.assertEqual(storage(p), [("ssd", 256), ("hdd", 1000)])
        self.assertEqual((p.val("psu_watt"), p.val("board"), p.val("price")), (500, "Z370", 250000))
        self.assertEqual(p.val("cooler")["size"], 240)
        self.assertGreater(p.val("age_years"), 7)

    def test_scam(self):
        p = self.p("L_SCAM")
        self.assertEqual((p.val("cpu"), p.val("gpu"), p.val("ram_gb"), p.val("price")),
                         ("i9-14900K", "RTX 4090", 64, 1300000))
        for code in ("prepay_direct", "messenger", "too_good", "urgent"):
            self.assertIn(code, p.flag_codes())
        j = judge(p, make_table(), TODAY)
        self.assertEqual(j.verdict, "의심")

    def test_mining_negation_vs_confirmation(self):
        a = self.p("L_NO_MINING")
        self.assertNotIn("mining", a.flag_codes())
        self.assertEqual((a.val("gpu"), a.val("price")), ("RX 6800 XT", 350000))
        b = self.p("L_MINING")
        self.assertIn("mining", b.flag_codes())
        self.assertEqual(b.val("gpu"), "RTX 3080 10GB")
        self.assertEqual(b.val("price"), 320000)

    def test_broken(self):
        p = self.p("L_BROKEN")
        self.assertIn("boot_fail", p.flag_codes())
        self.assertEqual((p.val("cpu"), p.val("price")), ("i5-10400F", 80000))
        j = judge(p, make_table(), TODAY)
        self.assertNotEqual(j.verdict, "의심")

    def test_gpu_only(self):
        p = self.p("L_GPU_ONLY")
        self.assertEqual((p.val("gpu"), p.val("kind"), p.val("price")), ("RTX 3070", "single_part", 300000))
        j = judge(p, make_table(), TODAY)
        self.assertEqual([i.category for i in j.items], ["gpu"])
        self.assertEqual(j.verdict, "싸다")     # 30만 < 305000 (테스트 표 33만 기준 하한)

    def test_bare_numbers(self):
        p = self.p("L_BARE_NUMBERS")
        self.assertEqual((p.val("cpu"), p.val("gpu"), p.val("ram_gb"), p.val("psu_watt"), p.val("price")),
                         ("Ryzen 5 5600", "RX 6600", 16, 500, 400000))
        self.assertEqual(storage(p), [("ssd", 500)])

    def test_korean_digits(self):
        p = self.p("L_KOREAN_DIGITS")
        self.assertEqual((p.val("cpu"), p.val("gpu"), p.val("ram_type"), p.val("ram_speed"), p.val("ram_gb")),
                         ("i5-12400F", "RTX 3060 Ti", "DDR5", 6400, 32))
        self.assertEqual(p.val("price"), 600000)

    def test_emoji_style(self):
        p = self.p("L_EMOJI")
        self.assertEqual((p.val("cpu"), p.val("gpu"), p.val("ram_gb"), p.val("psu_watt"), p.val("psu_grade"),
                          p.val("price")), ("Ryzen 5 5600X", "RTX 3070", 16, 750, "Gold", 850000))
        self.assertEqual(storage(p), [("ssd", 1000)])

    def test_price_variants(self):
        self.assertEqual(self.p("L_PRICE_BARE_LABEL").val("price"), 850000)
        self.assertEqual(self.p("L_PRICE_SELL_SUFFIX").val("price"), 550000)
        self.assertEqual(self.p("L_PRICE_K").val("price"), 850000)
        p = self.p("L_PRICE_MIXED")
        self.assertEqual((p.val("price"), p.val("original_price")), (955000, 1500000))
        self.assertFalse(self.p("L_PRICE_NONE").has("price"))

    def test_monitor_bundle_and_body_only(self):
        p = self.p("L_MONITOR_BUNDLE")
        self.assertTrue(p.val("monitor")["included"])
        self.assertEqual(p.val("peripherals"), ["마우스", "키보드"])
        j = judge(p, make_table(), TODAY)
        self.assertEqual(len(j.extras), 2)
        q = self.p("L_BODY_ONLY")
        self.assertFalse(q.val("monitor")["included"])
        self.assertTrue(q.val("body_only"))

    def test_ultra(self):
        p = self.p("L_ULTRA")
        self.assertEqual((p.val("cpu"), p.val("gpu"), p.val("board"), p.val("price")),
                         ("Ultra 5 245K", "RTX 5070 Ti", "Z890", 1900000))
        self.assertEqual((p.val("ram_gb"), p.val("ram_type"), p.val("ram_speed")), (32, "DDR5", 6400))

    def test_laptop(self):
        p = self.p("L_LAPTOP")
        self.assertEqual(p.val("kind"), "laptop")
        self.assertEqual(judge(p, make_table(), TODAY).verdict, "판단불가")

    def test_psu_unknown_and_noise(self):
        p = self.p("L_PSU_UNKNOWN")
        self.assertIn("psu_unknown", p.flag_codes())
        self.assertIn("cooler_noise", p.flag_codes())

    def test_warranty_cases(self):
        a = self.p("L_WARRANTY_EXPIRED")
        self.assertFalse(a.val("warranty")["has"])
        self.assertIn("no_receipt", a.flag_codes())
        b = self.p("L_WARRANTY_RECEIPT")
        w = b.val("warranty")
        self.assertEqual((w["until"], w["months_left"], w["receipt"]), ("2028-03", 16, True))
        self.assertEqual(b.val("gpu"), "RTX 4070 Super")

    def test_generation_only(self):
        p = self.p("L_GEN_ONLY")
        self.assertTrue(p.fields["cpu"].meta.get("generic"))
        self.assertEqual(p.val("price"), 600000)

    def test_no_gpu_listing(self):
        p = self.p("L_NO_GPU")
        self.assertIn("gpu", p.excluded)
        self.assertEqual((p.val("cpu"), p.val("price")), ("i5-12400", 250000))

    def test_vram_variants(self):
        self.assertEqual(self.p("L_VRAM_4060TI16").val("gpu"), "RTX 4060 Ti 16GB")
        f = self.p("L_VRAM_UNSPEC").fields["gpu"]
        self.assertEqual(f.value, "RTX 3060 12GB")
        self.assertTrue(f.meta["defaulted"])

    def test_prepay_listing(self):
        p = self.p("L_PREPAY")
        self.assertIn("prepay_direct", p.flag_codes())
        self.assertIn("no_safe_pay", p.flag_codes())
        self.assertEqual(judge(p, make_table(), TODAY).verdict, "의심")

    def test_positive_listing(self):
        p = self.p("L_POSITIVE")
        self.assertEqual(p.flags, [])
        self.assertGreaterEqual(len(p.positives), 6)
        self.assertEqual((p.val("cpu"), p.val("gpu"), p.val("ram_gb"), p.val("price")),
                         ("i5-13600K", "RTX 4070", 32, 1300000))

    def test_cooler_case_variants(self):
        p = self.p("L_COOLER_AIR")
        self.assertEqual(p.val("cooler")["kind"], "stock")
        self.assertIn("다크플래쉬", p.val("case"))

    def test_storage_mix(self):
        p = self.p("L_STORAGE_MIX")
        self.assertEqual(storage(p), [("nvme", 1000), ("hdd", 2000), ("sata_ssd", 500)])
        self.assertEqual((p.val("ram_gb"), p.val("ram_sticks")), (16, 2))

    def test_refurb(self):
        p = self.p("L_REFURB")
        self.assertEqual(p.val("gpu"), "RTX 3060 Ti")
        self.assertIn("refurb", p.flag_codes())
        self.assertIn("no_box", p.flag_codes())
        self.assertEqual(p.val("age_years"), 3.0)


class EndToEndSmoke(Base):
    """모든 합성 매물이 예외 없이 끝까지 돌고, 리포트가 한국어 핵심 섹션을 가진다."""

    def test_every_fixture_renders(self):
        table = make_table()
        self.assertGreaterEqual(len(F.ALL), 25)
        for name, text in F.ALL.items():
            p = parse(text)
            j = judge(p, table, TODAY)
            qs = make_questions(p, j)
            self.assertIn(j.verdict, ("싸다", "적정", "비싸다", "의심", "판단불가"), name)
            out = render_judgment(j, qs)
            self.assertIn("[판정 이유]", out, name)
            md = render_judgment_md(j, qs)
            self.assertIn("## 판정 이유", md, name)

    def test_every_fixture_with_upgrade_plan(self):
        table = make_table()
        for name, text in F.ALL.items():
            p = parse(text)
            j = judge(p, table, TODAY)
            for use in ("게임 1440p", "로컬 LLM 30B", "영상편집"):
                plan = advise(p, table, use, 200, TODAY, judgment=j)
                out = render_judgment(j, None, plan)
                self.assertIn("[업그레이드 추천]", out, (name, use))
                for o in plan.options:
                    self.assertLessEqual(o.outlay, 2000000, (name, use))

    def test_no_crash_on_weird_input(self):
        for text in ("", "   ", "\n\n", "ㅋㅋㅋㅋ", "!!!!!!", "1234567890", "RTX " * 50, "가" * 5000, "\x00\x01\x02", "i5-"):
            p = parse(text)
            j = judge(p, make_table(), TODAY)
            self.assertEqual(j.verdict, "판단불가", repr(text[:10]))
            render_judgment(j, make_questions(p, j))


if __name__ == "__main__":
    unittest.main()
