import unittest

from fakes import NVIDIA_SMI, POWERCFG_BALANCED_KO, POWERCFG_SAVER_EN  # noqa: F401

from pc_doctor import parsers
from pc_doctor.system import parse_ps_json, split_hive


class ParserTests(unittest.TestCase):
    def test_powercfg_korean_and_english(self):
        p = parsers.parse_powercfg_active(POWERCFG_BALANCED_KO)
        self.assertEqual(p["guid"], "381b4222-f2b5-4f9c-a9d4-e6b4e9ca5e9a")
        self.assertEqual(p["name"], "균형 조정")
        self.assertEqual(p["kind"], "balanced")
        self.assertEqual(parsers.parse_powercfg_active(POWERCFG_SAVER_EN)["kind"], "saver")
        self.assertIsNone(parsers.parse_powercfg_active(""))
        self.assertIsNone(parsers.parse_powercfg_active("garbage"))

    def test_nvidia_smi(self):
        g = parsers.parse_nvidia_smi(NVIDIA_SMI)[0]
        self.assertEqual(g["name"], "NVIDIA GeForce RTX 5080")
        self.assertEqual(g["driver_version"], "576.88")
        self.assertEqual(g["vram_mb"], 16303)
        self.assertEqual(g["pcie_width_current"], 16)
        na = parsers.parse_nvidia_smi("GPU X, 1.0, 8192, [N/A], [N/A], [N/A], [N/A], [N/A]")[0]
        self.assertIsNone(na["pcie_width_current"])
        self.assertEqual(parsers.parse_nvidia_smi(None), [])

    def test_ps_json_single_object_and_garbage(self):
        self.assertEqual(parsers.as_list(parse_ps_json('{"a":1}')), [{"a": 1}])
        self.assertEqual(parsers.as_list(parse_ps_json('﻿[{"a":1},{"a":2}]')), [{"a": 1}, {"a": 2}])
        self.assertIsNone(parse_ps_json(""))
        self.assertIsNone(parse_ps_json("오류: 액세스 거부"))
        self.assertEqual(parsers.as_list(None), [])

    def test_tasklist_csv(self):
        raw = '"System Idle Process","0","Services","0","8 K"\r\n"Cyberpunk2077.exe","4242","Console","1","9,000,000 K"\r\n'
        self.assertEqual(parsers.parse_tasklist(raw), {"cyberpunk2077.exe"})
        self.assertEqual(parsers.parse_tasklist("정보: 실행 중인 작업이 없습니다."), set())

    def test_sc_state_localized(self):
        self.assertEqual(parsers.parse_sc_state("        상태              : 4  RUNNING\r\n"), "RUNNING")
        self.assertEqual(parsers.parse_sc_state("        STATE              : 1  STOPPED "), "STOPPED")
        self.assertIsNone(parsers.parse_sc_state(None))

    def test_ram_part_number_guess(self):
        self.assertEqual(parsers.guess_rated_speed_from_part("F5-6000J3038F16G"), 6000)
        self.assertEqual(parsers.guess_rated_speed_from_part("CMK32GX5M2B6000C36"), 6000)
        self.assertEqual(parsers.guess_rated_speed_from_part("CMK64GX5M2B5600C40"), 5600)
        self.assertIsNone(parsers.guess_rated_speed_from_part("M425R1GB4BB0-CQKOL"))
        self.assertIsNone(parsers.guess_rated_speed_from_part(""))

    def test_channel_guess(self):
        self.assertEqual(parsers.guess_channel("P0 CHANNEL A", "DIMM 1"), "A")
        self.assertEqual(parsers.guess_channel("BANK 0", "Controller1-ChannelB-DIMM0"), "B")
        self.assertEqual(parsers.guess_channel("BANK 2", "DIMM_B2"), "B")
        self.assertIsNone(parsers.guess_channel("BANK 0", "DIMM 0"))

    def test_split_hive(self):
        self.assertEqual(split_hive(r"HKEY_CURRENT_USER\Software\X"), ("HKCU", r"Software\X"))
        with self.assertRaises(ValueError):
            split_hive(r"HKCR\foo")


if __name__ == "__main__":
    unittest.main()
