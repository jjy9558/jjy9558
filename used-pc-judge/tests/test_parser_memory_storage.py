import unittest

from tests.helpers import Base, parse


class RamParsing(Base):
    def test_sizes_korean(self):
        cases = {
            "램 16기가": 16,
            "메모리 32기가": 32,
            "램 32": 32,
            "램16": 16,
            "16GB 램": 16,
            "ram 64gb": 64,
            "램 16기가바이트": 16,
        }
        for t, e in cases.items():
            self.assertEqual(parse(t).val("ram_gb"), e, t)

    def test_kits(self):
        cases = {
            "램 16GB(8x2)": 16,
            "8G*2 램": 16,
            "램 16g x2 = 32g": 32,
            "램 8+8": 16,
            "삼성 DDR4 8G 2개": 16,
            "DDR5 32GB (16GBx2) 6000": 32,
            "램 8g 2개": 16,
            "2x16GB DDR5": 32,
        }
        for t, e in cases.items():
            self.assertEqual(parse(t).val("ram_gb"), e, t)

    def test_sticks(self):
        self.assertEqual(parse("램 16GB(8x2)").val("ram_sticks"), 2)
        self.assertEqual(parse("램 8g 2개").val("ram_sticks"), 2)
        self.assertIsNone(parse("램 16기가").val("ram_sticks"))

    def test_type_and_speed(self):
        p = parse("DDR5 6000 32GB")
        self.assertEqual((p.val("ram_type"), p.val("ram_speed"), p.val("ram_gb")), ("DDR5", 6000, 32))
        p = parse("램 16GB DDR4 3200")
        self.assertEqual((p.val("ram_type"), p.val("ram_speed")), ("DDR4", 3200))
        p = parse("디디알5 램 32기가")
        self.assertEqual(p.val("ram_type"), "DDR5")

    def test_type_inferred_from_speed(self):
        p = parse("램 16g 3600")
        self.assertEqual(p.val("ram_type"), "DDR4")
        self.assertLess(p.fields["ram_type"].conf, 0.9)
        p = parse("램 32g 6000")
        self.assertEqual(p.val("ram_type"), "DDR5")

    def test_korean_digit_speed(self):
        p = parse("램 DDR5 육사공공 32기가")
        self.assertEqual(p.val("ram_speed"), 6400)
        self.assertEqual(p.val("ram_gb"), 32)

    def test_bare_size_is_low_confidence_guess(self):
        p = parse("컴퓨터 판매 16g 입니다")
        f = p.fields.get("ram_gb")
        self.assertIsNotNone(f)
        self.assertLess(f.conf, 0.7)
        self.assertTrue(f.meta.get("guessed"))

    def test_ram_not_confused_with_storage(self):
        p = parse("램 16g / ssd 500g")
        self.assertEqual(p.val("ram_gb"), 16)
        self.assertEqual([(i["kind"], i["gb"]) for i in p.val("storage")], [("ssd", 500)])


class StorageParsing(Base):
    def items(self, text):
        p = parse(text)
        return [(i["kind"], i["gb"]) for i in (p.val("storage") or [])]

    def test_basic(self):
        self.assertEqual(self.items("nvme 1tb"), [("nvme", 1000)])
        self.assertEqual(self.items("SSD 500GB NVMe"), [("nvme", 500)])
        self.assertEqual(self.items("m.2 512g"), [("nvme", 512)])
        self.assertEqual(self.items("hdd 2tb"), [("hdd", 2000)])
        self.assertEqual(self.items("하드 1테라"), [("hdd", 1000)])
        self.assertEqual(self.items("ssd500"), [("ssd", 500)])
        self.assertEqual(self.items("1TB NVMe"), [("nvme", 1000)])

    def test_slang(self):
        self.assertEqual(self.items("사타 ssd 1t"), [("sata_ssd", 1000)])
        self.assertEqual(self.items("사테 ssd 500"), [("sata_ssd", 500)])
        self.assertEqual(self.items("삼실 500g"), [("ssd", 500)])
        self.assertEqual(self.items("엔비엠이 256기가"), [("nvme", 256)])

    def test_multiple(self):
        self.assertEqual(self.items("NVMe 1TB + HDD 2TB"), [("nvme", 1000), ("hdd", 2000)])
        self.assertEqual(self.items("ssd 256g + hdd 1t"), [("ssd", 256), ("hdd", 1000)])

    def test_model_hints(self):
        self.assertEqual(self.items("삼성 990 PRO 2TB"), [("nvme", 2000)])
        self.assertEqual(self.items("삼성 870 EVO ssd 500g"), [("sata_ssd", 500)])

    def test_bare_tb_is_low_confidence(self):
        p = parse("i9 14900k / 4090 / 64g / 2tb")
        st = p.val("storage")
        self.assertEqual([(i["kind"], i["gb"]) for i in st], [("ssd", 2000)])
        self.assertLess(p.fields["storage"].conf, 0.7)

    def test_small_sizes_are_ignored(self):
        self.assertEqual(self.items("램 8g 그래픽 6g"), [])


if __name__ == "__main__":
    unittest.main()
