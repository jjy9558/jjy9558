import json
import tempfile
import unittest
from pathlib import Path

from hpatch.backup import BackupStore
from hpatch.engines import ApplyOptions, detect_engine
from hpatch.engines.rpgmaker import RPGMakerEngine, patch_gamefont_css
from hpatch.tm import TranslationMemory
from hpatch.translate import Translator
from helpers import FakeLLM, make_rpgmaker, snapshot


def sources(res, kind=None):
    return [u.source for u in res.units if kind is None or u.kind == kind]


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    def extract(self, mz):
        make_rpgmaker(self.root, mz=mz)
        eng = detect_engine(self.root)
        return eng, eng.extract(BackupStore(self.root))

    def test_detect_variants(self):
        make_rpgmaker(self.root, mz=False)
        eng = detect_engine(self.root)
        self.assertIsInstance(eng, RPGMakerEngine)
        self.assertEqual((eng.variant, eng.base_rel), ("mv", "www"))
        with tempfile.TemporaryDirectory() as td:
            make_rpgmaker(Path(td), mz=True)
            eng2 = detect_engine(td)
            self.assertEqual((eng2.variant, eng2.base_rel), ("mz", ""))
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(detect_engine(td))

    def test_dialogue_blocks_and_event_commands_mv(self):
        _, res = self.extract(mz=False)
        s = sources(res)
        # 401: 연속된 줄이 한 묶음, 제어코드 그대로
        self.assertIn("Hello \\C[2]traveler\\C[0], welcome to \\N[1]'s town!\nYou have \\V[3] gold. \\{Big\\} \\. \\| \\! \\> \\< \\^", s)
        self.assertIn("Great!", s)
        # 102 선택지 + 402 분기
        self.assertEqual([u.source for u in res.units if u.kind == "choice"][:3], ["Yes", "No thanks", "100"])
        self.assertEqual(sources(res, "choice").count("Yes"), 2)  # 102 + 402
        self.assertEqual(sources(res, "choice").count("No thanks"), 2)
        # 405 스크롤 텍스트
        self.assertEqual(sources(res, "scroll_text"), ["The story begins\nin a quiet village."])
        # MV 에는 101 화자 이름이 없다
        self.assertEqual([u for u in res.units if u.speaker], [])
        # 스크립트/주석/미지원 코드는 추출하지 않음
        self.assertFalse(any("Do not" in x for x in s))
        self.assertFalse(any("Editor" in x for x in s))
        # 공용 이벤트 / 전투 이벤트
        self.assertIn("Common event text.", s)
        self.assertIn("The slime wobbles.", s)

    def test_mz_speaker_name_params4(self):
        _, res = self.extract(mz=True)
        names = [u for u in res.units if u.kind == "name" and u.path[-2:] == ["parameters", 4]]
        self.assertEqual([u.source for u in names], ["Alice"])
        d = [u for u in res.units if u.kind == "dialogue" and u.source.startswith("Hello")][0]
        self.assertEqual(d.speaker, "Alice")
        self.assertEqual([u.prev for u in res.units if u.source == "Great!"], [d.source])

    def test_database_and_system_fields(self):
        _, res = self.extract(mz=False)
        s = sources(res)
        for expected in ["Alice", "Brave", "A young hero.\nLikes tea.", "Hero", "Potion", "Restores 100 HP.",
                         "Iron Sword", "A plain sword.", "Leather Armor", "Light armor.", "Fireball", "Burns the enemy.",
                         "%1 casts Fireball!", "Poison", "%1 is poisoned!", "%1 is cured.", "Slime",
                         "Test Quest", "G", "General Armor", "Physical", "Fire", "Sword", "Magic", "Weapon",
                         "Level", "Fight", "Attack", "Max HP", "There was no effect on %1!", "%1 is now %2 %3!",
                         "Possession", "Starting Town"]:
            self.assertIn(expected, s, expected)
        # 빈 문자열 / null / 에디터 전용 항목은 제외
        self.assertNotIn("", s)
        self.assertNotIn(None, s)
        self.assertFalse(any(x in s for x in ("Editor Only Switch", "Editor Only Var", "keep me", "<tag>", "Common 1")))
        self.assertEqual(sources(res, "title"), ["Starting Town", "Test Quest"])

    def test_invalid_json_is_skipped_with_note(self):
        make_rpgmaker(self.root)
        (self.root / "www/data/Items.json").write_text("{not json", encoding="utf-8")
        res = detect_engine(self.root).extract(BackupStore(self.root))
        self.assertTrue(any("Items.json" in n for n in res.notes))
        self.assertIn("Alice", sources(res))


def run_pipeline(root, tr_fn=None, mz=False, **opts):
    eng = detect_engine(root)
    store = BackupStore(root)
    res = eng.extract(store)
    tm = TranslationMemory(":memory:")
    Translator(FakeLLM(), tm, eng.masker(), finalize=eng.finalize, log=lambda s: None, batch_size=50).run(res.units)
    rep = eng.apply(store, tm.as_dict(), ApplyOptions(**opts))
    return eng, store, tm, rep


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    def test_apply_restore_is_byte_exact_mv_and_mz(self):
        for mz in (False, True):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                make_rpgmaker(root, mz=mz)
                before = snapshot(root)
                eng, store, tm, rep = run_pipeline(root)
                self.assertTrue(rep.written)
                after = snapshot(root)
                self.assertNotEqual(before, {k: v for k, v in after.items() if not k.startswith(".hpatch_backup")})
                # 백업 폴더에 원본이 그대로
                for rel in rep.written:
                    self.assertEqual((root / ".hpatch_backup" / "files" / rel).read_bytes(), before[rel])
                r = BackupStore(root).restore()
                self.assertEqual(snapshot(root), before)
                self.assertFalse((root / ".hpatch_backup").exists())
                self.assertTrue(r["restored"])

    def test_output_json_valid_and_non_text_unchanged(self):
        make_rpgmaker(self.root)
        orig_map = json.loads((self.root / "www/data/Map001.json").read_text("utf-8"))
        orig_items = json.loads((self.root / "www/data/Items.json").read_text("utf-8"))
        run_pipeline(self.root)
        new_map = json.loads((self.root / "www/data/Map001.json").read_text("utf-8"))
        new_items = json.loads((self.root / "www/data/Items.json").read_text("utf-8"))
        # 아이템: 문자열 필드 두 개만 달라짐
        self.assertEqual(new_items[1]["name"], "KO:Potion")
        self.assertEqual(new_items[1]["description"], "KO:Restores 100 HP.")
        for k in ("id", "price", "note"):
            self.assertEqual(new_items[1][k], orig_items[1][k])
        # 맵: 이벤트 명령 코드/indent/비텍스트 파라미터 보존
        ol = orig_map["events"][1]["pages"][0]["list"]
        nl = new_map["events"][1]["pages"][0]["list"]
        self.assertEqual(len(ol), len(nl))
        for o, n in zip(ol, nl):
            self.assertEqual((o["code"], o["indent"]), (n["code"], n["indent"]))
            if o["code"] not in (401, 405, 102, 402):
                self.assertEqual(o, n)
        self.assertEqual(new_map["data"], orig_map["data"])
        self.assertEqual(new_map["displayName"], "KO:Starting Town")
        self.assertEqual(nl[3]["parameters"][1:], ol[3]["parameters"][1:])
        self.assertEqual(nl[3]["parameters"][0], ["KO:Yes", "KO:No thanks", "100"])
        # 스크립트 명령/주석은 그대로
        self.assertEqual(nl[14], ol[14])
        self.assertEqual(nl[15], ol[15])

    def test_control_codes_preserved_in_output(self):
        make_rpgmaker(self.root)
        run_pipeline(self.root)
        new_map = json.loads((self.root / "www/data/Map001.json").read_text("utf-8"))
        texts = [c["parameters"][0] for c in new_map["events"][1]["pages"][0]["list"] if c["code"] == 401]
        self.assertEqual(texts[0], "KO:Hello \\C[2]traveler\\C[0], welcome to \\N[1]'s town!")
        self.assertEqual(texts[1], "You have \\V[3] gold. \\{Big\\} \\. \\| \\! \\> \\< \\^")  # 두 번째 줄은 첫 줄과 한 묶음 -> 다시 분리
        sys_ = json.loads((self.root / "www/data/System.json").read_text("utf-8"))
        self.assertEqual(sys_["terms"]["messages"]["levelUp"], "KO:%1 is now %2 %3!")
        self.assertEqual(sys_["terms"]["commands"][2], None)
        self.assertEqual(sys_["switches"], ["", "Editor Only Switch"])

    def test_line_count_change_resizes_block(self):
        make_rpgmaker(self.root)
        eng = detect_engine(self.root)
        store = BackupStore(self.root)
        res = eng.extract(store)
        block = [u for u in res.units if u.kind == "dialogue" and u.source.startswith("Hello")][0]
        tr = {block.source: "첫줄 \\C[2]\\C[0]\n둘째줄\n셋째줄"}
        eng.apply(store, tr, ApplyOptions())
        new_map = json.loads((self.root / "www/data/Map001.json").read_text("utf-8"))
        lst = new_map["events"][1]["pages"][0]["list"]
        self.assertEqual([c["parameters"][0] for c in lst[1:4]], ["첫줄 \\C[2]\\C[0]", "둘째줄", "셋째줄"])
        self.assertEqual(lst[4]["code"], 102)  # 뒤 명령은 밀려 있음
        # 줄이 줄어드는 경우
        BackupStore(self.root).restore()
        store = BackupStore(self.root)
        eng.apply(store, {block.source: "한 줄만"}, ApplyOptions())
        lst = json.loads((self.root / "www/data/Map001.json").read_text("utf-8"))["events"][1]["pages"][0]["list"]
        self.assertEqual([c["code"] for c in lst[:3]], [101, 401, 102])

    def test_untranslated_stays_original(self):
        make_rpgmaker(self.root)
        eng = detect_engine(self.root)
        store = BackupStore(self.root)
        rep = eng.apply(store, {"Potion": "물약"}, ApplyOptions())
        items = json.loads((self.root / "www/data/Items.json").read_text("utf-8"))
        self.assertEqual(items[1]["name"], "물약")
        self.assertEqual(items[1]["description"], "Restores 100 HP.")
        self.assertEqual(rep.written, ["www/data/Items.json"])
        self.assertFalse((self.root / ".hpatch_backup/files/www/data/Map001.json").exists())

    def test_bom_is_preserved(self):
        make_rpgmaker(self.root)
        p = self.root / "www/data/Items.json"
        p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
        run_pipeline(self.root)
        self.assertTrue(p.read_bytes().startswith(b"\xef\xbb\xbf"))
        json.loads(p.read_text("utf-8-sig"))

    def test_reextract_after_apply_reads_originals(self):
        make_rpgmaker(self.root)
        eng, store, tm, rep = run_pipeline(self.root)
        res2 = eng.extract(BackupStore(self.root))
        self.assertIn("Potion", sources(res2))
        self.assertNotIn("KO:Potion", sources(res2))

    def test_game_update_after_patch_refreshes_backup(self):
        make_rpgmaker(self.root)
        run_pipeline(self.root)
        # 게임 업데이트: 파일이 새 원본으로 교체됨
        p = self.root / "www/data/Items.json"
        new_orig = json.dumps([None, {"id": 1, "name": "Elixir", "description": "New item.", "price": 1}], separators=(",", ":"))
        p.write_text(new_orig, encoding="utf-8")
        eng = detect_engine(self.root)
        res = eng.extract(BackupStore(self.root))
        self.assertIn("Elixir", sources(res))
        eng.apply(BackupStore(self.root), {"Elixir": "엘릭서"}, ApplyOptions())
        BackupStore(self.root).restore()
        self.assertEqual(p.read_text("utf-8"), new_orig)

    def test_restore_skips_files_changed_after_patch_unless_forced(self):
        make_rpgmaker(self.root)
        run_pipeline(self.root)
        p = self.root / "www/data/Items.json"
        p.write_text("[null]", encoding="utf-8")
        r = BackupStore(self.root).restore()
        self.assertIn("www/data/Items.json", r["skipped"])
        self.assertEqual(p.read_text(), "[null]")
        r = BackupStore(self.root).restore(force=True)
        self.assertIn("www/data/Items.json", r["restored"])
        self.assertIn("Potion", p.read_text("utf-8"))


class FontTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name) / "game"
        self.fontdir = Path(self._td.name) / "fonts_in"
        self.fontdir.mkdir()
        self.font = self.fontdir / "MyKorean.ttf"
        self.font.write_bytes(b"fake-ttf")

    def test_css_patch(self):
        css = '@font-face {\n    font-family: GameFont;\n    src: url("mplus-1m-regular.ttf");\n}\n'
        out = patch_gamefont_css(css, "MyKorean.ttf")
        self.assertIn('url("MyKorean.ttf")', out)
        self.assertNotIn("mplus", out)
        self.assertIn("font-family: GameFont", out)
        self.assertIn('url("A.ttf")', patch_gamefont_css("", "A.ttf"))
        other = "@font-face { font-family: Other; src: url('o.ttf'); }\n"
        self.assertIn("o.ttf", patch_gamefont_css(other, "A.ttf"))

    def test_mv_font_injection_and_restore(self):
        make_rpgmaker(self.root, mz=False)
        before = snapshot(self.root)
        run_pipeline(self.root, font=str(self.font), locale_ko=True)
        self.assertEqual((self.root / "www/fonts/MyKorean.ttf").read_bytes(), b"fake-ttf")
        css = (self.root / "www/fonts/gamefont.css").read_text()
        self.assertIn('url("MyKorean.ttf")', css)
        sysj = json.loads((self.root / "www/data/System.json").read_text("utf-8"))
        self.assertEqual(sysj["locale"], "ko_KR")
        BackupStore(self.root).restore()
        self.assertEqual(snapshot(self.root), before)

    def test_mz_font_injection_via_system_json(self):
        make_rpgmaker(self.root, mz=True)
        before = snapshot(self.root)
        run_pipeline(self.root, font=str(self.font))
        sysj = json.loads((self.root / "data/System.json").read_text("utf-8"))
        self.assertEqual(sysj["advanced"]["mainFontFilename"], "MyKorean.ttf")
        self.assertEqual(sysj["advanced"]["numberFontFilename"], "mplus-2p-bold-sub.ttf")
        self.assertEqual(sysj["locale"], "ja_JP")
        self.assertEqual((self.root / "fonts/MyKorean.ttf").read_bytes(), b"fake-ttf")
        BackupStore(self.root).restore()
        self.assertEqual(snapshot(self.root), before)

    def test_bad_font_path_is_reported(self):
        make_rpgmaker(self.root, mz=False)
        eng = detect_engine(self.root)
        rep = eng.apply(BackupStore(self.root), {}, ApplyOptions(font=str(self.fontdir / "missing.ttf")))
        self.assertTrue(any("폰트" in n for n in rep.notes))
        self.assertEqual(rep.written, [])


if __name__ == "__main__":
    unittest.main()
