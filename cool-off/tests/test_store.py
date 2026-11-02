import json
import os
import tempfile
import unittest
from pathlib import Path

from cool_off.core import Ledger, empty_state
from cool_off.store import Store, StoreError, home_dir
from tests.fakes import at


class HomeDirTests(unittest.TestCase):
    def test_env_overrides_default(self):
        self.assertEqual(home_dir({"COOLOFF_HOME": "/tmp/x-cool"}), Path("/tmp/x-cool"))

    def test_default_is_home_dot_cool_off(self):
        self.assertEqual(home_dir({}), Path.home() / ".cool-off")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "nested" / "data.json"

    def test_missing_file_loads_empty_state(self):
        self.assertEqual(Store(self.path).load(), empty_state())

    def test_round_trip_keeps_korean_text(self):
        ledger = Ledger()
        ledger.add_wish("에어팟 프로", 359000, at(2026, 11, 2), why="출퇴근 노캔")
        Store(self.path).save(ledger.state)
        loaded = Store(self.path).load()
        self.assertEqual(loaded["wishes"][0]["name"], "에어팟 프로")
        self.assertEqual(loaded["wishes"][0]["why"], "출퇴근 노캔")
        self.assertIn("에어팟 프로", self.path.read_text(encoding="utf-8"))

    def test_save_leaves_no_temp_files(self):
        Store(self.path).save(empty_state())
        Store(self.path).save(empty_state())
        self.assertEqual(sorted(os.listdir(self.path.parent)), ["data.json"])

    def test_missing_keys_are_filled_from_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"wishes": []}), encoding="utf-8")
        loaded = Store(self.path).load()
        self.assertEqual(loaded["subscriptions"], [])
        self.assertEqual(loaded["next_id"], 1)

    def test_corrupt_file_raises_store_error(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(StoreError):
            Store(self.path).load()

    def test_non_object_json_rejected(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaises(StoreError):
            Store(self.path).load()


if __name__ == "__main__":
    unittest.main()
