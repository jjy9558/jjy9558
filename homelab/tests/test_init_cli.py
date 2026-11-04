import io
import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

from homelab import cli, jobs, secretstore, tailscale
from helpers import REPO, TmpCase, make_ctx, write_all


class InitCommand(TmpCase):
    def base(self):
        return ["--dir", str(self.tmp / ".homelab"), "--home", str(self.tmp)]

    def run_cli(self, *args, **kw):
        out = io.StringIO()
        rc = cli.main(self.base() + list(args), out=out, **kw)
        return rc, out.getvalue()

    def test_non_interactive_init(self):
        rc, text = self.run_cli("init", "--yes", "--llm-url", "http://127.0.0.1:8081/v1", "--ntfy",
                                "--wallet-limit", "12", "--redact-words", "회사A,프로젝트B", "--appids", "570,730",
                                "--wol-mac", "aa-bb-cc-dd-ee-ff")
        self.assertEqual(rc, 0, text)
        root = self.tmp / ".homelab"
        s = json.loads((root / "homelab.json").read_text(encoding="utf-8"))
        self.assertEqual(s["llm_url"], "http://127.0.0.1:8081/v1")
        self.assertEqual(s["wol_mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(s["appids"], [570, 730])
        self.assertEqual(s["redact_words"], ["회사A", "프로젝트B"])
        self.assertTrue(s["python"])
        self.assertTrue((root / "secrets.json").exists())
        sec = secretstore.load(root / "secrets.json")
        self.assertIn("jarvis-s21", sec["tokens"])
        self.assertIn("ntfy-topic", sec["extras"])
        # 토큰 값이 화면에 나오지 않는다
        for v in secretstore.all_values(sec):
            self.assertNotIn(v, text)

    @unittest.skipIf(os.name == "nt", "POSIX 권한")
    def test_secrets_chmod_600(self):
        self.run_cli("init", "--yes")
        mode = stat.S_IMODE(os.stat(self.tmp / ".homelab" / "secrets.json").st_mode)
        self.assertEqual(mode, 0o600)

    def test_rerun_keeps_tokens_and_files(self):
        self.run_cli("init", "--yes")
        sec1 = secretstore.load(self.tmp / ".homelab" / "secrets.json")
        sk = self.tmp / ".homelab" / "config" / "sidekick.json"
        sk.write_text('{"mine": 1}\n', encoding="utf-8")
        rc, text = self.run_cli("init", "--yes")
        self.assertEqual(rc, 0)
        self.assertEqual(secretstore.load(self.tmp / ".homelab" / "secrets.json"), sec1)
        self.assertEqual(sk.read_text(encoding="utf-8"), '{"mine": 1}\n')
        self.assertIn("그대로 뒀어요", text)

    def test_force_backs_up_and_realigns(self):
        self.run_cli("init", "--yes")
        sk = self.tmp / ".homelab" / "config" / "sidekick.json"
        sk.write_text('{"mine": 1}\n', encoding="utf-8")
        rc, text = self.run_cli("init", "--yes", "--force")
        self.assertEqual(rc, 0)
        self.assertIn("백업", text)
        self.assertTrue(list(sk.parent.glob("sidekick.json.bak-*")))
        self.assertNotEqual(sk.read_text(encoding="utf-8"), '{"mine": 1}\n')

    def test_rotate_requires_force_and_changes_tokens_everywhere(self):
        self.run_cli("init", "--yes")
        rc, text = self.run_cli("init", "--yes", "--rotate-tokens")
        self.assertEqual(rc, 2)
        old = secretstore.load(self.tmp / ".homelab" / "secrets.json")
        rc, text = self.run_cli("init", "--yes", "--rotate-tokens", "--force")
        self.assertEqual(rc, 0)
        new = secretstore.load(self.tmp / ".homelab" / "secrets.json")
        self.assertNotEqual(old["tokens"]["sidekick"], new["tokens"]["sidekick"])
        # 설정 파일들도 새 토큰으로 맞춰졌다 (doctor 의 일관성 검사로 확인)
        from homelab import doctor
        ctx = cli.load_ctx(cli.build_parser().parse_args(self.base() + ["status"]), io.StringIO())
        bad = [c for c in doctor.token_consistency(ctx) if c.level != "ok"]
        self.assertEqual(bad, [])

    def test_dry_run_writes_nothing(self):
        rc, text = self.run_cli("init", "--yes", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertFalse((self.tmp / ".homelab").exists())
        self.assertFalse((self.tmp / ".phone-home").exists())
        self.assertFalse((self.tmp / ".objmem").exists())
        self.assertIn("[dry-run", text)
        self.assertIn("| +", text)       # diff 가 보인다
        self.assertIn("***", text)       # 토큰은 가려진다

    def test_bad_inputs(self):
        rc, text = self.run_cli("init", "--yes", "--wol-mac", "nope")
        self.assertEqual(rc, 2)
        self.assertIn("wol_mac", text)
        rc, text = self.run_cli("init", "--yes", "--enable", "nonexistent")
        self.assertEqual(rc, 2)
        rc, text = self.run_cli("init", "--yes", "--llm-url", "ftp://x")
        self.assertEqual(rc, 2)

    def test_enable_disable_and_gm_world(self):
        rc, text = self.run_cli("init", "--yes", "--disable", "object-memory,sound-watch", "--gm-world", "내세계")
        self.assertEqual(rc, 0, text)
        s = json.loads((self.tmp / ".homelab" / "homelab.json").read_text(encoding="utf-8"))
        self.assertNotIn("object-memory", s["enabled"])
        self.assertIn("gm-trpg", s["enabled"])
        self.assertFalse((self.tmp / ".objmem" / "config.json").exists())
        ph = json.loads((self.tmp / ".phone-home" / "config.json").read_text(encoding="utf-8"))
        self.assertNotIn("object-memory", ph["services"])

    def test_interactive_prompts_use_answers(self):
        answers = iter(["", "", "http://127.0.0.1:7000/v1", "sidekick,phone-home", "", "", "", "y", "n", "7", "", "", "", "n", ""])
        asked = []

        def ask(q):
            asked.append(q)
            return next(answers)
        out = io.StringIO()
        rc = cli.main(self.base() + ["init"], out=out, ask=ask, is_tty=True)
        self.assertEqual(rc, 0, out.getvalue())
        s = json.loads((self.tmp / ".homelab" / "homelab.json").read_text(encoding="utf-8"))
        self.assertEqual(s["llm_url"], "http://127.0.0.1:7000/v1")
        self.assertEqual(s["enabled"], ["sidekick", "phone-home"])
        self.assertTrue(s["minipc"])
        self.assertFalse(s["ntfy"])
        self.assertEqual(s["wallet_limit_usd"], 7)
        self.assertGreater(len(asked), 8)

    def test_flags_skip_prompts(self):
        asked = []
        out = io.StringIO()
        rc = cli.main(self.base() + ["init", "--python", sys.executable, "--llm-url", "http://127.0.0.1:1/v1",
                                     "--enable", "sidekick", "--hub-host", "127.0.0.1", "--hub-url", "",
                                     "--wol-mac", "", "--no-minipc", "--no-ntfy", "--wallet-limit", "0",
                                     "--redact-words", "", "--gm-world", "", "--appids", "", "--no-morning-gate",
                                     "--tailnet-host", "", "--repo-root", str(REPO)],
                      out=out, ask=lambda q: asked.append(q) or "", is_tty=True)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(asked, [])


class TokenCommand(TmpCase):
    def test_token_show_and_list(self):
        base = ["--dir", str(self.tmp / ".homelab"), "--home", str(self.tmp)]
        cli.main(base + ["init", "--yes"], out=io.StringIO())
        sec = secretstore.load(self.tmp / ".homelab" / "secrets.json")
        out = io.StringIO()
        self.assertEqual(cli.main(base + ["token", "show", "phone-home"], out=out), 0)
        self.assertEqual(out.getvalue().strip(), sec["tokens"]["phone-home"])
        out = io.StringIO()
        cli.main(base + ["token", "list"], out=out)
        self.assertIn("phone-home", out.getvalue())
        self.assertNotIn(sec["tokens"]["phone-home"], out.getvalue())
        self.assertEqual(cli.main(base + ["token", "show", "없는것"], out=io.StringIO()), 1)


class TailscaleCommand(TmpCase):
    def test_only_phone_home_and_funnel_warning(self):
        ctx = make_ctx(self.tmp)
        lines = tailscale.tailscale_lines(ctx)
        cmds = [ln for ln in lines if ln.startswith("tailscale ")]
        self.assertEqual(cmds, ["tailscale serve --bg 8790", "tailscale serve status", "tailscale serve reset"])
        text = "\n".join(lines)
        self.assertIn("funnel", text)
        self.assertIn("쓰지 마세요", text)
        for port in (8765, 8787, 8788, 8770, 8800):
            self.assertFalse(any(("serve" in c and str(port) in c) for c in cmds))
        self.assertNotIn(ctx.token("phone-home"), text)        # 기본은 토큰을 찍지 않는다
        shown = "\n".join(tailscale.tailscale_lines(ctx, show_token=True))
        self.assertIn(ctx.token("phone-home"), shown)

    def test_custom_port(self):
        ctx = make_ctx(self.tmp)
        ctx.settings["ports"] = {"phone-home": 9100}
        self.assertIn("tailscale serve --bg 9100", tailscale.tailscale_lines(ctx))

    def test_cli(self):
        base = ["--dir", str(self.tmp / ".homelab"), "--home", str(self.tmp)]
        cli.main(base + ["init", "--yes", "--tailnet-host", "mypc.tail1234.ts.net"], out=io.StringIO())
        out = io.StringIO()
        self.assertEqual(cli.main(base + ["tailscale", "--print"], out=out), 0)
        self.assertIn("https://mypc.tail1234.ts.net/#token=", out.getvalue())


class RunJob(TmpCase):
    def test_job_gets_secret_env_and_logs(self):
        ctx = make_ctx(self.tmp)
        write_all(ctx)
        captured = {}

        class FakePopen:
            def __init__(self, argv, **kw):
                captured["argv"], captured["kw"] = argv, kw
                self.stdout = io.BytesIO("작업 출력 한 줄\n".encode("utf-8"))

            def wait(self):
                return 0

        out = io.StringIO()
        rc = jobs.run_job(ctx, "morning-brief", ["--dry-run"], out, popen=FakePopen)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["argv"][1:3], ["-m", "mbrief"])
        self.assertIn("--speak", captured["argv"])
        self.assertEqual(captured["argv"][-1], "--dry-run")
        self.assertIs(captured["kw"]["shell"], False)
        self.assertEqual(captured["kw"]["env"]["SIDEKICK_TOKEN"], ctx.token("sidekick"))
        self.assertEqual(captured["kw"]["env"]["JARVIS_TOKEN"], ctx.token("jarvis-s21"))
        self.assertEqual(Path(captured["kw"]["cwd"]), REPO / "morning-brief")
        self.assertIn("작업 출력 한 줄", out.getvalue())
        log = ctx.layout.log_file("job-morning-brief").read_text(encoding="utf-8")
        self.assertIn("작업 출력 한 줄", log)
        self.assertIn("종료 코드 0", log)
        self.assertNotIn(ctx.token("sidekick"), log)

    def test_all_jobs_build_argument_lists(self):
        ctx = make_ctx(self.tmp, ntfy=True)
        from homelab.registry import JOBS
        for name, job in JOBS.items():
            spec = job.build(ctx, [])
            self.assertTrue(all(isinstance(a, str) for a in spec.argv), name)
            self.assertEqual(spec.argv[0], ctx.python)
            self.assertNotIn(ctx.token("sidekick"), " ".join(spec.argv), name)
        cool = JOBS["cooloff-check"].build(ctx, []).argv
        self.assertEqual(cool[-2:], ["--ntfy", ctx.extra("ntfy-topic")])
        wallet = JOBS["devguard-wallet"].build(ctx, [])
        self.assertEqual(wallet.env["DEVGUARD_HOME"], str(ctx.layout.devguard_home))

    def test_missing_config_message(self):
        ctx = make_ctx(self.tmp)
        out = io.StringIO()
        rc = jobs.run_job(ctx, "game-calendar", [], out, popen=mock.Mock())
        self.assertEqual(rc, 2)
        self.assertIn("homelab init", out.getvalue())

    def test_run_command_passes_extra(self):
        base = ["--dir", str(self.tmp / ".homelab"), "--home", str(self.tmp)]
        cli.main(base + ["init", "--yes"], out=io.StringIO())
        seen = {}
        with mock.patch.object(jobs, "run_job", side_effect=lambda ctx, name, extra, out: seen.update(n=name, e=extra) or 0):
            self.assertEqual(cli.main(base + ["run", "til-drafter", "--", "--check"], out=io.StringIO()), 0)
        self.assertEqual(seen, {"n": "til-drafter", "e": ["--check"]})


class HooksCommand(TmpCase):
    def test_hooks_adds_config_to_sidekick_command(self):
        base = ["--dir", str(self.tmp / ".homelab"), "--home", str(self.tmp)]
        cli.main(base + ["init", "--yes"], out=io.StringIO())
        snippet = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": '"python" "/x/s21-sidekick/hooks/claude_pretooluse.py"', "timeout": 60}]}]}}
        fake = lambda argv, cwd: (0, "안내\n" + json.dumps(snippet))
        out = io.StringIO()
        rc = cli.main(base + ["hooks"], out=out, hooks_run=fake)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("--config", out.getvalue())
        self.assertIn("sidekick.json", out.getvalue())
        self.assertIn("settings.json", out.getvalue())

    def test_hooks_failure(self):
        base = ["--dir", str(self.tmp / ".homelab"), "--home", str(self.tmp)]
        cli.main(base + ["init", "--yes"], out=io.StringIO())
        out = io.StringIO()
        self.assertEqual(cli.main(base + ["hooks"], out=out, hooks_run=lambda a, c: (1, "boom")), 1)


class SettingsFiles(TmpCase):
    def test_every_write_target_is_under_the_temp_home(self):
        """다른 프로젝트 폴더에는 아무것도 쓰지 않는다: 모든 쓰기 대상이 임시 홈 안."""
        from homelab import configgen
        ctx = make_ctx(self.tmp, minipc=True, ntfy=True, gm_world="w", enabled=list(__import__("homelab.registry", fromlist=["x"]).SERVICES))
        for f in configgen.render_all(ctx):
            self.assertTrue(f.path.is_relative_to(self.tmp), f.path)
            self.assertFalse(f.path.is_relative_to(REPO), f.path)
        for d in configgen.data_dirs(ctx):
            self.assertTrue(d.is_relative_to(self.tmp), d)
        from homelab.registry import SERVICES
        for name, svc in SERVICES.items():
            self.assertEqual(svc.build(ctx).cwd.parent, REPO)  # 프로젝트 폴더는 cwd 로만 쓴다


if __name__ == "__main__":
    unittest.main()


class MiniPcScenario(TmpCase):
    """미니PC: 허브 + 미니PC 워커(WOL, notify.say)만. 메인 PC 는 같은 secrets.json 으로 워커만."""

    def test_split_setup_shares_tokens(self):
        mini = self.tmp / "mini"
        main = self.tmp / "main"
        out = io.StringIO()
        rc = cli.main(["--dir", str(mini / ".homelab"), "--home", str(mini), "init", "--yes",
                       "--enable", "jarvis-worker-minipc", "--disable", "voice-journal,object-memory,sound-watch,phone-home,jarvis-worker",
                       "--hub-host", "100.64.0.10", "--wol-mac", "aa:bb:cc:dd:ee:ff"], out=out)
        self.assertEqual(rc, 0, out.getvalue())
        hub = json.loads((mini / ".homelab" / "config" / "jarvis-hub.json").read_text(encoding="utf-8"))
        self.assertEqual(hub["server"]["host"], "100.64.0.10")
        self.assertIn("minipc", hub["devices"])
        wk = json.loads((mini / ".homelab" / "config" / "jarvis-worker-minipc.json").read_text(encoding="utf-8"))
        self.assertEqual(wk["hub"], "http://100.64.0.10:8800")
        self.assertTrue(wk["handlers"]["wake"]["enabled"])
        # secrets.json 을 메인 PC 로 복사했다고 치고 거기서 워커만
        (main / ".homelab").mkdir(parents=True)
        (main / ".homelab" / "secrets.json").write_text((mini / ".homelab" / "secrets.json").read_text(encoding="utf-8"), encoding="utf-8")
        out = io.StringIO()
        rc = cli.main(["--dir", str(main / ".homelab"), "--home", str(main), "init", "--yes",
                       "--enable", "jarvis-worker", "--disable", "jarvis-hub,sidekick,voice-journal,object-memory,sound-watch,phone-home",
                       "--hub-url", "http://100.64.0.10:8800"], out=out)
        self.assertEqual(rc, 0, out.getvalue())
        wk2 = json.loads((main / ".homelab" / "config" / "jarvis-worker-main.json").read_text(encoding="utf-8"))
        self.assertEqual(wk2["hub"], "http://100.64.0.10:8800")
        sec_a = json.loads((mini / ".homelab" / "secrets.json").read_text(encoding="utf-8"))
        sec_b = json.loads((main / ".homelab" / "secrets.json").read_text(encoding="utf-8"))
        self.assertEqual(sec_a["tokens"]["jarvis-main"], sec_b["tokens"]["jarvis-main"])
