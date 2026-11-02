import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest

import helpers as H

from devguard import payslip, permissions, snippet, wallet

SK = "/opt/sidekick/hooks/claude_pretooluse.py"


def snip(**kw):
    return snippet.build("python", "/opt/dg/devguard_run.py", **kw)


class OrderTests(unittest.TestCase):
    def test_sidekick_first_then_strike(self):
        pre = snip(sidekick=SK)["hooks"]["PreToolUse"]
        self.assertEqual(len(pre), 2)
        self.assertEqual(pre[0]["hooks"][0]["command"], "python " + SK)
        self.assertEqual(pre[0]["matcher"], "Bash|PowerShell")
        self.assertIn("strike", pre[1]["hooks"][0]["command"])

    def test_nagger_goes_after_wallet_stop(self):
        stop = snip(nagger="/x/nag.py")["hooks"]["Stop"]
        self.assertIn("wallet", stop[0]["hooks"][0]["command"])
        self.assertEqual(stop[1]["hooks"][0]["command"], "python /x/nag.py")

    def test_default_unchanged_without_flags(self):
        self.assertEqual(len(snip()["hooks"]["PreToolUse"]), 1)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "settings.json")

    def tearDown(self):
        H.rmtree(self.d)

    def test_preserves_existing_and_unknown_keys_and_orders(self):
        mine = {"type": "command", "command": "echo mine"}
        H.write(self.p, json.dumps({"model": "x", "permissions": {"allow": ["Read"]},
                                    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [mine]}],
                                              "Notification": [{"hooks": [mine]}]}}))
        info = snippet.merge_file(self.p, snip(sidekick=SK))
        self.assertTrue(info["changed"])
        data = json.loads(H.read(self.p))
        self.assertEqual(data["model"], "x")
        self.assertEqual(data["permissions"], {"allow": ["Read"]})
        self.assertEqual(data["hooks"]["Notification"], [{"hooks": [mine]}])
        pre = data["hooks"]["PreToolUse"]
        self.assertIn("sidekick", pre[0]["hooks"][0]["command"])
        self.assertIn("strike", pre[1]["hooks"][0]["command"])
        self.assertEqual(pre[2]["hooks"], [mine])
        # 백업은 병합 전 내용
        self.assertEqual(json.loads(H.read(info["backup"]))["hooks"]["PreToolUse"][0]["hooks"], [mine])

    def test_idempotent(self):
        snippet.merge_file(self.p, snip(sidekick=SK, nagger="/x/n.py"))
        first = H.read(self.p)
        info = snippet.merge_file(self.p, snip(sidekick=SK, nagger="/x/n.py"))
        self.assertFalse(info["changed"])
        self.assertIsNone(info["backup"])
        self.assertEqual(H.read(self.p), first)
        self.assertEqual([f for f in os.listdir(self.d) if ".bak" in f], [])  # 새 파일이었으므로 백업 없음

    def test_partial_overlap_adds_only_missing(self):
        snippet.merge_file(self.p, snip())
        snippet.merge_file(self.p, snip(sidekick=SK))
        pre = json.loads(H.read(self.p))["hooks"]["PreToolUse"]
        cmds = [h["command"] for g in pre for h in g["hooks"]]
        self.assertEqual(len(cmds), len(set(cmds)))
        self.assertIn("sidekick", cmds[0])

    def test_invalid_json_untouched(self):
        H.write(self.p, "{oops")
        with self.assertRaises(snippet.SettingsError):
            snippet.merge_file(self.p, snip())
        self.assertEqual(H.read(self.p), "{oops")
        self.assertEqual(os.listdir(self.d), ["settings.json"])

    def test_wrong_shapes_rejected(self):
        for body in ('[1]', '{"hooks": []}', '{"hooks": {"PreToolUse": {}}}'):
            H.write(self.p, body)
            with self.assertRaises(snippet.SettingsError):
                snippet.merge_file(self.p, snip())
            self.assertEqual(H.read(self.p), body)

    def test_dry_run_writes_nothing(self):
        H.write(self.p, '{"a": 1}')
        info = snippet.merge_file(self.p, snip(), dry_run=True)
        self.assertTrue(info["changed"])
        self.assertIn("+", info["diff"])
        self.assertEqual(H.read(self.p), '{"a": 1}')
        self.assertEqual(os.listdir(self.d), ["settings.json"])

    def test_existing_backup_not_overwritten(self):
        H.write(self.p, '{"a": 1}')
        H.write(self.p + ".bak", "old backup")
        info = snippet.merge_file(self.p, snip())
        self.assertEqual(H.read(self.p + ".bak"), "old backup")
        self.assertNotEqual(info["backup"], self.p + ".bak")

    def test_cli_merge_only_touches_given_path(self):
        home = tempfile.mkdtemp()
        try:
            other = os.path.join(home, ".claude", "settings.json")
            H.write(other, '{"keep": true}')
            H.write(self.p, "{}")
            env = H.git_env({"HOME": home, "USERPROFILE": home, "DEVGUARD_HOME": home})
            run = lambda *a: subprocess.run([sys.executable, "-m", "devguard", "settings-snippet", *a],  # noqa: E731
                                            cwd=H.ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p = run("--merge", self.p, "--dry-run", "--with-sidekick", SK)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(H.read(self.p), "{}")
            p = run("--merge", self.p, "--with-sidekick", SK)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("PreToolUse", H.read(self.p))
            self.assertEqual(H.read(other), '{"keep": true}')
            H.write(self.p, "not json")
            p = run("--merge", self.p)
            self.assertEqual(p.returncode, 1)
            self.assertEqual(H.read(self.p), "not json")
            self.assertEqual(run("--dry-run").returncode, 2)
        finally:
            H.rmtree(home)


def _line(ts, sid, mid, tools=(), model="claude-sonnet-x", out=100):
    content = [{"type": "tool_use", "id": t[0], "name": t[1], "input": t[2]} for t in tools]
    return json.dumps({"timestamp": ts, "sessionId": sid, "cwd": "/w/<b>proj", "requestId": "r" + mid,
                       "message": {"id": mid, "model": model, "content": content,
                                   "usage": {"input_tokens": 10, "output_tokens": out}}})


class PermissionsTests(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(permissions.command_prefixes("cd x && FOO=1 git status | head -3"),
                         ["cd", "git status", "head"])
        self.assertEqual(permissions.command_prefixes("git -C x log"), ["git"])
        self.assertEqual(permissions.command_prefixes("'unterminated"), ["'unterminated"])

    def test_report_and_suggest(self):
        d = tempfile.mkdtemp()
        try:
            lines = []
            for i in range(4):
                lines.append(_line("2026-05-04T10:0%d:00Z" % i, "s1", "m%d" % i, [
                    ("t%d" % i, "Bash", {"command": "git status"}),
                    ("u%d" % i, "Bash", {"command": "rm -rf build"}),
                    ("v%d" % i, "Read", {"file_path": "a"})]))
            lines.append(lines[0])  # 중복 기록은 한 번만
            lines.append("garbage")
            H.write(os.path.join(d, "p", "s1.jsonl"), "\n".join(lines))
            tools, bash, total = permissions.count_tool_calls(d)
            self.assertEqual(total, 12)
            self.assertEqual(bash["git status"], 4)
            sug = permissions.suggest(tools, bash, 3)
            self.assertIn(("Bash(git status:*)", 4), sug["allow"])
            self.assertIn(("Read", 4), sug["allow"])
            self.assertEqual([r for r, _ in sug["review"]], ["Bash(rm:*)"])
            text = permissions.format_report(tools, bash, total, sug)
            self.assertIn("수정하지 않음", text)
            json.loads(permissions.report_json(tools, bash, total, sug))
        finally:
            H.rmtree(d)

    def test_cli_does_not_modify_anything(self):
        d = tempfile.mkdtemp()
        try:
            H.write(os.path.join(d, "s.jsonl"), _line("2026-05-04T10:00:00Z", "s", "m", [("t", "Bash", {"command": "ls"})]))
            before = sorted(os.listdir(d))
            p = subprocess.run([sys.executable, "-m", "devguard", "permissions-report", "--projects-dir", d,
                                "--min-count", "1"], cwd=H.ROOT, env=H.git_env(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Bash(ls:*)", p.stdout.decode("utf-8"))
            self.assertEqual(sorted(os.listdir(d)), before)
        finally:
            H.rmtree(d)


class PayslipTests(unittest.TestCase):
    UTC = dt.timezone.utc

    def _usages(self, lines):
        return [u for u in (wallet.parse_line(l) for l in lines) if u]

    def test_hours_split_and_gap_cut(self):
        # 2026-05-04 월요일: 17:30~18:15 -> 정규 0.5h + 야근 0.25h, 3시간 공백 뒤 기록은 시간에 안 넣음
        ls = [_line("2026-05-04T17:30:00Z", "s1", "a"), _line("2026-05-04T17:45:00Z", "s1", "b"),
              _line("2026-05-04T18:00:00Z", "s1", "c"), _line("2026-05-04T18:15:00Z", "s1", "d"),
              _line("2026-05-04T21:30:00Z", "s1", "e")]
        rows = payslip.compute(self._usages(ls), "2026-05", wallet.merged_prices({}), self.UTC)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertAlmostEqual(r.regular, 0.5, places=2)
        self.assertAlmostEqual(r.overtime, 0.25, places=2)
        self.assertEqual(r.night, 0)

    def test_night_weekend_and_month_filter(self):
        ls = [_line("2026-05-09T23:00:00Z", "w", "a"), _line("2026-05-09T23:10:00Z", "w", "b"),  # 토요일 -> 특근
              _line("2026-05-05T23:00:00Z", "n", "c"), _line("2026-05-05T23:10:00Z", "n", "d"),  # 화요일 밤
              _line("2026-04-05T23:00:00Z", "o", "e"), _line("2026-04-05T23:10:00Z", "o", "f")]  # 다른 달
        rows = {r.session: r for r in payslip.compute(self._usages(ls), "2026-05", wallet.merged_prices({}), self.UTC)}
        self.assertEqual(set(rows), {"w", "n"})
        self.assertAlmostEqual(rows["w"].weekend, 10 / 60, places=2)
        self.assertAlmostEqual(rows["n"].night, 10 / 60, places=2)
        self.assertGreater(rows["n"].cost, 0)

    def test_bad_month(self):
        with self.assertRaises(ValueError):
            payslip.month_bounds("2026/05", None)

    def test_html_escapes_and_warns(self):
        ls = [_line("2026-05-04T10:00:00Z", "s1", "a"), _line("2026-05-04T10:05:00Z", "s1", "b")]
        rows = payslip.compute(self._usages(ls), "2026-05", wallet.merged_prices({}), self.UTC)
        page = payslip.render_html(rows, "2026-05", False)
        self.assertIn("&lt;b&gt;proj", page)
        self.assertNotIn("<b>proj", page)
        self.assertIn("예시값", page)
        self.assertNotIn("예시값", payslip.render_html(rows, "2026-05", True).split("<table>")[0])
        self.assertNotIn("http", page)  # 자체 완결

    def test_cli_writes_file(self):
        d = tempfile.mkdtemp()
        try:
            H.write(os.path.join(d, "s.jsonl"), "\n".join([_line("2026-05-04T10:00:00Z", "s", "a"),
                                                          _line("2026-05-04T10:05:00Z", "s", "b")]))
            out = os.path.join(d, "slip.html")
            p = subprocess.run([sys.executable, "-m", "devguard", "wallet", "payslip", "--month", "2026-05",
                                "--projects-dir", d, "--out", out], cwd=H.ROOT, env=H.git_env(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("급 여 명 세 서", H.read(out))
            p = subprocess.run([sys.executable, "-m", "devguard", "wallet", "payslip", "--month", "bad"],
                               cwd=H.ROOT, env=H.git_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 2)
        finally:
            H.rmtree(d)


if __name__ == "__main__":
    unittest.main()
