import unittest

from tests.helpers import Base, parse


class CpuParsing(Base):
    def check(self, text, expected):
        p = parse(text)
        self.assertEqual(p.val("cpu"), expected, text)

    def test_intel_explicit(self):
        cases = {
            "인텔 i5-12400F 팝니다": "i5-12400F",
            "i5 12400f 본체": "i5-12400F",
            "코어 i7 12700F": "i7-12700F",
            "i5 13600K 팝니다": "i5-13600K",
            "I7-13700KF 새제품급": "i7-13700KF",
            "인텔 14700K 38만": "i7-14700K",
            "i9 14900ks": "i9-14900KS",
            "i3-12100f 6만": "i3-12100F",
            "i7 8700k 구형": "i7-8700K",
            "i5-10400F": "i5-10400F",
        }
        for t, e in cases.items():
            self.check(t, e)

    def test_intel_ultra(self):
        self.check("울트라5 245K 새제품", "Ultra 5 245K")
        self.check("Core Ultra 7 265KF", "Ultra 7 265KF")
        self.check("Ultra 9 285K 판매 62만", "Ultra 9 285K")
        self.check("인텔 코어 울트라 5 225F", "Ultra 5 225F")

    def test_ultra_k_is_not_a_price(self):
        p = parse("울트라5 245K + RTX 5070 Ti 본체 190만")
        self.assertEqual(p.val("price"), 1900000)
        self.assertEqual(p.val("cpu"), "Ultra 5 245K")

    def test_ryzen_explicit(self):
        cases = {
            "라이젠5 5600X": "Ryzen 5 5600X",
            "라이젠7 5800X3D": "Ryzen 7 5800X3D",
            "Ryzen 7 7800X3D": "Ryzen 7 7800X3D",
            "r5 5600 팝니다 9만": "Ryzen 5 5600",
            "라이젠 7600": "Ryzen 5 7600",
            "라이젠 9 9950X3D": "Ryzen 9 9950X3D",
            "AMD 5700x": "Ryzen 7 5700X",
            "ryzen 5 5600g": "Ryzen 5 5600G",
        }
        for t, e in cases.items():
            self.check(t, e)

    def test_bare_models(self):
        cases = {
            "12400f 본체": "i5-12400F",
            "13600k 팝니다": "i5-13600K",
            "7800x3d 팝니다 35만": "Ryzen 7 7800X3D",
            "5800x3d 30만": "Ryzen 7 5800X3D",
            "9700x 새제품": "Ryzen 7 9700X",
            "14700kf": "i7-14700KF",
        }
        for t, e in cases.items():
            self.check(t, e)

    def test_generation_only(self):
        p = parse("인텔 i5 12세대 + 3060 본체 60만 램 16 ssd 500")
        self.assertEqual(p.val("cpu"), "i5-12400F")
        f = p.fields["cpu"]
        self.assertLess(f.conf, 0.5)
        self.assertTrue(f.meta.get("generic"))
        p2 = parse("13세대 i7 본체")
        self.assertEqual(p2.fields["cpu"].meta.get("gen"), 13)

    def test_unknown_model_is_approximated(self):
        p = parse("i5-12490F 본체")
        f = p.fields.get("cpu")
        self.assertIsNotNone(f)
        self.assertTrue(f.meta.get("approx"))
        self.assertLess(f.conf, 0.7)

    def test_ambiguous_number_uses_other_part(self):
        # 5600 + 6600: 라이젠 5600 + RX 6600 으로 해석
        p = parse("5600 + 6600 본체 40만")
        self.assertEqual(p.val("cpu"), "Ryzen 5 5600")
        self.assertEqual(p.val("gpu"), "RX 6600")
        # CPU가 명시되면 7600 은 RX 7600
        p = parse("i5 12400f 7600 본체")
        self.assertEqual(p.val("cpu"), "i5-12400F")
        self.assertEqual(p.val("gpu"), "RX 7600")
        # GPU가 명시되면 7600 은 라이젠 7600
        p = parse("rtx 4060 7600 본체")
        self.assertEqual(p.val("cpu"), "Ryzen 5 7600")

    def test_ram_speed_is_not_cpu(self):
        p = parse("i5 12400f 램 ddr4 3600 16gb")
        self.assertEqual(p.val("cpu"), "i5-12400F")
        self.assertEqual(p.val("ram_speed"), 3600)
        p = parse("ddr5 5600 32gb 3060 본체")
        self.assertIsNone(p.val("cpu"))
        self.assertEqual(p.val("ram_speed"), 5600)

    def test_korean_digit_reading(self):
        p = parse("삼공육공티아이 일이사공공에프 완본체")
        self.assertEqual(p.val("gpu"), "RTX 3060 Ti")
        self.assertEqual(p.val("cpu"), "i5-12400F")

    def test_no_cpu(self):
        self.assertIsNone(parse("그래픽카드 RTX 3070 팝니다").val("cpu"))


class GpuParsing(Base):
    def check(self, text, expected):
        p = parse(text)
        self.assertEqual(p.val("gpu"), expected, text)

    def test_nvidia_variants(self):
        cases = {
            "RTX3060TI 팝니다 20만": "RTX 3060 Ti",
            "지포스 3060 Ti 8g 팝니다": "RTX 3060 Ti",
            "gtx1660super 9만": "GTX 1660 Super",
            "GTX 1660 Super": "GTX 1660 Super",
            "rtx 5070ti 새제품급": "RTX 5070 Ti",
            "4070ti super 팝니다": "RTX 4070 Ti Super",
            "4070tis 95만": "RTX 4070 Ti Super",
            "RTX 4080S 120만": "RTX 4080 Super",
            "rtx 4070 슈퍼 12g": "RTX 4070 Super",
            "RTX 4070 수퍼": "RTX 4070 Super",
            "알티엑스 3070": "RTX 3070",
            "rtx 2060": "RTX 2060",
            "RTX 5090 팝니다": "RTX 5090",
            "RTX 5060": "RTX 5060",
            "rtx 3090 24g": "RTX 3090",
            "1660s 팝니다": "GTX 1660 Super",
            "3060ti 팝니다": "RTX 3060 Ti",
            "4060 팝니다 램 16": "RTX 4060",
        }
        for t, e in cases.items():
            self.check(t, e)

    def test_amd_and_intel(self):
        cases = {
            "RX 6700XT 28만": "RX 6700 XT",
            "라데온 RX 7900 XTX 90만": "RX 7900 XTX",
            "rx6600 15만": "RX 6600",
            "9070 xt 80만": "RX 9070 XT",
            "라데온 6800xt": "RX 6800 XT",
            "RX 7800 XT 16GB": "RX 7800 XT",
            "rx 7600 xt": "RX 7600 XT",
            "인텔 arc b580": "Arc B580",
        }
        for t, e in cases.items():
            self.check(t, e)

    def test_vram_variants(self):
        self.check("RTX 3060 8GB", "RTX 3060 8GB")
        self.check("RTX 3060 12GB", "RTX 3060 12GB")
        self.check("RTX 4060 Ti 16G", "RTX 4060 Ti 16GB")
        self.check("4060ti 16기가 팝니다 램 32기가", "RTX 4060 Ti 16GB")
        self.check("rtx 3080 12g", "RTX 3080 12GB")
        self.check("RTX 5060 Ti 16GB", "RTX 5060 Ti 16GB")

    def test_vram_not_stolen_from_ram(self):
        p = parse("RTX 4070 12G 램 32g 본체")
        self.assertEqual(p.val("gpu"), "RTX 4070")
        self.assertEqual(p.val("ram_gb"), 32)
        p = parse("3060 12g 8g 램")
        self.assertEqual(p.val("gpu"), "RTX 3060 12GB")
        self.assertEqual(p.val("ram_gb"), 8)

    def test_default_variant_is_flagged(self):
        p = parse("i5 12400f RTX 3060 본체 램 16 ssd 500 55만")
        f = p.fields["gpu"]
        self.assertEqual(f.value, "RTX 3060 12GB")
        self.assertTrue(f.meta.get("defaulted"))
        self.assertEqual(f.meta.get("variants"), [8, 12])
        self.assertLess(f.conf, 0.9)
        p = parse("RTX 4060 Ti 팝니다")
        self.assertTrue(p.fields["gpu"].meta.get("defaulted"))

    def test_gpu_candidates_note(self):
        p = parse("RTX 3060 Ti 랑 RTX 3070 둘 다 있어요")
        self.assertIn("gpu_candidates", p.fields)

    def test_year_or_resolution_is_not_gpu(self):
        p = parse("2023년 구매 1080p 144hz 모니터 포함")
        self.assertIsNone(p.val("gpu"))
        p = parse("풀hd 1080 모니터")
        self.assertIsNone(p.val("gpu"))

    def test_gpu_exclusion(self):
        p = parse("i5 12400 사무용 본체 그래픽카드 제외 팝니다 25만원")
        self.assertIn("gpu", p.excluded)


if __name__ == "__main__":
    unittest.main()
