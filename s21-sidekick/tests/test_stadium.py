import contextlib
import io
import os
import random
import tempfile
import unittest

from helpers import FakeMono, LiveServer, config, free_port
from sidekick import cli
from sidekick.server import Sidekick
from sidekick.stadium import EVENTS, RateLimiter, Stadium, clean_detail

NO_CONFIG = os.path.join(tempfile.gettempdir(), "sidekick-no-such-config.json")


class RateLimiterTests(unittest.TestCase):
    def test_min_interval_and_window(self):
        mono = FakeMono()
        rl = RateLimiter(max_per_minute=3, min_interval=5, clock=mono)
        self.assertEqual(rl.try_acquire(), (True, 0.0))
        ok, wait = rl.try_acquire()
        self.assertFalse(ok)
        self.assertAlmostEqual(wait, 5)
        mono.t += 5
        self.assertTrue(rl.try_acquire()[0])
        mono.t += 5
        self.assertTrue(rl.try_acquire()[0])
        mono.t += 5  # 3번 썼으니 첫 번째로부터 60초까지 막힘
        ok, wait = rl.try_acquire()
        self.assertFalse(ok)
        self.assertAlmostEqual(wait, 45)
        mono.t += 45
        self.assertTrue(rl.try_acquire()[0])


class StadiumTests(unittest.TestCase):
    def make(self, **kw):
        self.mono = FakeMono()
        return Stadium(config(stadium=kw)["stadium"], monotonic=self.mono, rng=random.Random(0))

    def test_event_routing(self):
        st = self.make(min_interval_seconds=0, max_per_minute=100)
        sounds = {e: st.notify(e)["sound"] for e in EVENTS}
        self.assertEqual(sounds, {"push_ok": "cheer", "ci_ok": "cheer", "push_fail": "boo",
                                  "ci_fail": "boo", "pr_merged": "fireworks"})
        r = st.notify("push_ok", detail="main\n브랜치\x07")
        self.assertTrue(r["say"].endswith(" main 브랜치."))
        self.assertIn(r["say"][:-len(" main 브랜치.")], EVENTS["push_ok"]["lines"])
        with self.assertRaises(ValueError):
            st.notify("rm_rf")

    def test_small_pr_claps_big_pr_fireworks(self):
        st = self.make(min_interval_seconds=0, max_per_minute=100, big_pr_lines=300)
        self.assertEqual(st.notify("pr_merged", lines=12)["sound"], "cheer")
        self.assertEqual(st.notify("pr_merged", lines=1200)["sound"], "fireworks")
        self.assertEqual(st.notify("pr_merged")["sound"], "fireworks")

    def test_rate_limited_and_disabled(self):
        st = self.make(min_interval_seconds=10)
        self.assertTrue(st.notify("ci_ok")["ok"])
        r = st.notify("ci_ok")
        self.assertEqual((r["ok"], r["rate_limited"], r["retry_after"]), (False, True, 11))
        self.assertTrue(self.make(enabled=False).notify("ci_ok")["disabled"])

    def test_clean_detail(self):
        self.assertEqual(clean_detail("a\r\nb\tc"), "a b c")
        self.assertEqual(len(clean_detail("가" * 200)), 80)


class StadiumHttpAndCliTests(unittest.TestCase):
    def setUp(self):
        self.mono = FakeMono()
        self.app = Sidekick(config(stadium={"min_interval_seconds": 30}), monotonic=self.mono,
                            rng=random.Random(0), spawn=lambda fn, *a: fn(*a))
        self.live = LiveServer(self.app)

    def tearDown(self):
        self.live.close()

    def notify(self, *args, token="secret-token", url=None, **kw):
        out, err = io.StringIO(), io.StringIO()
        code = cli.notify(*args, config_path=NO_CONFIG, url=url or self.live.base, token=token,
                          environ={}, out=out, err=err, **kw)
        return code, out.getvalue(), err.getvalue()

    def test_route_auth_validation_and_event(self):
        self.assertEqual(self.live.call("/api/notify", {"event": "ci_fail"}, token=None)[0], 401)
        self.assertEqual(self.live.call("/api/notify", {"event": "nope"})[0], 400)
        self.assertEqual(self.live.call("/api/notify", {"event": "pr_merged", "lines": "many"})[0], 400)
        code, r = self.live.call("/api/notify", {"event": "ci_fail", "detail": "unit tests"})
        self.assertEqual((code, r["sound"]), (200, "boo"))
        ev = self.app.events.since(0)[-1]
        self.assertEqual((ev["kind"], ev["sound"], ev["emoji"], ev["text"]), ("stadium", "boo", "🍅", r["say"]))
        code, r = self.live.call("/api/notify", {"event": "push_ok"})
        self.assertEqual((code, r["rate_limited"]), (429, True))
        self.assertEqual(len(self.app.events.since(0)), 1)  # 레이트 리밋이면 폰에 아무것도 안 감

    def test_cli_ok_rate_limit_auth_and_unreachable(self):
        code, out, _ = self.notify("push_ok", "main")
        self.assertEqual(code, 0)
        self.assertIn("관중석:", out)
        code, _, err = self.notify("push_ok")
        self.assertEqual(code, 0)  # 레이트 리밋은 실패가 아니라 '건너뜀'
        self.assertIn("레이트 리밋", err)
        self.mono.t += 31
        self.assertEqual(self.notify("ci_ok", token="wrong")[0], 1)
        self.assertEqual(self.notify("ci_ok", url="http://127.0.0.1:%d" % free_port())[0], 1)
        self.assertEqual(self.notify("ci_ok", token="")[0], 2)
        self.assertEqual(self.notify("pr_merged", lines=5)[0], 0)
        self.assertEqual(self.app.events.since(0)[-1]["sound"], "cheer")

    def test_cli_reads_token_and_url_from_env(self):
        out, err = io.StringIO(), io.StringIO()
        code = cli.notify("ci_ok", config_path=NO_CONFIG, out=out, err=err,
                          environ={"SIDEKICK_URL": self.live.base, "SIDEKICK_TOKEN": "secret-token"})
        self.assertEqual(code, 0, err.getvalue())

    def test_argparse_rejects_unknown_event(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["notify", "bogus"])


if __name__ == "__main__":
    unittest.main()
