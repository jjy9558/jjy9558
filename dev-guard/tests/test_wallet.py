import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import helpers as H
from helpers import rmtree, write

from devguard import wallet
from devguard.config import DEFAULTS, deep_merge

KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc

# 테스트 전용 단가 (실제 가격과 무관)
PRICES = {
    "opus": {"input": 10.0, "output": 50.0, "cache_read": 1.0, "cache_write": 12.5, "cache_write_1h": 20.0},
    "sonnet": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5, "cache_write_1h": 4.0},
    "haiku": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25},
}
WCFG = {"prices": PRICES, "daily_limit_usd": 10, "warn_ratio": 0.8, "prices_confirmed": True}


def rec(ts, model="claude-sonnet-4-5", i=0, o=0, cr=0, cw=0, sid="sess1", mid=None, rid=None, cc=None, **extra):
    usage = {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr,
             "cache_creation_input_tokens": cw}
    if cc is not None:
        usage["cache_creation"] = cc
    message = {"model": model, "usage": usage, "role": "assistant"}
    if mid:
        message["id"] = mid
    d = {"type": "assistant", "timestamp": ts, "sessionId": sid, "cwd": "/proj", "message": message}
    if rid:
        d["requestId"] = rid
    d.update(extra)
    return json.dumps(d)


class ParseTests(unittest.TestCase):
    def test_basic_fields(self):
        u = wallet.parse_line(rec("2025-03-01T10:00:00.123Z", "claude-opus-4-1", i=10, o=20, cr=30, cw=40,
                                  mid="m1", rid="r1"))
        self.assertEqual((u.input, u.output, u.cache_read, u.cache_write_5m, u.cache_write_1h), (10, 20, 30, 40, 0))
        self.assertEqual(u.model, "claude-opus-4-1")
        self.assertEqual(u.session, "sess1")
        self.assertEqual(u.key, ("m1", "r1"))
        self.assertEqual(u.ts, dt.datetime(2025, 3, 1, 10, 0, 0, 123000, tzinfo=UTC))

    def test_cache_creation_breakdown(self):
        u = wallet.parse_line(rec("2025-03-01T10:00:00Z", cw=150, cc={"ephemeral_5m_input_tokens": 100,
                                                                   "ephemeral_1h_input_tokens": 50}))
        self.assertEqual((u.cache_write_5m, u.cache_write_1h), (100, 50))

    def test_malformed_lines_return_none(self):
        bad = ["", "   ", "garbage", "{not json", "[]", "123", '"str"', "null",
               json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
               json.dumps({"message": {"usage": "oops"}}),
               json.dumps({"message": {"usage": {}}}),
               json.dumps({"message": {"usage": {"input_tokens": 0, "output_tokens": 0}}}),
               rec("2025-03-01T10:00:00Z", model="<synthetic>", i=5)]
        for line in bad:
            self.assertIsNone(wallet.parse_line(line), line)

    def test_weird_values_are_tolerated(self):
        line = json.dumps({"timestamp": "nope", "message": {"model": None, "usage": {
            "input_tokens": "12", "output_tokens": -5, "cache_read_input_tokens": None,
            "cache_creation_input_tokens": True}}})
        u = wallet.parse_line(line)
        self.assertEqual((u.input, u.output, u.cache_read, u.cache_write_5m), (12, 0, 0, 0))
        self.assertIsNone(u.ts)
        self.assertEqual(u.model, "")

    def test_timestamps(self):
        p = wallet.parse_timestamp
        self.assertEqual(p("2025-03-01T10:00:00Z"), dt.datetime(2025, 3, 1, 10, tzinfo=UTC))
        self.assertEqual(p("2025-03-01T10:00:00.5Z"), dt.datetime(2025, 3, 1, 10, 0, 0, 500000, tzinfo=UTC))
        self.assertEqual(p("2025-03-01T19:00:00+09:00"), dt.datetime(2025, 3, 1, 10, tzinfo=UTC))
        self.assertEqual(p("2025-03-01T10:00:00.123456789Z"), dt.datetime(2025, 3, 1, 10, 0, 0, 123456, tzinfo=UTC))
        self.assertIsNone(p("yesterday"))
        self.assertIsNone(p(None))
        self.assertIsNone(p(12345))


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dg-claude-")

    def tearDown(self):
        rmtree(self.root)

    def put(self, rel, lines):
        write(os.path.join(self.root, rel), "\n".join(lines) + "\n")

    def test_dedupe_and_malformed(self):
        self.put("proj-a/s1.jsonl", [
            rec("2025-03-01T10:00:00Z", i=100, o=1, mid="m1", rid="r1"),
            "this is not json",
            rec("2025-03-01T10:00:01Z", i=100, o=50, mid="m1", rid="r1"),  # 같은 응답의 최종 기록
            '{"truncated": ',
            rec("2025-03-01T10:01:00Z", i=7, mid="m2", rid="r2"),
            json.dumps({"type": "summary", "summary": "x"}),
        ])
        # 세션 재개로 다른 파일에 복사된 같은 응답
        self.put("proj-a/s2.jsonl", [rec("2025-03-01T10:00:01Z", i=100, o=50, mid="m1", rid="r1", sid="sess2"),
                                     rec("2025-03-01T10:02:00Z", i=3)])
        us = wallet.load_usage(self.root)
        self.assertEqual(sorted(u.input for u in us), [3, 7, 100])
        self.assertEqual([u.output for u in us if u.input == 100], [50])

    def test_nested_subagent_dirs_and_non_jsonl(self):
        self.put("p/sess/subagents/agent-1.jsonl", [rec("2025-03-01T10:00:00Z", i=5, mid="a", rid="b")])
        write(os.path.join(self.root, "p", "notes.txt"), "ignore")
        self.assertEqual(len(wallet.load_usage(self.root)), 1)

    def test_missing_dir(self):
        self.assertEqual(wallet.load_usage(os.path.join(self.root, "nope")), [])

    def test_binary_garbage_file(self):
        with open(os.path.join(self.root, "x.jsonl"), "wb") as f:
            f.write(b"\xff\xfe\x00\x01\n" + rec("2025-03-01T10:00:00Z", i=9).encode() + b"\n")
        self.assertEqual(sum(u.input for u in wallet.load_usage(self.root)), 9)

    def test_since_skips_old_files_by_mtime(self):
        self.put("old.jsonl", [rec("2025-01-01T10:00:00Z", i=1, mid="o", rid="o")])
        self.put("new.jsonl", [rec("2025-03-01T10:00:00Z", i=2, mid="n", rid="n")])
        old = dt.datetime(2025, 1, 2).timestamp()
        os.utime(os.path.join(self.root, "old.jsonl"), (old, old))
        us = wallet.load_usage(self.root, since=dt.datetime.fromtimestamp(old).astimezone() + dt.timedelta(days=30))
        # new.jsonl 의 mtime 은 '지금'이라 포함, old.jsonl 은 제외
        self.assertEqual([u.input for u in us], [2])

    def test_session_fallback_to_filename(self):
        self.put("fallback-sess.jsonl", [json.dumps({"timestamp": "2025-03-01T10:00:00Z",
                                                     "message": {"model": "m", "usage": {"input_tokens": 1}}})])
        self.assertEqual(wallet.load_usage(self.root)[0].session, "fallback-sess")


class CostTests(unittest.TestCase):
    def test_cost_math(self):
        u = wallet.Usage(None, "s", "claude-opus-4-1-20250805", "", input=1000, output=500, cache_read=2000,
                         cache_write_5m=100, cache_write_1h=50)
        self.assertAlmostEqual(wallet.cost_of(u, wallet.merged_prices({"prices": PRICES})),
                               (1000 * 10 + 500 * 50 + 2000 * 1 + 100 * 12.5 + 50 * 20) / 1e6)

    def test_family_matching(self):
        pr = wallet.merged_prices({"prices": PRICES})
        self.assertEqual(wallet.price_for("claude-3-5-haiku-20241022", pr)[0], "haiku")
        self.assertEqual(wallet.price_for("claude-sonnet-4-5", pr)[0], "sonnet")
        self.assertEqual(wallet.price_for("CLAUDE-OPUS-4", pr)[0], "opus")
        fam, _p, exact = wallet.price_for("some-other-model", pr)
        self.assertEqual((fam, exact), ("default", False))

    def test_longest_key_wins_and_user_override(self):
        pr = wallet.merged_prices({"prices": {"opus-4-5": {"input": 1.0, "output": 2.0}}})
        self.assertEqual(wallet.price_for("claude-opus-4-5-2025", pr)[0], "opus-4-5")
        self.assertEqual(wallet.price_for("claude-opus-4-1", pr)[0], "opus")
        u = wallet.Usage(None, "s", "claude-opus-4-5", "", input=1_000_000, output=1_000_000)
        self.assertAlmostEqual(wallet.cost_of(u, pr), 3.0)

    def test_placeholder_prices_present_and_flagged(self):
        self.assertIn("default", wallet.PLACEHOLDER_PRICES)
        self.assertIn("예시", wallet.PRICE_WARNING)

    def test_junk_price_entries_ignored(self):
        pr = wallet.merged_prices({"prices": {"_comment": "x", "sonnet": {"input": "abc", "output": 99}}})
        self.assertEqual(pr["sonnet"]["output"], 99)
        self.assertEqual(pr["sonnet"]["input"], wallet.PLACEHOLDER_PRICES["sonnet"]["input"])

    def test_missing_cache_write_1h_price_falls_back_to_5m_price(self):
        u = wallet.Usage(None, "s", "claude-3-haiku", "", cache_write_1h=1_000_000)
        prices = {"haiku": {"cache_write": 3.0}, "default": {}}
        self.assertAlmostEqual(wallet.cost_of(u, prices), 3.0)


class AggregateTests(unittest.TestCase):
    def test_per_day_in_local_timezone(self):
        us = [wallet.parse_line(rec("2025-03-01T16:30:00Z", i=1_000_000, mid="a", rid="a", sid="s1")),
              wallet.parse_line(rec("2025-03-01T14:59:00Z", i=1_000_000, mid="b", rid="b", sid="s2"))]
        pr = wallet.merged_prices({"prices": PRICES})
        by_utc = wallet.aggregate(us, pr, UTC)
        self.assertEqual(sorted(d.isoformat() for d in by_utc), ["2025-03-01"])
        by_kst = wallet.aggregate(us, pr, KST)  # 16:30Z = 다음날 01:30 KST
        self.assertEqual(sorted(d.isoformat() for d in by_kst), ["2025-03-01", "2025-03-02"])
        self.assertAlmostEqual(by_kst[dt.date(2025, 3, 2)].total.cost, 2.0)

    def test_models_sessions_and_unknown(self):
        us = [wallet.parse_line(rec("2025-03-01T01:00:00Z", "claude-opus-4", i=1000, o=100, mid="1", rid="1", sid="A")),
              wallet.parse_line(rec("2025-03-01T02:00:00Z", "claude-sonnet-4", i=1000, o=100, mid="2", rid="2", sid="A")),
              wallet.parse_line(rec("2025-03-01T03:00:00Z", "weird-model", i=1000, mid="3", rid="3", sid="B"))]
        rep = wallet.aggregate(us, wallet.merged_prices({"prices": PRICES}), UTC)[dt.date(2025, 3, 1)]
        self.assertEqual(set(rep.models), {"claude-opus-4", "claude-sonnet-4", "weird-model"})
        self.assertEqual(set(rep.sessions), {"A", "B"})
        self.assertEqual(rep.unknown_models, {"weird-model"})
        self.assertAlmostEqual(rep.sessions["A"].cost, (1000 * 10 + 100 * 50 + 1000 * 2 + 100 * 10) / 1e6)

    def test_entries_without_timestamp_skipped(self):
        u = wallet.Usage(None, "s", "m", "", input=5)
        self.assertEqual(wallet.aggregate([u], wallet.merged_prices({}), UTC), {})


class ThresholdTests(unittest.TestCase):
    def test_levels(self):
        e = wallet.evaluate_threshold
        self.assertEqual(e(5, 0), "none")
        self.assertEqual(e(5, -1), "none")
        self.assertEqual(e(0, 10), "ok")
        self.assertEqual(e(7.99, 10), "ok")
        self.assertEqual(e(8, 10), "warn")
        self.assertEqual(e(9.99, 10), "warn")
        self.assertEqual(e(10, 10), "over")
        self.assertEqual(e(50, 10), "over")
        self.assertEqual(e(5, 10, warn_ratio=0.5), "warn")


class AlertAndHookTests(unittest.TestCase):
    NOW = dt.datetime(2025, 3, 1, 12, 0, tzinfo=KST)

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dg-claude-")
        self.home = tempfile.mkdtemp(prefix="dg-home-")
        self.cfg = deep_merge(DEFAULTS, {"wallet": WCFG})

    def tearDown(self):
        rmtree(self.root)
        rmtree(self.home)

    def spend(self, usd, ts="2025-03-01T00:30:00Z", n=[0]):
        n[0] += 1
        # sonnet output $10/MTok -> usd/10 MTok
        write(os.path.join(self.root, "p", "s%d.jsonl" % n[0]),
              rec(ts, "claude-sonnet-4", o=int(usd / 10 * 1_000_000), mid="m%d" % n[0], rid="r%d" % n[0]) + "\n")

    def alert(self, sender, **kw):
        lines = []
        level = wallet.run_alert(self.root, self.cfg, now=self.NOW, tz=KST, home=self.home, sender=sender,
                                 out=lines.append, **kw)
        return level, lines

    def test_ok_warn_over_and_console(self):
        level, lines = self.alert(mock.Mock(return_value=True))
        self.assertEqual(level, "ok")
        self.spend(8.5)
        level, lines = self.alert(mock.Mock(return_value=True))
        self.assertEqual(level, "warn")
        self.assertIn("$8.50", lines[0])
        self.spend(2.0)
        self.assertEqual(self.alert(mock.Mock(return_value=True))[0], "over")

    def test_ntfy_sent_once_per_level_per_day(self):
        sender = mock.Mock(return_value=True)
        self.spend(8.5)
        self.alert(sender, ntfy_url="https://ntfy.invalid/topic")
        self.alert(sender, ntfy_url="https://ntfy.invalid/topic")
        self.assertEqual(sender.call_count, 1)
        args = sender.call_args[0]
        self.assertEqual(args[0], "https://ntfy.invalid/topic")
        self.assertTrue(args[1].isascii())  # HTTP 헤더 제목은 ASCII
        self.spend(5.0)  # warn -> over : 다시 알림
        self.alert(sender, ntfy_url="https://ntfy.invalid/topic")
        self.assertEqual(sender.call_count, 2)

    def test_ntfy_failure_does_not_mark_notified(self):
        self.spend(9.0)
        bad = mock.Mock(return_value=False)
        self.alert(bad, ntfy_url="https://ntfy.invalid/t")
        self.alert(bad, ntfy_url="https://ntfy.invalid/t")
        self.assertEqual(bad.call_count, 2)

    def test_no_ntfy_when_ok_or_no_url(self):
        sender = mock.Mock(return_value=True)
        self.alert(sender, ntfy_url="https://ntfy.invalid/t")
        self.spend(9.0)
        self.alert(sender)  # URL 없음
        sender.assert_not_called()

    def test_yesterdays_spend_not_counted_today(self):
        self.spend(50.0, ts="2025-02-28T03:00:00Z")
        self.assertEqual(self.alert(mock.Mock())[0], "ok")

    def test_no_limit(self):
        self.cfg["wallet"]["daily_limit_usd"] = 0
        self.spend(1000)
        self.assertEqual(self.alert(mock.Mock())[0], "none")

    def test_send_ntfy_swallows_errors(self):
        self.assertFalse(wallet.send_ntfy("http://127.0.0.1:9/none", "t", "b", timeout=0.2))
        self.assertFalse(wallet.send_ntfy("not a url", "t", "b"))

    def test_stop_hook_output(self):
        self.spend(8.5)
        payload = json.dumps({"session_id": "x", "hook_event_name": "Stop", "stop_hook_active": False})
        out = wallet.run_stop_hook(payload, self.root, self.cfg, self.NOW, KST)
        data = json.loads(out)
        self.assertEqual(set(data), {"systemMessage"})  # decision/block 금지: Stop 을 막으면 안 됨
        self.assertIn("$8.50", data["systemMessage"])
        self.assertIn("한도", data["systemMessage"])
        self.assertIn("경고", data["systemMessage"])

    def test_stop_hook_shows_session_spend(self):
        write(os.path.join(self.root, "p", "mine.jsonl"),
              rec("2025-03-01T00:30:00Z", "claude-sonnet-4", o=100_000, sid="mine", mid="z", rid="z") + "\n")
        out = wallet.run_stop_hook(json.dumps({"session_id": "mine"}), self.root, self.cfg, self.NOW, KST)
        self.assertIn("이 세션 $1.00", json.loads(out)["systemMessage"])

    def test_stop_hook_never_raises(self):
        self.assertIn("$0.00", json.loads(wallet.run_stop_hook("", os.path.join(self.root, "none"), self.cfg,
                                                              self.NOW, KST))["systemMessage"])
        out = wallet.run_stop_hook("not json", self.root, self.cfg, self.NOW, KST)
        self.assertIn("systemMessage", json.loads(out))
        with mock.patch.object(wallet, "spend_today", side_effect=RuntimeError("x")):
            self.assertEqual(wallet.run_stop_hook("{}", self.root, self.cfg, self.NOW, KST), "")

    def test_report_text(self):
        self.spend(3.0)
        usages = wallet.load_usage(self.root)
        days = wallet.aggregate(usages, wallet.merged_prices(WCFG), KST)
        txt = wallet.format_report(days, prices_confirmed=True)
        self.assertIn("2025-03-01", txt)
        self.assertIn("$    3.00", txt)
        self.assertNotIn("예시값", txt)
        self.assertIn("예시값", wallet.format_report(days, prices_confirmed=False))
        self.assertIn("(기록 없음)", wallet.format_report({}, True))
        self.assertEqual(json.loads(wallet.report_json(days))["2025-03-01"]["cost_usd"], 3.0)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dg-claude-")
        self.home = tempfile.mkdtemp(prefix="dg-home-")
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write(os.path.join(self.root, "p", "s.jsonl"),
              rec(today, "claude-sonnet-4", o=1_000_000, mid="a", rid="b") + "\n")
        write(os.path.join(self.home, "config.json"), json.dumps({"wallet": WCFG}))

    def tearDown(self):
        rmtree(self.root)
        rmtree(self.home)

    def run_cli(self, *args, stdin=b""):
        env = H.git_env({"DEVGUARD_HOME": self.home})
        return subprocess.run([sys.executable, os.path.join(H.ROOT, "devguard_run.py")] + list(args),
                              input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=self.home)

    def test_report_and_stop_hook(self):
        p = self.run_cli("wallet", "report", "--projects-dir", self.root)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("$   10.00", p.stdout.decode("utf-8"))
        p = self.run_cli("wallet", "stop-hook", "--projects-dir", self.root, stdin=b'{"session_id":"q"}')
        self.assertEqual(p.returncode, 0)
        self.assertIn("$10.00", json.loads(p.stdout)["systemMessage"])
        p = self.run_cli("wallet", "report", "--json", "--projects-dir", self.root)
        self.assertEqual(list(json.loads(p.stdout).values())[0]["cost_usd"], 10.0)

    def test_alert_strict(self):
        p = self.run_cli("wallet", "alert", "--strict", "--projects-dir", self.root)
        self.assertEqual(p.returncode, 1)  # 한도 $10 에 정확히 도달 -> over
        self.assertIn("초과", p.stdout.decode("utf-8"))

    def test_init_does_not_overwrite(self):
        p = self.run_cli("wallet", "init")
        self.assertIn("이미 있습니다", p.stdout.decode("utf-8"))
        with open(os.path.join(self.home, "config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["wallet"]["daily_limit_usd"], 10)


if __name__ == "__main__":
    unittest.main()
