"""선택: Node + Playwright(Chromium)가 있으면 폰 페이지를 실제로 띄워 JS 오류가 없는지 본다. 없으면 skip."""
import datetime as dt
import json
import os
import shutil
import subprocess
import threading
import unittest

from helpers import FakeClock, config
from sidekick.server import Sidekick, make_server

HERE = os.path.dirname(os.path.abspath(__file__))


def _playwright_available():
    node = shutil.which("node")
    if not node:
        return False
    try:
        r = subprocess.run([node, "-e", "require('playwright')"], capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


@unittest.skipUnless(_playwright_available(), "node playwright 없음")
class PageSmokeTest(unittest.TestCase):
    def test_page_loads_and_round_trips(self):
        cfg = config(debugger={"mode": "command", "command": ["x"]},
                     butler={"providers": [{"name": "greeting", "text": "어서 와요."}]})
        app = Sidekick(cfg, clock=FakeClock(dt.datetime(2026, 11, 2, 12, 0)),
                       runner=lambda argv, cwd=None, timeout=None: (0, "가짜 요약입니다."),
                       spawn=lambda fn, *a: fn(*a))
        srv = make_server(app, "127.0.0.1", 0, quiet=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            out = subprocess.run(["node", os.path.join(HERE, "page_smoke.js"), base, "secret-token"],
                                 capture_output=True, text=True, timeout=90)
            res = json.loads(out.stdout.strip().splitlines()[-1])
            self.assertEqual(res["errors"], [])
            self.assertIn(res["said"], ("잠깐, 볼게요.", "가짜 요약입니다."))
            self.assertFalse(app.alarm.status()["ringing"])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
