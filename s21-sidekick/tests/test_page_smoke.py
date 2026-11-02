"""선택: Node + Playwright(Chromium)가 있으면 폰 페이지를 실제로 띄워 JS 오류가 없는지 본다. 없으면 skip."""
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import threading
import unittest

from helpers import FakeClock, config
from sidekick import hook
from sidekick.client import SidekickClient
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
                     butler={"providers": [{"name": "greeting", "text": "어서 와요."}]},
                     departure={"providers": [{"name": "reminders", "items": ["지갑", "우산"]}]},
                     approval={"timeout_seconds": 30})
        app = Sidekick(cfg, clock=FakeClock(dt.datetime(2026, 11, 2, 12, 0)),
                       runner=lambda argv, cwd=None, timeout=None: (0, "가짜 요약입니다."),
                       spawn=lambda fn, *a: fn(*a))
        srv = make_server(app, "127.0.0.1", 0, quiet=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            proc = subprocess.Popen(["node", os.path.join(HERE, "page_smoke.js"), base, "secret-token"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            hook_result = {}

            def run_hook():  # 실제 훅 경로: stdin JSON -> 서버 결재 -> 폰 승인 -> JSON allow
                out, err = io.StringIO(), io.StringIO()
                hook_input = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                         "tool_input": {"command": "rm -rf ./build"}})
                code = hook.run(hook_input, lambda: cfg,
                                lambda c, a: SidekickClient(base, "secret-token"), out, err)
                hook_result.update(code=code, out=out.getvalue(), err=err.getvalue())
            hook_thread = None
            lines = []
            timer = threading.Timer(120, proc.kill)
            timer.start()
            for line in proc.stdout:
                lines.append(line.strip())
                if line.strip() == "READY_FOR_APPROVAL" and hook_thread is None:
                    hook_thread = threading.Thread(target=run_hook, daemon=True)
                    hook_thread.start()
            proc.wait()
            proc.stdout.close()
            timer.cancel()
            res = json.loads(lines[-1])
            self.assertEqual(res["errors"], [])
            self.assertIn(res["said"], ("잠깐, 볼게요.", "가짜 요약입니다."))
            self.assertFalse(app.alarm.status()["ringing"])
            self.assertNotIn("소리 실패", res["log"])
            self.assertEqual(res["checklist"], ["챙기세요: 지갑, 우산."])
            self.assertEqual((res["apprLabel"], res["apprCmd"]), ("강제 재귀 삭제", "rm -rf ./build"))
            self.assertIsNotNone(hook_thread)
            hook_thread.join(15)
            self.assertEqual(hook_result.get("code"), 0, hook_result)
            out = json.loads(hook_result["out"])
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
