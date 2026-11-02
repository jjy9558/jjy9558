"""Playwright(Node)가 있으면 폰 페이지를 가짜 카메라로 열어 실제 업로드가 일어나는지 확인. 없으면 건너뜀."""
import json
import os
import shutil
import subprocess
import threading
import unittest

import helpers
from helpers import FakeVlm, make_app, obj
from objmem.server import make_server

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [url, token] = process.argv.slice(2);
  const browser = await chromium.launch({ args: [
    '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--no-proxy-server'] });
  const ctx = await browser.newContext({ permissions: ['camera'] });
  const page = await ctx.newPage();
  await page.goto(url + '/#token=' + token + '&interval=0.05&camera=%EA%B1%B0%EC%8B%A4');
  const hashCleared = await page.evaluate(() => location.hash === '');
  await page.click('#go');
  await page.waitForFunction(() => window.__lastUpload, null, { timeout: 20000 });
  const last = await page.evaluate(() => ({ up: window.__lastUpload, text: document.getElementById('last').textContent }));
  await page.click('#pause');
  const paused = await page.evaluate(() => document.getElementById('state').textContent);
  console.log(JSON.stringify({ last, hashCleared, paused }));
  await browser.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def playwright_ok():
    node = shutil.which("node")
    if not node:
        return False
    return subprocess.run([node, "-e", "require('playwright')"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


@unittest.skipUnless(playwright_ok(), "node playwright 없음")
class PhonePageSmoke(unittest.TestCase, helpers.TmpCase):
    def test_fake_camera_uploads_frame(self):
        tmp = self.make_tmp()
        vlm = FakeVlm([{"objects": [obj("리모컨", "소파 위")], "scene": ""}])
        app = make_app(tmp, vlm=vlm, quiet=True, max_frame_bytes=8 * 1024 * 1024)
        srv = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        script = os.path.join(tmp, "smoke.js")
        with open(script, "w") as f:
            f.write(SCRIPT)
        env = dict(os.environ, NODE_PATH=os.environ.get("NODE_PATH") or subprocess.run(
            ["npm", "root", "-g"], capture_output=True, text=True).stdout.strip())
        p = subprocess.run(["node", script, "http://127.0.0.1:%d" % srv.server_address[1], app.cfg["token"]],
                           capture_output=True, text=True, timeout=90, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertTrue(out["hashCleared"])
        self.assertEqual(out["last"]["up"]["status"], "stored")
        self.assertNotEqual(out["last"]["text"], "없음")
        self.assertIn("일시정지", out["paused"])
        with app.store.conn() as c:
            row = c.execute("SELECT camera, phash, size FROM frames").fetchone()
        self.assertEqual(row["camera"], "거실")
        self.assertRegex(row["phash"], r"^[0-9a-f]{16}$")
        self.assertGreater(row["size"], 100)
        self.assertEqual(vlm.calls, 1)


if __name__ == "__main__":
    unittest.main()
