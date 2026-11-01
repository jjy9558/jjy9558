import copy
import json
import os
import tempfile
import unittest

from fakes import GB, SNAPSHOT_RAW, FakeSystem, TempHome, sample_snapshot

from pc_doctor import snapshot


class SnapshotTests(unittest.TestCase):
    def test_normalize_fields(self):
        s = sample_snapshot()
        self.assertEqual(s["cpu"]["name"], "AMD Ryzen 7 9800X3D 8-Core Processor")
        self.assertEqual(s["cpu"]["cores"], 8)
        self.assertEqual(s["memory"]["total_gb"], 64)
        self.assertEqual(s["memory"]["configured_speed"], 6000)
        self.assertEqual(s["memory"]["part_rated_speed"], 6000)
        self.assertEqual(s["memory"]["channels"], ["A", "B"])
        self.assertEqual(s["memory"]["slots"], 4)
        # 단일 GPU 객체 + nvidia-smi 병합 (WMI 4GB 상한 대신 실제 VRAM)
        self.assertEqual(len(s["gpus"]), 1)
        self.assertEqual(s["gpus"][0]["vram_gb"], 15.9)
        self.assertEqual(s["gpus"][0]["nvidia_driver"], "576.88")
        self.assertEqual(s["gpus"][0]["vram_source"], "nvidia-smi")
        c = [d for d in s["disks"] if d["drive"] == "C"][0]
        self.assertEqual(c["media_type"], "SSD")
        self.assertEqual(c["used_percent"], 60.0)
        d = [d for d in s["disks"] if d["drive"] == "D"][0]
        self.assertEqual(d["media_type"], "HDD")
        self.assertEqual(s["power_plan"]["kind"], "balanced")
        self.assertTrue(s["features"]["game_mode"])  # 값 없음 = 기본 켜짐
        self.assertTrue(s["features"]["hags"])
        self.assertFalse(s["features"]["hvci_running"])
        self.assertEqual(s["startup"]["count"], 2)
        self.assertEqual(s["pagefile"]["files"][0]["size_mb"], 8192)
        self.assertEqual(s["os"]["build"], 26100)
        self.assertEqual(s["processes"]["top_memory"][0]["name"], "llama-server")

    def test_normalize_survives_empty_and_missing(self):
        s = snapshot.normalize({}, None, None, taken_at="2026-01-01T00:00:00", hostname="x")
        self.assertIsNone(s["cpu"])
        self.assertEqual(s["gpus"], [])
        self.assertIsNone(s["power_plan"])
        self.assertIsNone(s["features"]["vbs_running"])

    def test_no_nvidia_smi_falls_back_to_wmi(self):
        s = sample_snapshot(nvidia=None)
        self.assertEqual(s["gpus"][0]["vram_source"], "wmi(4GB 상한)")

    def test_collect_save_list_load(self):
        with TempHome():
            sysm = FakeSystem()
            s = snapshot.collect(sysm, {"model_dirs": []})
            p1 = snapshot.save(s)
            p2 = snapshot.save(s)  # 같은 시각 → 이름 충돌 회피
            self.assertNotEqual(p1, p2)
            self.assertEqual(len(snapshot.list_snapshots()), 2)
            self.assertEqual(snapshot.load(p1)["memory"]["total_gb"], 64)
            self.assertIn(os.environ["PC_DOCTOR_HOME"], p1)

    def test_scan_models_groups_split_files(self):
        d = tempfile.mkdtemp()
        try:
            for name, size in (("a-00001-of-00002.gguf", 3), ("a-00002-of-00002.gguf", 2), ("b.gguf", 1), ("c.txt", 9)):
                with open(os.path.join(d, name), "wb") as f:
                    f.truncate(size * 1024 * 1024)
            m = snapshot.scan_models([d, os.path.join(d, "missing")])
            self.assertEqual([f["name"] for f in m["files"]], ["a", "b"])
            self.assertEqual(len(m["files"]), 2)
        finally:
            import shutil
            shutil.rmtree(d)


class DiffTests(unittest.TestCase):
    def test_ram_speed_drop_flags_xmp(self):
        old = sample_snapshot()
        raw = copy.deepcopy(SNAPSHOT_RAW)
        for m in raw["memory"]:
            m["ConfiguredClockSpeed"] = 4800
        raw["bios"] = {"SMBIOSBIOSVersion": "1.A30", "ReleaseDate": "2026-10-01"}
        new = sample_snapshot(raw=raw, taken_at="2026-11-02T12:00:00")
        d = snapshot.diff(old, new)
        crit = [m for sev, m in d["highlights"] if sev == "critical"]
        self.assertTrue(any("6000 → 4800" in m and "XMP/EXPO" in m for m in crit), d["highlights"])
        self.assertTrue(any("BIOS 버전 1.A20 → 1.A30" in m for _s, m in d["highlights"]))
        keys = [k for k, _o, _n in d["changes"]]
        self.assertIn("memory.configured_speed", keys)
        self.assertNotIn("taken_at", keys)

    def test_driver_update_and_disk_and_vbs(self):
        old = sample_snapshot()
        raw = copy.deepcopy(SNAPSHOT_RAW)
        raw["volumes"][0]["SizeRemaining"] = 350 * GB
        raw["device_guard"] = {"VirtualizationBasedSecurityStatus": 2, "SecurityServicesRunning": [2]}
        new = sample_snapshot(raw=raw, nvidia="NVIDIA GeForce RTX 5080, 581.15, 16303, 900, 1, 5, 8, 16\n")
        msgs = [m for _s, m in snapshot.diff(old, new)["highlights"]]
        self.assertTrue(any("576.88 → 581.15" in m for m in msgs), msgs)
        self.assertTrue(any("x16 → x8" in m for m in msgs), msgs)
        self.assertTrue(any("C: 여유 공간 50 GB 감소" in m for m in msgs), msgs)
        self.assertTrue(any("메모리 무결성(HVCI): 꺼짐 → 켜짐" in m for m in msgs), msgs)

    def test_identical_snapshots_no_changes(self):
        a = sample_snapshot()
        b = json.loads(json.dumps(sample_snapshot(taken_at="2026-12-01T00:00:00")))
        d = snapshot.diff(a, b)
        self.assertEqual(d, {"highlights": [], "changes": []})


if __name__ == "__main__":
    unittest.main()
