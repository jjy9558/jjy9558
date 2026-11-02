import json
import tempfile
import unittest
from pathlib import Path

from steam_buddy import compat
from steam_buddy.steam import SteamError
from tests.test_cli import Base


class ValidationTests(unittest.TestCase):
    def test_preset_ok(self):
        p = compat.validate_preset({"tdp_w": 10, "fps_cap": 40, "resolution": "1280x800",
                                    "fsr": "quality", "graphics_notes": "낮음"})
        self.assertEqual(p["tdp_w"], 10)

    def test_preset_bad(self):
        for bad in ({"tdp_w": 99}, {"tdp_w": True}, {"fps_cap": 5}, {"resolution": "big"},
                    {"fsr": "ultra"}, {"graphics_notes": "x" * 501}, {"extra": 1}, []):
            with self.assertRaises(ValueError, msg=str(bad)):
                compat.validate_preset(bad)

    def test_entry_bad(self):
        for bad in ({"deck": "great"}, {"anticheat": {"linux": "maybe"}}, {"anticheat": {"x": 1}}, {"zzz": 1}):
            with self.assertRaises(ValueError):
                compat.validate_entry(bad)

    def test_shipped_sample_valid_and_labeled(self):
        db = compat.CompatDB(Path(tempfile.gettempdir()) / "nonexistent-compat-xyz.json")
        self.assertTrue(db.shipped)
        for k in db.shipped:
            self.assertTrue(db.get(k)["example"])
        self.assertIn("예시", json.loads(compat.SHIPPED.read_text(encoding="utf-8"))["_comment"])

    def test_parse_preset_doc_rejects(self):
        for text in ("not json", "[]", '{"steam_buddy_preset": 2}',
                     '{"steam_buddy_preset": 1, "appid": "x", "preset": {}}',
                     '{"steam_buddy_preset": 1, "appid": 5}',
                     '{"steam_buddy_preset": 1, "appid": 5, "preset": {"tdp_w": 1}}',
                     '{"steam_buddy_preset": 1, "appid": 5, "preset": {}, "evil": 1}'):
            with self.assertRaises(ValueError, msg=text):
                compat.parse_preset_doc(text)


class CompatCliTests(Base):
    def user_file(self):
        return self.path.parent / "compat.json"

    def test_check_without_info_and_existing_fields_intact(self):
        self.run_cmd("check", "Hades")
        self.assertIn("스팀덱    정보 없음", self.text())
        self.assertIn("₩26,000", self.text())

    def test_set_then_check(self):
        self.run_cmd("compat", "set", "10", "--deck", "playable", "--anticheat", "EasyAntiCheat",
                     "--ac-linux", "yes", "--ac-deck", "unknown", "--tdp", "9", "--fps", "40",
                     "--resolution", "1280x800", "--fsr", "balanced", "--gfx", "그림자 낮음")
        self.assertTrue(self.user_file().exists())
        self.run_cmd("check", "Hades")
        t = self.text()
        self.assertIn("플레이 가능", t)
        self.assertIn("EasyAntiCheat / 리눅스 지원 / 덱 확인 필요", t)
        self.assertIn("TDP 9W, 40fps 제한, 1280x800, FSR balanced, 그림자 낮음", t)

    def test_check_warns(self):
        self.run_cmd("compat", "set", "10", "--deck", "unsupported", "--anticheat", "BattlEye", "--ac-linux", "no")
        self.run_cmd("check", "10")
        self.assertIn("스팀덱 미지원", self.text())
        self.assertIn("안티치트가 리눅스 미지원", self.text())

    def test_user_overrides_shipped_and_data_json_untouched(self):
        sid = next(iter(compat.CompatDB(self.user_file()).shipped))
        self.run_cmd("compat", "show", sid)
        self.assertIn("예시 데이터", self.text())
        self.run_cmd("compat", "set", sid, "--deck", "verified")
        self.run_cmd("compat", "show", sid)
        self.assertIn("인증됨", self.text())
        self.assertNotIn("예시 데이터", self.text())
        self.assertFalse(self.path.exists())  # data.json은 만들지 않음

    def test_show_table_marks_examples(self):
        self.run_cmd("compat", "show")
        self.assertIn("예시 데이터예요", self.text())
        self.assertIn("appid", self.text())

    def test_set_requires_field_and_validates(self):
        with self.assertRaises(ValueError):
            self.run_cmd("compat", "set", "10")
        with self.assertRaises(ValueError):
            self.run_cmd("compat", "set", "10", "--tdp", "100")
        self.assertFalse(self.user_file().exists())

    def test_set_by_local_name(self):
        self.run_cmd("buy", "10")
        self.run_cmd("compat", "set", "Hades", "--note", "메모")
        self.assertIn("메모", json.loads(self.user_file().read_text(encoding="utf-8"))["games"]["10"]["note"])

    def test_corrupt_user_file(self):
        self.user_file().write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_cmd("check", "Hades")


class PresetCliTests(Base):
    def test_export_import_roundtrip(self):
        self.run_cmd("compat", "set", "10", "--tdp", "12", "--fps", "60", "--fsr", "off")
        out = Path(self.tmp.name) / "p.json"
        self.run_cmd("preset", "export", "10", "--out", str(out))
        doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(doc["steam_buddy_preset"], 1)
        self.assertEqual(doc["preset"], {"tdp_w": 12, "fps_cap": 60, "fsr": "off"})
        self.run_cmd("preset", "import", "10", str(out))  # 같은 appid 재가져오기
        # 다른 appid로는 막힘, --force면 허용
        with self.assertRaises(ValueError):
            self.run_cmd("preset", "import", "11", str(out))
        self.run_cmd("preset", "import", "11", str(out), "--force")
        self.run_cmd("compat", "show", "11")
        self.assertIn("TDP 12W", self.text())

    def test_export_stdout(self):
        self.run_cmd("compat", "set", "10", "--tdp", "8")
        self.run_cmd("preset", "export", "10")
        self.assertEqual(json.loads(self.text())["appid"], 10)

    def test_export_without_preset(self):
        with self.assertRaises(ValueError):
            self.run_cmd("preset", "export", "10")

    def test_import_invalid_does_not_write(self):
        f = Path(self.tmp.name) / "bad.json"
        f.write_text(json.dumps({"steam_buddy_preset": 1, "appid": 10, "preset": {"tdp_w": 500}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_cmd("preset", "import", "10", str(f))
        self.assertFalse((self.path.parent / "compat.json").exists())

    def test_import_missing_file(self):
        with self.assertRaises(ValueError):
            self.run_cmd("preset", "import", "10", str(Path(self.tmp.name) / "none.json"))

    def test_unknown_name(self):
        with self.assertRaises(SteamError):
            self.run_cmd("preset", "export", "없는게임")


if __name__ == "__main__":
    unittest.main()
