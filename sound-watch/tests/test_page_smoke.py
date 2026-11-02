"""선택: node + playwright(Chromium) 가 있으면 폰 페이지를 가짜 마이크로 실제 구동해 청크 도착을 확인한다. 없으면 skip."""
import json
import os
import shutil
import subprocess
import threading
import unittest

import helpers  # noqa: F401
from soundwatch import config
from soundwatch.server import SoundWatchApp, make_server
from soundwatch.store import Store

HERE = os.path.dirname(os.path.abspath(__file__))


def _playwright_available():
    node = shutil.which("node")
    if not node:
        return False
    try:
        return subprocess.run([node, "-e", "require('playwright')"], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


@unittest.skipUnless(_playwright_available(), "node playwright 없음")
class PhonePageSmokeTest(unittest.TestCase):
    def test_fake_microphone_chunks_arrive(self):
        cfg = config.load_config("/nonexistent")
        cfg["server"]["token"] = "test-token"
        store = Store(":memory:")
        self.addCleanup(store.close)
        app = SoundWatchApp(cfg, store)
        srv = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        r = subprocess.run(["node", os.path.join(HERE, "page_smoke.js"), base, "test-token"],
                           capture_output=True, text=True, timeout=90)
        self.assertTrue(r.stdout.strip(), r.stderr[-500:])
        res = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(res["ok"], res)
        self.assertGreaterEqual(res["sent"], 3)
        self.assertEqual(res["failed"], 0)
        self.assertEqual(res["hashAfter"], "")            # 토큰이 주소창에서 지워짐
        self.assertGreaterEqual(app.chunks, 3)
        self.assertEqual(app.bytes, app.chunks * 32000)    # 1초 = 16000샘플 Int16
        self.assertGreater(res["rmsDb"], -60)             # 가짜 마이크의 삐 소리가 실제로 들어옴
        self.assertGreater(app.engine.summary()["max_dbfs_a"], -60)


if __name__ == "__main__":
    unittest.main()
