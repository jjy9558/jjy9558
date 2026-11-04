import io
import sys
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from homelab import cli, registry, schedule
from homelab.schedule import ScheduleError, parse_win_cmdline, win_join, win_quote
from helpers import TmpCase, make_ctx

WIN_PY = r"C:\Program Files\Python 3.12\python.exe"
WIN_RUNNER = Path(r"C:\Users\홍 길동\My Projects\homelab\homelab_run.py")
WIN_ROOT = Path(r"C:\Users\홍 길동\.homelab")


class Quoting(unittest.TestCase):
    def test_parser_matches_microsoft_examples(self):
        # https://learn.microsoft.com/cpp/cpp/main-function-command-line-args 의 예
        self.assertEqual(parse_win_cmdline('"abc" d e'), ["abc", "d", "e"])
        self.assertEqual(parse_win_cmdline(r'a\\\b d"e f"g h'), [r"a\\\b", "de fg", "h"])
        self.assertEqual(parse_win_cmdline(r'a\\\"b c d'), [r'a\"b', "c", "d"])
        self.assertEqual(parse_win_cmdline(r'a\\\\"b c" d e'), [r"a\\b c", "d", "e"])

    def test_round_trip_nasty_args(self):
        nasty = [r"C:\Program Files\x y\z.exe", "한글 이름", r"C:\dir with space\ ", "trail\\", 'quo"te',
                 "a&b", "c|d", "e<f>g", "(paren)", "", " ", "tab\there", r"back\\slash\\", "a=b,c;d"]
        for a in nasty:
            line = win_join([a, "x"])
            self.assertEqual(parse_win_cmdline(line), [a, "x"], a)

    def test_percent_is_refused(self):
        with self.assertRaises(ScheduleError):
            win_quote(r"C:\Users\%USERNAME%\x")

    def test_plain_args_not_quoted(self):
        self.assertEqual(win_quote("/create"), "/create")
        self.assertEqual(win_quote("Homelab-morning-brief"), "Homelab-morning-brief")


class Tasks(TmpCase):
    def make(self, python=WIN_PY, root=WIN_ROOT, **kw):
        ctx = make_ctx(self.tmp, **kw)
        ctx.settings["python"] = python
        ctx.layout.root = root
        patcher = mock.patch.object(schedule, "RUNNER", WIN_RUNNER)
        patcher.start()
        self.addCleanup(patcher.stop)
        return ctx

    def test_all_jobs_present_with_times(self):
        ctx = self.make()
        tasks = {t.job.name: t for t in schedule.build_tasks(ctx)}
        self.assertEqual(set(tasks), {"morning-brief", "til-drafter", "game-calendar", "community-briefing",
                                      "life-nagger", "devguard-wallet", "soundwatch-report", "cooloff-check"})
        self.assertEqual((tasks["morning-brief"].when, tasks["morning-brief"].time), ("daily", "06:50"))
        self.assertEqual(tasks["til-drafter"].time, "23:30")
        self.assertEqual(tasks["game-calendar"].time, "07:00")
        self.assertEqual(tasks["community-briefing"].time, "06:30")
        self.assertEqual(tasks["life-nagger"].when, "logon")
        self.assertEqual(tasks["soundwatch-report"].when, "weekly")
        self.assertEqual(tasks["devguard-wallet"].when, "daily")
        self.assertEqual(tasks["cooloff-check"].when, "daily")

    def test_schtasks_argv_round_trips_with_spaces_and_korean_paths(self):
        ctx = self.make()
        for t in schedule.build_tasks(ctx):
            argv = t.schtasks_argv()
            self.assertEqual(argv[:2], ["schtasks", "/create"])
            tr = argv[argv.index("/tr") + 1]
            self.assertEqual(parse_win_cmdline(tr), t.inner)          # 스케줄러가 풀면 원래 인자
            self.assertEqual(t.inner[0], WIN_PY)
            self.assertIn("홍 길동", tr)
            line = t.schtasks_line()                                 # 사람이 cmd 에 붙여넣는 줄
            self.assertEqual(parse_win_cmdline(line), argv)          # cmd 가 풀면 argv
            self.assertTrue(line.startswith("schtasks /create"))
            self.assertNotIn(ctx.token("sidekick"), line)            # 토큰은 명령줄에 없다
            self.assertEqual(argv[-1], "/f")

    def test_tr_quoting_shape(self):
        ctx = self.make()
        t = schedule.build_tasks(ctx, ["morning-brief"])[0]
        line = t.schtasks_line()
        self.assertIn(r'/tr "\"C:\Program Files\Python 3.12\python.exe\" ', line)
        self.assertIn("/sc DAILY /st 06:50", line)

    def test_schedule_flavours(self):
        ctx = self.make()
        by = {t.job.name: t for t in schedule.build_tasks(ctx)}
        self.assertIn("/sc ONLOGON", by["life-nagger"].schtasks_line())
        self.assertIn("/sc WEEKLY /d SUN /st 09:00", by["soundwatch-report"].schtasks_line())

    def test_time_override(self):
        ctx = self.make(schedule_times={"morning-brief": "07:15"})
        t = schedule.build_tasks(ctx, ["morning-brief"])[0]
        self.assertIn("/st 07:15", t.schtasks_line())

    def test_unknown_job(self):
        with self.assertRaises(ScheduleError):
            schedule.build_tasks(self.make(), ["nope"])

    def test_percent_in_path_refused(self):
        ctx = self.make(root=Path(r"C:\Users\%USERNAME%\.homelab"))
        with self.assertRaises(ScheduleError):
            schedule.build_tasks(ctx)[0].schtasks_argv()

    def test_cron_lines(self):
        ctx = self.make(python="/usr/bin/python3", root=PurePosixPath("/home/홍 길동/.homelab"))
        tasks = {t.job.name: t for t in schedule.build_tasks(ctx)}
        mb = tasks["morning-brief"].cron_line("/usr/bin/python3")
        self.assertTrue(mb.startswith("50 6 * * * "))
        self.assertIn("run morning-brief", mb)
        self.assertIn("'/home/홍 길동/.homelab'", mb)       # 공백 경로는 셸 따옴표
        self.assertTrue(tasks["til-drafter"].cron_line("p").startswith("30 23 * * * "))
        self.assertTrue(tasks["soundwatch-report"].cron_line("p").startswith("0 9 * * 0 "))
        self.assertTrue(tasks["life-nagger"].cron_line("p").startswith("@reboot "))

    def test_print_output_has_windows_and_cron(self):
        ctx = self.make()
        lines = schedule.print_lines(ctx, schedule.build_tasks(ctx))
        text = "\n".join(lines)
        self.assertIn("schtasks /create /tn Homelab-morning-brief", text)
        self.assertIn("crontab", text)
        self.assertIn("# @reboot", text)  # life-nagger 는 윈도우 전용이라 주석 처리

    def test_hidden_uses_pythonw_when_present(self):
        ctx = make_ctx(self.tmp)
        py = self.tmp / "py" / "python.exe"
        py.parent.mkdir()
        py.write_text("", encoding="utf-8")
        (py.parent / "pythonw.exe").write_text("", encoding="utf-8")
        ctx.settings["python"] = str(py)
        t = schedule.build_tasks(ctx, ["til-drafter"], hidden=True, platform="win32")[0]
        self.assertTrue(t.inner[0].endswith("pythonw.exe"))
        t2 = schedule.build_tasks(ctx, ["til-drafter"], hidden=True, platform="linux")[0]
        self.assertTrue(t2.inner[0].endswith("python.exe"))


class Install(TmpCase):
    def setUp(self):
        super().setUp()
        self.ctx = make_ctx(self.tmp)
        self.tasks = schedule.build_tasks(self.ctx, ["morning-brief", "til-drafter"])

    def test_refuses_on_non_windows(self):
        out = io.StringIO()
        run = mock.Mock()
        self.assertEqual(schedule.install(self.tasks, out, run=run, platform="linux"), 2)
        run.assert_not_called()

    def test_runs_schtasks_with_arg_lists(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout=b"", stderr=b""))
        out = io.StringIO()
        self.assertEqual(schedule.install(self.tasks, out, run=run, platform="win32"), 0)
        self.assertEqual(run.call_count, 2)
        for call, t in zip(run.call_args_list, self.tasks):
            self.assertEqual(call.args[0], t.schtasks_argv())
            self.assertIs(call.kwargs.get("shell"), False)

    def test_failure_reported(self):
        run = mock.Mock(return_value=mock.Mock(returncode=1, stdout=b"", stderr="액세스가 거부되었습니다".encode("utf-8")))
        out = io.StringIO()
        self.assertEqual(schedule.install(self.tasks, out, run=run, platform="win32"), 1)
        self.assertIn("❌", out.getvalue())

    def test_cli_confirmation(self):
        base = ["--dir", str(self.ctx.layout.root), "--home", str(self.tmp)]
        cli.main(base + ["init", "--yes"], out=io.StringIO())
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout=b"", stderr=b""))
        out = io.StringIO()
        rc = cli.main(base + ["schedule", "--install", "--only", "til-drafter"], out=out, platform="win32",
                      ask=lambda q: "n", run=run)
        self.assertEqual(rc, 1)
        run.assert_not_called()
        out = io.StringIO()
        rc = cli.main(base + ["schedule", "--install", "--only", "til-drafter"], out=out, platform="win32",
                      ask=lambda q: "y", run=run)
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_count, 1)
        out = io.StringIO()
        rc = cli.main(base + ["schedule", "--install", "--yes"], out=out, platform="linux")
        self.assertEqual(rc, 2)

    def test_cli_print_is_default_and_runs_nothing(self):
        base = ["--dir", str(self.ctx.layout.root), "--home", str(self.tmp)]
        cli.main(base + ["init", "--yes"], out=io.StringIO())
        out = io.StringIO()
        run = mock.Mock()
        self.assertEqual(cli.main(base + ["schedule"], out=out, run=run), 0)
        run.assert_not_called()
        self.assertIn("schtasks /create", out.getvalue())


if __name__ == "__main__":
    unittest.main()
