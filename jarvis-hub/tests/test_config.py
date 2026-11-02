import copy
import json
import os
import unittest

from helpers import BASE
from jarvis.config import ConfigError, build_hub_config, build_worker_config, load_client_settings, load_worker_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigTest(unittest.TestCase):
    def test_examples_valid(self):
        with open(os.path.join(ROOT, "hub.example.json"), encoding="utf-8") as f:
            build_hub_config(json.load(f))
        for name in ("worker.main.example.json", "worker.minipc.example.json"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                build_worker_config(json.load(f))

    def test_hub_requires_devices_and_long_unique_tokens(self):
        with self.assertRaises(ConfigError):
            build_hub_config({})
        with self.assertRaises(ConfigError):
            build_hub_config({"devices": {"a": {"token": "short"}}})
        with self.assertRaises(ConfigError):
            build_hub_config({"devices": {"a": {"token": "x" * 20}, "b": {"token": "x" * 20}}})
        with self.assertRaises(ConfigError):
            build_hub_config({"devices": {"hub": {"token": "x" * 20}}})
        with self.assertRaises(ConfigError):
            build_hub_config({"devices": {"Bad Id": {"token": "x" * 20}}})

    def test_wake_mac_validated(self):
        cfg = copy.deepcopy(BASE)
        cfg["wake"]["mac"] = "zz"
        with self.assertRaises(ConfigError):
            build_hub_config(cfg)

    def test_device_defaults(self):
        cfg = build_hub_config({"devices": {"a": {"token": "x" * 20}}})
        d = cfg["devices"]["a"]
        self.assertEqual((d["worker"], d["approve"], d["trusted"], d["submit"]), (False, False, False, ["*"]))
        self.assertEqual(cfg["server"]["host"], "127.0.0.1")

    def test_worker_overrides_and_env(self):
        cfg = load_worker_config(None, {"device": "main", "caps": "gpu, llm,windows"}, environ={"JARVIS_TOKEN": "t" * 20})
        self.assertEqual(cfg["caps"], ["gpu", "llm", "windows"])
        self.assertEqual(cfg["token"], "t" * 20)
        with self.assertRaises(ConfigError):
            load_worker_config(None, {"device": "main"}, environ={})
        with self.assertRaises(ConfigError):
            load_worker_config(None, {"device": "main", "token": "x", "caps": ["GPU"]}, environ={})

    def test_client_settings_precedence(self):
        s = load_client_settings(None, hub="http://h:1/", environ={"JARVIS_DEVICE": "s21", "JARVIS_TOKEN": "t"})
        self.assertEqual(s, {"hub": "http://h:1", "device": "s21", "token": "t"})


if __name__ == "__main__":
    unittest.main()
