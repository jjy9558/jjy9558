import copy
import datetime as dt
import unittest

import helpers  # noqa: F401

from devguard import friday
from devguard.config import DEFAULTS

FCFG = DEFAULTS["friday"]


def at(y, mo, d, h=0, mi=0):
    return dt.datetime(y, mo, d, h, mi)


# 2025-01-03 은 금요일
FRI = (2025, 1, 3)
SAT = (2025, 1, 4)
THU = (2025, 1, 2)


def refs(*names):
    return [("refs/heads/x", "a" * 40, "refs/heads/" + n, "b" * 40) for n in names]


class TimeWindow(unittest.TestCase):
    def test_default_friday_after_15(self):
        self.assertFalse(friday.is_blocked_time(at(*FRI, 14, 59), FCFG))
        self.assertTrue(friday.is_blocked_time(at(*FRI, 15, 0), FCFG))
        self.assertTrue(friday.is_blocked_time(at(*FRI, 23, 59), FCFG))

    def test_other_days_not_blocked(self):
        self.assertFalse(friday.is_blocked_time(at(*THU, 16), FCFG))
        self.assertFalse(friday.is_blocked_time(at(*SAT, 16), FCFG))

    def test_custom_days_and_hours(self):
        cfg = dict(FCFG, days=["thu", "금"], after="09:30", before="12:00")
        self.assertFalse(friday.is_blocked_time(at(*THU, 9, 29), cfg))
        self.assertTrue(friday.is_blocked_time(at(*THU, 9, 30), cfg))
        self.assertTrue(friday.is_blocked_time(at(*FRI, 11, 59), cfg))
        self.assertFalse(friday.is_blocked_time(at(*FRI, 12, 0), cfg))

    def test_numeric_hours_and_weekday_numbers(self):
        cfg = dict(FCFG, days=[4], after=13.5)
        self.assertFalse(friday.is_blocked_time(at(*FRI, 13, 29), cfg))
        self.assertTrue(friday.is_blocked_time(at(*FRI, 13, 30), cfg))

    def test_all_day_days(self):
        cfg = dict(FCFG, all_day_days=["sat", "sun"])
        self.assertTrue(friday.is_blocked_time(at(*SAT, 0, 1), cfg))
        self.assertTrue(friday.is_blocked_time(at(2025, 1, 5, 8), cfg))

    def test_garbage_config_falls_back(self):
        cfg = dict(FCFG, after="soon", days=["funday", None, True])
        friday.is_blocked_time(at(*FRI, 16), cfg)  # 예외 없이 동작


class BranchMatching(unittest.TestCase):
    def test_protected(self):
        fri = at(*FRI, 16)
        for b in ("main", "master", "release/1.2", "release/2025/01"):
            self.assertEqual(friday.check(fri, refs(b), FCFG), [b], b)

    def test_unprotected(self):
        fri = at(*FRI, 16)
        for b in ("feature/main", "develop", "release", "mainline", "hotfix/x"):
            self.assertEqual(friday.check(fri, refs(b), FCFG), [], b)

    def test_not_friday_nothing_blocked(self):
        self.assertEqual(friday.check(at(*THU, 16), refs("main"), FCFG), [])

    def test_custom_branches(self):
        cfg = dict(FCFG, branches=["prod", "deploy/*"])
        fri = at(*FRI, 16)
        self.assertEqual(friday.check(fri, refs("prod", "main", "deploy/eu"), cfg), ["prod", "deploy/eu"])

    def test_tags_only_when_configured(self):
        fri = at(*FRI, 16)
        tag = [("refs/tags/v1", "a" * 40, "refs/tags/v1.0.0", "0" * 40)]
        self.assertEqual(friday.check(fri, tag, FCFG), [])
        self.assertEqual(friday.check(fri, tag, dict(FCFG, tags=["v*"])), ["tag:v1.0.0"])

    def test_delete_push_to_protected_is_checked(self):
        fri = at(*FRI, 16)
        r = [("(delete)", "0" * 40, "refs/heads/release/1", "b" * 40)]
        self.assertEqual(friday.check(fri, r, FCFG), ["release/1"])

    def test_parse_stdin(self):
        text = "refs/heads/a %s refs/heads/main %s\n\nbad line\n" % ("1" * 40, "2" * 40)
        self.assertEqual(friday.parse_pre_push_stdin(text), [("refs/heads/a", "1" * 40, "refs/heads/main", "2" * 40)])


class RunBehaviour(unittest.TestCase):
    STDIN = "refs/heads/main %s refs/heads/main %s\n" % ("1" * 40, "2" * 40)

    def test_blocks_and_message(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = friday.run(self.STDIN, now=at(*FRI, 16, 5), env={}, cfg={"friday": FCFG})
        self.assertEqual(rc, 1)
        out = buf.getvalue()
        self.assertIn("금요일", out)
        self.assertIn("main", out)
        self.assertIn("DEVGUARD_FRIDAY_OK=1", out)
        self.assertIn("--i-know", out)

    def test_env_override(self):
        self.assertEqual(friday.run(self.STDIN, now=at(*FRI, 16), env={"DEVGUARD_FRIDAY_OK": "1"},
                                    cfg={"friday": FCFG}), 0)

    def test_env_other_value_does_not_override(self):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(friday.run(self.STDIN, now=at(*FRI, 16), env={"DEVGUARD_FRIDAY_OK": "0"},
                                        cfg={"friday": FCFG}), 1)

    def test_i_know_flag(self):
        self.assertEqual(friday.run(self.STDIN, i_know=True, now=at(*FRI, 16), env={}, cfg={"friday": FCFG}), 0)

    def test_fake_clock_env(self):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            rc = friday.run(self.STDIN, env={"DEVGUARD_NOW": "2025-01-03T15:00"}, cfg={"friday": FCFG})
        self.assertEqual(rc, 1)
        self.assertEqual(friday.run(self.STDIN, env={"DEVGUARD_NOW": "2025-01-03T14:00"}, cfg={"friday": FCFG}), 0)

    def test_broken_config_never_crashes_push(self):
        self.assertEqual(friday.run(self.STDIN, now=at(*FRI, 16), env={}, cfg={"friday": None}), 0)

    def test_message_is_deterministic_and_varied(self):
        a = friday.pick_message(at(*FRI, 16), ["main"])
        self.assertEqual(a, friday.pick_message(at(*FRI, 16), ["main"]))
        seen = {friday.pick_message(at(2025, 1, d, 16), ["main"]) for d in range(1, 9)}
        self.assertGreater(len(seen), 1)
        self.assertIn("main", a)


if __name__ == "__main__":
    unittest.main()
