import json
import tempfile
import unittest
from pathlib import Path

from hpatch.backup import BackupStore
from hpatch.engines import ApplyOptions, detect_engine
from hpatch.tm import TranslationMemory
from hpatch.translate import Translator
from hpatch.units import source_hash
from helpers import FakeLLM, make_rpgmaker, snapshot


class BackupTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    def test_write_backup_restore_and_created_files(self):
        (self.root / "a.txt").write_bytes(b"orig\r\n\x00\xff")
        before = snapshot(self.root)
        s = BackupStore(self.root)
        s.write("a.txt", b"patched1")
        s.write("a.txt", b"patched2")  # 두 번 써도 원본 백업 유지
        s.write("sub/new.txt", b"created")
        self.assertEqual((self.root / ".hpatch_backup/files/a.txt").read_bytes(), before["a.txt"])
        self.assertEqual(BackupStore(self.root).original_bytes("a.txt"), before["a.txt"])  # 새 인스턴스(매니페스트 재로드)
        self.assertIsNone(BackupStore(self.root).original_bytes("sub/new.txt"))
        r = BackupStore(self.root).restore()
        self.assertEqual(sorted(r["restored"]), ["a.txt"])
        self.assertEqual(r["removed"], ["sub/new.txt"])
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / "sub").exists())

    def test_apply_twice_keeps_pristine_backup(self):
        make_rpgmaker(self.root)
        before = snapshot(self.root)
        eng = detect_engine(self.root)
        eng.apply(BackupStore(self.root), {"Potion": "물약"}, ApplyOptions())
        eng.apply(BackupStore(self.root), {"Potion": "포션"}, ApplyOptions())
        items = json.loads((self.root / "www/data/Items.json").read_text("utf-8"))
        self.assertEqual(items[1]["name"], "포션")
        BackupStore(self.root).restore()
        self.assertEqual(snapshot(self.root), before)

    def test_restore_with_nothing_is_noop(self):
        (self.root / "x").write_text("1")
        r = BackupStore(self.root).restore()
        self.assertEqual(r["restored"], [])
        self.assertEqual((self.root / "x").read_text(), "1")


class MemoryReuseOnUpdateTests(unittest.TestCase):
    def test_game_update_translates_only_new_strings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            make_rpgmaker(root)
            eng = detect_engine(root)
            tm = TranslationMemory(root / "tm.sqlite")
            llm1 = FakeLLM()
            res = eng.extract(BackupStore(root))
            Translator(llm1, tm, eng.masker(), log=lambda s: None, batch_size=100).run(res.units)
            n = tm.count()
            # 업데이트: 아이템 하나 추가
            p = root / "www/data/Items.json"
            items = json.loads(p.read_text("utf-8"))
            items.append({"id": 2, "name": "Hi-Potion", "description": "Restores 500 HP.", "price": 1})
            p.write_text(json.dumps(items), encoding="utf-8")
            llm2 = FakeLLM()
            res = eng.extract(BackupStore(root))
            rep = Translator(llm2, tm, eng.masker(), log=lambda s: None, batch_size=100).run(res.units)
            self.assertEqual(rep.translated, 2)
            self.assertEqual(len(llm2.calls), 1)
            self.assertEqual(tm.count(), n + 2)
            tm.close()

    def test_tm_keyed_by_source_hash_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "tm.sqlite"
            tm = TranslationMemory(path)
            tm.put("Hello", "안녕")
            tm.close()
            tm2 = TranslationMemory(path)
            self.assertEqual(tm2.get("Hello"), "안녕")
            row = tm2.db.execute("SELECT hash FROM tm").fetchone()[0]
            self.assertEqual(row, source_hash("Hello"))
            self.assertFalse(tm2.put("Hello", "다른", origin="llm"))  # 기본은 덮어쓰지 않음
            self.assertTrue(tm2.put("Hello", "다른", origin="llm", overwrite=True))
            tm2.close()


if __name__ == "__main__":
    unittest.main()
