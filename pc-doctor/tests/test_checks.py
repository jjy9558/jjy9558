import copy
import unittest

from fakes import GB, POWERCFG_SAVER_EN, SNAPSHOT_RAW, sample_snapshot

from pc_doctor import checks
from pc_doctor.config import DEFAULT_CONFIG

CFG = copy.deepcopy(DEFAULT_CONFIG)


def ids(snap, cfg=CFG):
    return {f.id: f for f in checks.run_checks(snap, cfg)}


def raw(**changes):
    r = copy.deepcopy(SNAPSHOT_RAW)
    r.update(changes)
    return r


class CheckRuleTests(unittest.TestCase):
    def test_healthy_baseline_has_no_warnings(self):
        found = checks.run_checks(sample_snapshot(), CFG)
        self.assertEqual([f for f in found if f.severity in ("warn", "critical")], [])

    def test_every_rule_has_id_and_is_registered(self):
        rule_ids = [r.rule_id for r in checks.RULES]
        self.assertEqual(len(rule_ids), len(set(rule_ids)))
        for expected in ("ram_below_rated", "ram_single_channel", "disk_almost_full", "vbs_on", "power_plan_saver",
                         "gpu_driver_old", "startup_too_many", "pagefile_off", "hags_off", "game_mode_off"):
            self.assertIn(expected, rule_ids)

    def test_ram_below_rated_part_number(self):
        mem = copy.deepcopy(SNAPSHOT_RAW["memory"])
        for m in mem:
            m["ConfiguredClockSpeed"] = 4800
        f = ids(sample_snapshot(raw={"memory": mem}))["ram_below_rated"]
        self.assertEqual(f.severity, "warn")
        self.assertIn("4800 / 6000", f.title)
        self.assertIn("EXPO", f.fix)

    def test_ram_below_rated_uses_config_value(self):
        cfg = dict(CFG, expected_ram_mts=6400)
        self.assertIn("ram_below_rated", ids(sample_snapshot(), cfg))
        self.assertNotIn("ram_below_rated", ids(sample_snapshot()))

    def test_single_channel_one_stick(self):
        f = ids(sample_snapshot(raw={"memory": [SNAPSHOT_RAW["memory"][0]]}))["ram_single_channel"]
        self.assertIn("단일 채널", f.title)

    def test_single_channel_same_channel_slots(self):
        mem = copy.deepcopy(SNAPSHOT_RAW["memory"])
        mem[1]["BankLabel"] = "P0 CHANNEL A"
        mem[1]["DeviceLocator"] = "DIMM 2"
        self.assertIn("ram_single_channel", ids(sample_snapshot(raw={"memory": mem})))

    def test_mixed_kits(self):
        mem = copy.deepcopy(SNAPSHOT_RAW["memory"])
        mem[1]["PartNumber"] = "CMK32GX5M2B5600C36"
        self.assertEqual(ids(sample_snapshot(raw={"memory": mem}))["ram_mixed_kits"].severity, "info")

    def test_disk_90_and_95(self):
        vols = copy.deepcopy(SNAPSHOT_RAW["volumes"])
        vols[0]["SizeRemaining"] = 80 * GB   # 92%
        vols[1]["SizeRemaining"] = 100 * GB  # 97%
        found = [f for f in checks.run_checks(sample_snapshot(raw={"volumes": vols}), CFG) if f.id == "disk_almost_full"]
        self.assertEqual(sorted(f.severity for f in found), ["critical", "warn"])

    def test_system_disk_hdd(self):
        vols = copy.deepcopy(SNAPSHOT_RAW["volumes"])
        vols[0]["DiskNumber"] = 1
        self.assertIn("system_disk_hdd", ids(sample_snapshot(raw={"volumes": vols})))

    def test_vbs_on(self):
        f = ids(sample_snapshot(raw={"device_guard": {"VirtualizationBasedSecurityStatus": 2, "SecurityServicesRunning": [1, 2]}}))["vbs_on"]
        self.assertIn("메모리 무결성", f.title)
        self.assertIn("절대 바꾸지 않습니다", f.detail)

    def test_power_saver(self):
        self.assertIn("power_plan_saver", ids(sample_snapshot(powercfg=POWERCFG_SAVER_EN)))
        self.assertNotIn("power_plan_saver", ids(sample_snapshot()))

    def test_old_gpu_driver(self):
        gpu = dict(SNAPSHOT_RAW["gpu"], DriverDate="2025-01-01")
        f = ids(sample_snapshot(raw={"gpu": gpu}))["gpu_driver_old"]
        self.assertEqual(f.severity, "warn")  # 669일 > 180*2
        gpu = dict(SNAPSHOT_RAW["gpu"], DriverDate="2026-09-01")
        self.assertNotIn("gpu_driver_old", ids(sample_snapshot(raw={"gpu": gpu})))

    def test_pcie_width(self):
        self.assertIn("gpu_pcie_width", ids(sample_snapshot(nvidia="NVIDIA GeForce RTX 5080, 576.88, 16303, 1200, 1, 5, 8, 16")))

    def test_startup_too_many(self):
        startup = [{"Name": "App%d" % i, "Location": "Run"} for i in range(20)]
        self.assertIn("startup_too_many", ids(sample_snapshot(raw={"startup": startup})))

    def test_pagefile_off_with_big_models(self):
        comp = dict(SNAPSHOT_RAW["computer"], AutomaticManagedPagefile=False)
        s = sample_snapshot(raw={"computer": comp, "pagefile": []},
                            models={"files": [{"name": "llama-70b-q4", "size_gb": 42.5}], "largest_gb": 42.5})
        self.assertEqual(ids(s)["pagefile_off"].severity, "warn")
        s = sample_snapshot(raw={"computer": comp, "pagefile": []})
        self.assertEqual(ids(s)["pagefile_off"].severity, "info")
        # 정보 누락(None)은 꺼짐으로 오인하지 않음
        self.assertNotIn("pagefile_off", ids(sample_snapshot(raw={"computer": {}, "pagefile": []})))

    def test_hags_off_rtx50_is_warn(self):
        self.assertEqual(ids(sample_snapshot(raw={"hags": 1}))["hags_off"].severity, "warn")

    def test_game_mode_off(self):
        self.assertIn("game_mode_off", ids(sample_snapshot(raw={"game_mode": 0})))

    def test_low_free_ram(self):
        os_ = dict(SNAPSHOT_RAW["os"], FreePhysicalMemory=2_000_000)
        f = ids(sample_snapshot(raw={"os": os_}))["low_free_ram"]
        self.assertIn("llama-server", f.detail)

    def test_broken_rule_does_not_crash(self):
        def boom(snap, cfg):
            raise RuntimeError("x")
        boom.rule_id = "boom"
        found = checks.run_checks(sample_snapshot(), CFG, rules=[boom])
        self.assertEqual(found[0].id, "boom")

    def test_format(self):
        lines = checks.format_findings(checks.run_checks(sample_snapshot(raw={"hags": 1}), CFG))
        self.assertTrue(any(l.startswith("🟡 주의 [hags_off]") for l in lines))
        self.assertEqual(checks.format_findings([]), ["✅ 발견된 문제가 없습니다."])


if __name__ == "__main__":
    unittest.main()
