import re
import unittest

from phonehome import config as C
from phonehome import pages


class PageTests(unittest.TestCase):
    def all_pages(self):
        snap = {"cards": [{"key": "0" * 12, "kind": "x", "title": "</script><img src=x onerror=alert(1)>",
                           "detail": "", "due": "", "actions": [], "stamp": ""}],
                "cards_status": "ok", "approvals": [], "errors": {}, "actions": {}}
        tiles = [{"name": "a", "title": "</script>", "icon": "🔗", "desc": "", "link": "/go/a", "quick": None}]
        return [pages.login_page(), pages.launcher_page(tiles), pages.desk_page(snap)]

    def test_self_contained_and_mobile(self):
        for html in self.all_pages():
            self.assertNotRegex(html, r"(?i)(src|href)=[\"']?(https?:)?//")
            self.assertNotIn("<link", html)
            self.assertNotIn("@import", html)
            self.assertIn("width=device-width", html)
            self.assertIn("prefers-color-scheme", html)
            self.assertIn("min-height:52px", html)  # 큰 버튼

    def test_data_cannot_close_script(self):
        for html in self.all_pages()[1:]:
            self.assertEqual(len(re.findall(r"</script>", html)), 1)


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = C.normalize({})
        s = cfg["services"]
        self.assertEqual(cfg["server"]["port"], 8790)
        self.assertEqual(cfg["server"]["host"], "127.0.0.1")
        ports = {n: int(v["url"].rsplit(":", 1)[1]) for n, v in s.items()}
        self.assertEqual(ports, {"sidekick": 8765, "voice-journal": 8787, "object-memory": 8788,
                                 "sound-watch": 8770, "jarvis": 8800})
        self.assertEqual(s["sidekick"]["token_header"], "X-Sidekick-Token")
        self.assertEqual(s["voice-journal"]["token_header"], "X-VJ-Token")
        self.assertEqual(s["object-memory"]["token_header"], "X-ObjMem-Token")
        self.assertEqual(s["sound-watch"]["token_header"], "X-SoundWatch-Token")
        self.assertEqual(s["jarvis"]["token_header"], "X-Jarvis-Token")
        self.assertEqual(cfg["desk"]["actions"], {})
        self.assertTrue(cfg["desk"]["cards_path"].endswith("cards-latest.json"))

    def test_partial_override_keeps_defaults(self):
        cfg = C.normalize({"services": {"sidekick": {"token": "abc"}}})
        self.assertEqual(list(cfg["services"]), ["sidekick"])
        self.assertEqual(cfg["services"]["sidekick"]["token_header"], "X-Sidekick-Token")

    def test_validation(self):
        bad = [{"services": {"a b": {"url": "http://127.0.0.1:1"}}},
               {"services": {"a": {"url": "file:///etc"}}},
               {"services": {"a": {"url": "http://x:1", "path_mode": "weird"}}},
               {"services": {"a": {"url": "http://x:1", "path_mode": "passthrough"}}},
               {"services": {"a": {"url": "http://x:1", "path_mode": "passthrough", "listen_port": 8790}}},
               {"desk": {"actions": {"x": {"argv": "rm -rf /"}}}},
               {"desk": {"approvals": {"x": "other"}}}]
        for raw in bad:
            with self.assertRaises(ValueError, msg=raw):
                C.normalize(raw)

    def test_token_env_and_file(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "tok.txt"
            f.write_text("filetok-123\n", encoding="utf-8")
            cfg = C.normalize({"services": {"a": {"url": "http://x:1", "token_file": str(f)},
                                            "b": {"url": "http://x:2", "token_env": "PH_TEST_B"}}})
            with mock.patch.dict(os.environ, {"PH_TEST_B": "envtok", "PHONE_HOME_TOKEN": "clienttok"}):
                r = C.resolve_secrets(cfg)
        self.assertEqual(r["services"]["a"]["token"], "filetok-123")
        self.assertEqual(r["services"]["b"]["token"], "envtok")
        self.assertEqual(r["server"]["token"], "clienttok")
        self.assertEqual(cfg["services"]["a"]["token"], "")  # 원본은 그대로


if __name__ == "__main__":
    unittest.main()
