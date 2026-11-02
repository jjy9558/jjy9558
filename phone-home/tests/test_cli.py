import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phonehome.cli import main

from helpers import FakeBackend, dead_url


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.json"
        self.env = mock.patch.dict(os.environ, {"PHONE_HOME_TOKEN": ""})
        self.env.start()
        os.environ.pop("PHONE_HOME_TOKEN", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        rc = main(["--config", str(self.cfg), *argv], out=buf)
        return rc, buf.getvalue()

    def test_requires_command(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
            main([], out=io.StringIO())

    def test_init_and_token(self):
        rc, out = self.run_cli("init")
        self.assertEqual(rc, 0)
        data = json.loads(self.cfg.read_text(encoding="utf-8"))
        first = data["server"]["token"]
        self.assertGreaterEqual(len(first), 20)
        self.assertEqual(self.run_cli("init")[0], 1)  # 덮어쓰지 않음
        rc, out = self.run_cli("token", "new")
        self.assertEqual(rc, 0)
        second = json.loads(self.cfg.read_text(encoding="utf-8"))["server"]["token"]
        self.assertNotEqual(first, second)
        rc, out = self.run_cli("token", "show")
        self.assertEqual(out.strip(), second)

    def test_check(self):
        be = FakeBackend({("GET", "/api/ping"): (200, {"ok": True}, None)})
        try:
            self.cfg.write_text(json.dumps({"server": {"token": "t" * 24}, "services": {
                "sidekick": {"url": be.url}, "voice-journal": {"url": dead_url()}}}), encoding="utf-8")
            rc, out = self.run_cli("check")
            self.assertEqual(rc, 1)
            self.assertIn("sidekick", out)
            self.assertIn("살아 있음", out)
            self.assertIn("꺼짐", out)
        finally:
            be.close()

    def test_bad_config(self):
        self.cfg.write_text(json.dumps({"services": {"../x": {"url": "http://127.0.0.1:1"}}}), encoding="utf-8")
        rc, out = self.run_cli("check")
        self.assertEqual(rc, 2)
        self.assertIn("설정 오류", out)

    def test_serve_refuses_without_token(self):
        self.cfg.write_text(json.dumps({"server": {"token": "", "port": 0}}), encoding="utf-8")
        rc, out = self.run_cli("serve")
        self.assertEqual(rc, 2)
        self.assertIn("token", out)


if __name__ == "__main__":
    unittest.main()
