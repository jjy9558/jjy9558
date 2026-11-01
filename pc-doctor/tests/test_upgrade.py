import copy
import unittest

from fakes import SNAPSHOT_RAW, sample_snapshot

from pc_doctor import upgrade
from pc_doctor.config import DEFAULT_CONFIG

CFG = copy.deepcopy(DEFAULT_CONFIG)


def titles(snap):
    return [a.area + ":" + a.title for a in upgrade.advise(snap, CFG)]


class UpgradeTests(unittest.TestCase):
    def test_bandwidth(self):
        self.assertEqual(upgrade.memory_bandwidth_gbs(sample_snapshot()), 96.0)
        one = sample_snapshot(raw={"memory": [SNAPSHOT_RAW["memory"][0]]})
        self.assertEqual(upgrade.memory_bandwidth_gbs(one), 48.0)

    def test_model_fits_in_vram(self):
        s = sample_snapshot(models={"files": [{"name": "qwen-14b", "size_gb": 9.0}], "largest_gb": 9.0})
        self.assertTrue(any("VRAM(16 GB)에 다 올라감" in t for t in titles(s)))

    def test_model_offloaded_ram_bound(self):
        s = sample_snapshot(models={"files": [{"name": "llama-70b", "size_gb": 42.5}], "largest_gb": 42.5})
        adv = upgrade.advise(s, CFG)
        llm = [a for a in adv if a.area == "LLM"][0]
        self.assertIn("RAM으로 오프로드", llm.title)
        self.assertIn("RAM-bound", llm.detail)
        self.assertIn("tok/s", llm.detail)

    def test_ram_too_small_for_model(self):
        s = sample_snapshot(models={"files": [{"name": "big", "size_gb": 120.0}], "largest_gb": 120.0})
        ram = [a for a in upgrade.advise(s, CFG) if a.area == "RAM" and "부족" in a.title]
        self.assertTrue(ram)
        self.assertIn("128 GB", ram[0].detail)

    def test_free_fixes_first(self):
        mem = copy.deepcopy(SNAPSHOT_RAW["memory"])
        for m in mem:
            m["ConfiguredClockSpeed"] = 4800
        adv = upgrade.advise(sample_snapshot(raw={"memory": mem}), CFG)
        self.assertEqual(adv[0].area, "무료")
        self.assertIn("XMP/EXPO", adv[0].detail)

    def test_no_models_gives_vram_table(self):
        self.assertTrue(any("VRAM 16 GB 기준" in t for t in titles(sample_snapshot())))

    def test_format_mentions_no_prices(self):
        self.assertIn("가격", upgrade.format_advice([])[0])


if __name__ == "__main__":
    unittest.main()
