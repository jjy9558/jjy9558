import unittest

from tests.helpers import Base, make_table, parse, TODAY
from usedpc import catalog, perf
from usedpc.upgrade import (advise, bottleneck_notes, config_from, evaluate,
                            pcie_effective, recommended_psu)

LISTING = """i5 12400F RTX 3060 12G 램 16GB DDR4 NVMe 500G B660M 파워 시소닉 650W 골드 정품쿨러
가격 55만원
"""


def plan(use, budget=None, text=LISTING, table=None):
    table = table or make_table()
    p = parse(text)
    return advise(p, table, use, budget, TODAY), p, table


class UseParsing(Base):
    def test_gaming(self):
        self.assertEqual(perf.parse_use("게임 1440p")["res"], "1440p")
        self.assertEqual(perf.parse_use("게임 4K")["res"], "4k")
        self.assertEqual(perf.parse_use("게임")["res"], "1080p")
        self.assertEqual(perf.parse_use("롤 배그 QHD")["res"], "1440p")

    def test_llm(self):
        u = perf.parse_use("로컬 LLM 30B")
        self.assertEqual((u["kind"], u["params_b"]), ("llm", 30.0))
        self.assertGreater(u["vram_need"], 18)
        self.assertLess(u["vram_need"], 22)
        self.assertGreater(perf.parse_use("llm 70b")["vram_need"], 40)
        self.assertLess(perf.parse_use("llm 8b")["vram_need"], 8)
        self.assertGreater(perf.parse_use("llm 8b q8")["vram_need"], perf.parse_use("llm 8b")["vram_need"])

    def test_editing_and_others(self):
        self.assertEqual(perf.parse_use("영상편집")["kind"], "editing")
        self.assertEqual(perf.parse_use("프리미어 4k 편집")["res"], "4k")
        self.assertEqual(perf.parse_use("스테이블 디퓨전")["kind"], "diffusion")

    def test_errors(self):
        with self.assertRaises(ValueError):
            perf.parse_use("로컬 LLM")          # 크기 없음
        with self.assertRaises(ValueError):
            perf.parse_use("요리")
        with self.assertRaises(ValueError):
            perf.parse_use("")

    def test_vram_need_monotonic(self):
        a, b, c = (perf.llm_vram_need(x) for x in (8, 14, 32))
        self.assertLess(a, b)
        self.assertLess(b, c)


class Evaluate(Base):
    def test_gaming_bottleneck_and_vram(self):
        r = perf.gaming_effective("i3-12100F", "RTX 4080", "1440p")
        self.assertEqual(r["bottleneck"], "cpu")
        self.assertLess(r["eff"], catalog.GPUS["RTX 4080"]["score"])
        r = perf.gaming_effective("Ryzen 7 7800X3D", "RTX 3060 Ti", "1440p")
        self.assertEqual(r["bottleneck"], "gpu")
        self.assertTrue(r["vram_short"])        # 8GB < 1440p 기준 12GB
        self.assertIsNone(perf.gaming_effective(None, "RTX 4080"))

    def test_evaluate_meets(self):
        cfg = {"cpu": "Ryzen 7 7800X3D", "gpu": "RTX 4070 Super", "ram_gb": 32}
        self.assertTrue(evaluate(cfg, perf.parse_use("게임 1440p"))["meets"])
        cfg = {"cpu": "i5-12400F", "gpu": "RTX 3060 12GB", "ram_gb": 16}
        self.assertFalse(evaluate(cfg, perf.parse_use("게임 1440p"))["meets"])
        self.assertTrue(evaluate(cfg, perf.parse_use("게임 1080p"))["meets"])

    def test_llm_fits(self):
        cfg = {"cpu": "i5-12400F", "gpu": "RTX 3090", "ram_gb": 32}
        self.assertTrue(evaluate(cfg, perf.parse_use("llm 30b"))["meets"])
        cfg["gpu"] = "RTX 4070 Super"
        self.assertFalse(evaluate(cfg, perf.parse_use("llm 30b"))["meets"])
        self.assertTrue(evaluate(cfg, perf.parse_use("llm 8b"))["meets"])


class Advise(Base):
    def test_gaming_plan_headline_format(self):
        pl, p, t = plan("게임 1440p", budget=120)
        self.assertGreaterEqual(len(pl.options), 1)
        self.assertLessEqual(len(pl.options), 2)
        o = pl.options[0]
        self.assertTrue(o.meets)
        self.assertIn("가성비", o.label)
        self.assertTrue(o.headline.startswith("이 매물 550,000원 + "))
        self.assertIn("교체(차액 ", o.headline)
        self.assertIn("→ 총 ", o.headline)
        self.assertIn("신품 대비 ", o.headline)
        self.assertIn("절약", o.headline)
        self.assertTrue(any(s.category == "gpu" for s in o.swaps))

    def test_budget_is_respected(self):
        pl, _, _ = plan("게임 1440p", budget=100)
        for o in pl.options:
            self.assertLessEqual(o.outlay, 1000000)
        pl, _, _ = plan("게임 1440p", budget=150)
        for o in pl.options:
            self.assertLessEqual(o.outlay, 1500000)

    def test_budget_below_listing_price(self):
        pl, _, _ = plan("게임 1440p", budget=50)
        self.assertEqual(pl.options, [])
        self.assertTrue(any("예산" in m for m in pl.messages))

    def test_no_option_fits_small_remaining_budget(self):
        pl, _, _ = plan("게임 1440p", budget=56)
        self.assertEqual(pl.options, [])
        self.assertTrue(any("예산" in m for m in pl.messages))

    def test_swap_math(self):
        pl, p, t = plan("게임 1440p", budget=150)
        o = pl.options[0]
        gpu_swap = [s for s in o.swaps if s.category == "gpu"][0]
        old = t.gpu("RTX 3060 12GB")[0]
        self.assertEqual(gpu_swap.resale, int(old * t.adj("upgrade_resale_factor")))
        self.assertEqual(gpu_swap.delta, gpu_swap.new_price - gpu_swap.resale)
        self.assertEqual(o.delta_total, sum(s.delta for s in o.swaps))
        self.assertEqual(o.outlay, 550000 + o.delta_total)
        expected = (1 - o.outlay / float(o.new_equiv)) * 100
        self.assertAlmostEqual(o.saving_pct, expected, places=6)

    def test_cheapest_meeting_option_is_first(self):
        pl, _, _ = plan("게임 1440p", budget=300)
        a = pl.options[0]
        self.assertTrue(a.meets)
        if len(pl.options) > 1:
            b = pl.options[1]
            self.assertGreater(b.perf, a.perf)
            self.assertGreaterEqual(b.outlay, a.outlay)

    def test_psu_added_when_new_gpu_needs_more_power(self):
        pl, _, _ = plan("로컬 LLM 30B", budget=300)
        self.assertTrue(pl.options)
        o = pl.options[0]
        gpu_name = [s for s in o.swaps if s.category == "gpu"][0].to_name
        self.assertGreaterEqual(catalog.GPUS[gpu_name]["vram"], 20)
        need = recommended_psu(o.config)
        if need > 650 * 0.95:
            self.assertTrue(any(s.category == "psu" for s in o.swaps))
        self.assertTrue(any("파워" in n for n in o.notes))

    def test_llm_target_is_vram_based(self):
        pl, _, _ = plan("로컬 LLM 30B", budget=300)
        for o in pl.options:
            if o.meets:
                g = [s for s in o.swaps if s.category == "gpu"][0].to_name
                self.assertGreaterEqual(catalog.GPUS[g]["vram"], pl.use["vram_need"])
        self.assertIn("VRAM", pl.target_text)

    def test_llm_budget_too_small_for_24gb(self):
        pl, _, _ = plan("로컬 LLM 30B", budget=100)
        self.assertFalse(any(o.meets for o in pl.options))
        if pl.options:
            self.assertIn("못 미침", pl.options[0].label)

    def test_editing_suggests_ram_or_cpu(self):
        pl, _, _ = plan("영상편집", budget=100)
        self.assertTrue(pl.options)
        cats = {s.category for s in pl.options[0].swaps}
        self.assertTrue(cats & {"ram", "cpu"})
        self.assertTrue(any("램" in n for n in pl.current_notes))

    def test_already_meets_target(self):
        text = "7800X3D RTX 4070 Super 램 32GB DDR5 NVMe 1TB B650 파워 850w 골드 가격 150만"
        pl, _, _ = plan("게임 1440p", budget=300, text=text)
        self.assertTrue(pl.current_meets)
        self.assertTrue(any("이미 목표" in m for m in pl.messages))
        for o in pl.options:
            self.assertIn("선택 사항", o.label)

    def test_cpu_swap_stays_on_same_socket(self):
        pl, _, _ = plan("영상편집", budget=300, text="Ryzen 5 5600 RTX 3060 12G 램 16GB DDR4 NVMe 500G B550 파워 650w 가격 50만")
        for o in pl.options:
            for s in o.swaps:
                if s.category == "cpu":
                    self.assertEqual(catalog.CPUS[s.to_name]["socket"], "AM4")

    def test_missing_info(self):
        pl, _, _ = plan("게임 1440p", text="램 16 ssd 500 본체 50만")
        self.assertEqual(pl.options, [])
        self.assertTrue(any("부족" in m for m in pl.messages))

    def test_missing_price_uses_fair_value(self):
        pl, p, t = plan("게임 1440p", text=LISTING.replace("가격 55만원", ""))
        self.assertTrue(pl.price_is_fair_mid)
        self.assertTrue(any("적정가 중앙값" in m for m in pl.messages))

    def test_unknown_use_raises(self):
        p = parse(LISTING)
        with self.assertRaises(ValueError):
            advise(p, make_table(), "요리", None, TODAY)


class BottleneckNotes(Base):
    def test_cpu_bottleneck_note(self):
        notes = bottleneck_notes({"cpu": "i3-12100F", "gpu": "RTX 4080"}, perf.parse_use("게임 1440p"))
        self.assertTrue(any("CPU-GPU 균형" in n and "못 먹" in n for n in notes))

    def test_gpu_weak_note(self):
        notes = bottleneck_notes({"cpu": "Ryzen 7 7800X3D", "gpu": "GTX 1660 Super"}, perf.parse_use("게임 1440p"))
        self.assertTrue(any("GPU 쪽이 상대적으로 약" in n for n in notes))

    def test_psu_notes(self):
        use = perf.parse_use("게임 1440p")
        low = bottleneck_notes({"cpu": "i5-12400F", "gpu": "RTX 4090", "psu_watt": 550}, use)
        self.assertTrue(any("부족" in n and "파워" in n for n in low))
        ok = bottleneck_notes({"cpu": "i5-12400F", "gpu": "RTX 3060 12GB", "psu_watt": 650}, use)
        self.assertTrue(any("충족" in n for n in ok))
        unknown = bottleneck_notes({"cpu": "i5-12400F", "gpu": "RTX 3060 12GB"}, use)
        self.assertTrue(any("용량을 모릅니다" in n for n in unknown))
        noname = bottleneck_notes({"cpu": "i5-12400F", "gpu": "RTX 4070 Super", "psu_watt": 750, "psu_unknown": True}, use)
        self.assertTrue(any("무명" in n for n in noname))

    def test_pcie_note_for_x8_card_on_gen3(self):
        cfg = {"cpu": "Ryzen 5 3600", "board": "B450", "gpu": "RTX 4060"}
        self.assertEqual(pcie_effective(cfg), 3.0)
        notes = bottleneck_notes(cfg, perf.parse_use("게임 1080p"))
        self.assertTrue(any("PCIe" in n for n in notes))
        cfg = {"cpu": "i5-12400F", "board": "B660", "gpu": "RTX 4060"}
        notes = bottleneck_notes(cfg, perf.parse_use("게임 1080p"))
        self.assertFalse(any("PCIe" in n for n in notes))

    def test_pcie_limited_by_cpu(self):
        # 라이젠 5500은 PCIe 3.0 까지만, 보드가 B550(4.0)이어도 3.0
        self.assertEqual(pcie_effective({"cpu": "Ryzen 5 5500", "board": "B550"}), 3.0)
        self.assertEqual(pcie_effective({"cpu": "Ryzen 5 5600", "board": "B550"}), 4.0)
        self.assertEqual(pcie_effective({"cpu": "Ryzen 5 5600", "board": "B450"}), 3.0)

    def test_ddr_platform_notes(self):
        use = perf.parse_use("게임 1440p")
        am4 = bottleneck_notes({"cpu": "Ryzen 5 5600", "gpu": "RX 6600", "ram_type": "DDR4"}, use)
        self.assertTrue(any("AM4" in n for n in am4))
        lga = bottleneck_notes({"cpu": "i5-12400F", "gpu": "RX 6600", "ram_type": "DDR4"}, use)
        self.assertTrue(any("DDR4 보드는 DDR5로 못" in n for n in lga))
        d5 = bottleneck_notes({"cpu": "Ryzen 5 7600", "gpu": "RX 6600", "ram_type": "DDR5"}, use)
        self.assertTrue(any("DDR5 플랫폼" in n for n in d5))
        amb = bottleneck_notes({"cpu": "i5-12400F", "gpu": "RX 6600"}, use)
        self.assertTrue(any("DDR4/DDR5 중 하나만" in n for n in amb))

    def test_single_channel_and_capacity(self):
        n = bottleneck_notes({"cpu": "i5-12400F", "gpu": "RX 6600", "ram_gb": 8, "ram_sticks": 1}, perf.parse_use("게임 1080p"))
        self.assertTrue(any("싱글 채널" in x for x in n))
        self.assertTrue(any("8GB" in x for x in n))

    def test_bios_note(self):
        n = bottleneck_notes({"cpu": "Ryzen 5 5600", "gpu": "RX 6600", "board": "B450"}, perf.parse_use("게임 1080p"))
        self.assertTrue(any("BIOS" in x for x in n))

    def test_recommended_psu(self):
        self.assertGreaterEqual(recommended_psu({"cpu": "i9-14900K", "gpu": "RTX 4090"}), 850)
        self.assertLessEqual(recommended_psu({"cpu": "i5-12400F", "gpu": "GTX 1660 Super"}), 550)
        self.assertIsNone(recommended_psu({"cpu": "i5-12400F"}))

    def test_config_from_parsed(self):
        cfg = config_from(parse(LISTING))
        self.assertEqual(cfg["gpu"], "RTX 3060 12GB")
        self.assertEqual(cfg["psu_watt"], 650)
        self.assertEqual(cfg["psu_tier"], "A")


if __name__ == "__main__":
    unittest.main()
