import json
import os
import stat
import sys
import unittest
from pathlib import Path

from homelab import configgen, registry, secretstore
from helpers import REPO, TmpCase, make_ctx, project_loader, write_all


class GenerateConfigs(TmpCase):
    def setUp(self):
        super().setUp()
        self.ctx = make_ctx(self.tmp, minipc=True, ntfy=True, wol_mac="aa:bb:cc:dd:ee:ff",
                            appids=[570, 730], morning_gate=True, wallet_limit_usd=15,
                            redact_words=["우리회사"])
        self.files, self.results = write_all(self.ctx)

    def test_everything_created(self):
        self.assertTrue(all(r.status == "created" for r in self.results))
        keys = {f.key for f in self.files}
        for k in ("sidekick", "jarvis-hub", "jarvis-worker", "jarvis-worker-minipc", "voice-journal",
                  "object-memory", "sound-watch", "phone-home", "briefing", "gamecal", "mbrief", "nagger",
                  "til-drafter", "dev-guard"):
            self.assertIn(k, keys)

    def test_documented_locations(self):
        home = self.tmp
        self.assertEqual(self.ctx.cfg("phone-home"), home / ".phone-home" / "config.json")
        self.assertEqual(self.ctx.cfg("object-memory"), home / ".objmem" / "config.json")
        self.assertEqual(self.ctx.cfg("voice-journal"), home / ".vjournal" / "token")
        self.assertEqual(self.ctx.cfg("til-drafter"), home / ".til-drafter.json")
        self.assertEqual(self.ctx.cfg("dev-guard"), home / ".devguard" / "config.json")
        for f in self.files:
            self.assertTrue(f.path.exists(), f.path)

    @unittest.skipIf(os.name == "nt", "POSIX 권한")
    def test_files_are_private(self):
        for f in self.files:
            mode = stat.S_IMODE(os.stat(f.path).st_mode)
            self.assertEqual(mode & 0o077, 0, "%s %o" % (f.path, mode))

    def test_json_files_parse(self):
        for f in self.files:
            if f.path.suffix == ".json":
                json.loads(f.path.read_text(encoding="utf-8"))

    # --- 각 프로젝트의 자기 로더가 받아들이는지 -------------------------------------------------
    def test_sidekick_loader(self):
        m = project_loader("s21-sidekick", "sidekick")
        cfg = m.load_config(str(self.ctx.cfg("sidekick")))
        self.assertEqual(cfg["server"]["token"], self.ctx.token("sidekick"))
        self.assertEqual(cfg["server"]["port"], 8765)
        self.assertTrue(cfg["alarm"]["morning_gate"]["enabled"])
        self.assertEqual(len(cfg["alarm"]["emergency_code"]), 6)

    def test_jarvis_hub_and_workers(self):
        m = project_loader("jarvis-hub", "jarvis")
        hub = m.load_hub_config(str(self.ctx.cfg("jarvis-hub")))
        self.assertEqual(set(hub["devices"]), {"main", "s21", "minipc"})
        self.assertTrue(hub["wake"]["enabled"])
        self.assertEqual(hub["wake"]["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(hub["notify"]["ntfy"]["topic"], self.ctx.extra("ntfy-topic"))
        env = {"JARVIS_TOKEN": self.ctx.token("jarvis-main")}
        wk = m.load_worker_config(str(self.ctx.cfg("jarvis-worker")), environ=env)
        self.assertEqual(wk["device"], "main")
        self.assertEqual(wk["token"], hub["devices"]["main"]["token"])
        self.assertTrue(wk["handlers"]["notify.say"]["enabled"])
        env = {"JARVIS_TOKEN": self.ctx.token("jarvis-minipc")}
        mp = m.load_worker_config(str(self.ctx.cfg("jarvis-worker-minipc")), environ=env)
        self.assertEqual(mp["token"], hub["devices"]["minipc"]["token"])
        self.assertTrue(mp["handlers"]["wake"]["enabled"])

    def test_phone_home_loader_and_token_map(self):
        m = project_loader("phone-home", "phonehome")
        cfg = m.load(str(self.ctx.cfg("phone-home")))
        self.assertEqual(cfg["server"]["token"], self.ctx.token("phone-home"))
        self.assertEqual(set(cfg["services"]), {"sidekick", "object-memory", "voice-journal", "sound-watch", "jarvis"})
        # 뒤쪽 서비스 토큰은 token_file 로 풀려서 각 서비스의 실제 토큰과 같아야 한다
        want = {"sidekick": self.ctx.token("sidekick"), "object-memory": self.ctx.token("object-memory"),
                "voice-journal": self.ctx.token("voice-journal"), "sound-watch": self.ctx.token("sound-watch"),
                "jarvis": self.ctx.token("jarvis-s21")}
        for name, tok in want.items():
            self.assertEqual(cfg["services"][name]["token"], tok, name)
        self.assertEqual(cfg["services"]["jarvis"]["headers"], {"X-Jarvis-Device": "s21"})
        self.assertEqual(Path(cfg["desk"]["cards_path"]),
                         self.ctx.layout.data("morning-brief") / "out" / "cards-latest.json")

    def test_object_memory_loader(self):
        m = project_loader("object-memory", "objmem")
        cfg = m.load(str(self.ctx.cfg("object-memory")))
        self.assertEqual(cfg["token"], self.ctx.token("object-memory"))
        self.assertEqual(Path(cfg["data_dir"]), self.tmp / ".objmem" / "data")

    def test_sound_watch_loader(self):
        m = project_loader("sound-watch", "soundwatch")
        cfg = m.load_config(str(self.ctx.cfg("sound-watch")))
        self.assertEqual(cfg["server"]["token"], self.ctx.token("sound-watch"))
        self.assertTrue(cfg["notify"]["sidekick"]["enabled"])
        self.assertEqual(cfg["notify"]["sidekick"]["token_env"], "SIDEKICK_TOKEN")
        self.assertEqual(cfg["notify"]["ntfy"]["topic"], self.ctx.extra("ntfy-topic"))

    def test_voice_journal_token_file(self):
        text = self.ctx.cfg("voice-journal").read_text(encoding="utf-8")
        self.assertEqual(text.strip(), self.ctx.token("voice-journal"))
        self.assertGreaterEqual(len(text.strip()), 8)  # vjournal.server 최소 길이

    def test_briefing_loader(self):
        m = project_loader("community-briefing", "briefing")
        cfg = m.load(str(self.ctx.cfg("briefing")))
        self.assertEqual(len(cfg["sources"]), 3)
        self.assertTrue(cfg["delivery"]["ntfy"]["enabled"])
        self.assertTrue(Path(cfg["output"]["dir"]).is_absolute())

    def test_morning_brief_loader_and_wiring(self):
        m = project_loader("morning-brief", "mbrief")
        cfg, base = m.load_config(self.ctx.cfg("mbrief"))
        self.assertEqual(Path(cfg["out_dir"]), self.ctx.layout.data("morning-brief") / "out")
        self.assertEqual(Path(cfg["sources"]["game_calendar"]["state"]),
                         self.ctx.layout.data("game-calendar") / "state.json")
        self.assertEqual(Path(cfg["sources"]["community_briefing"]["dir"]),
                         self.ctx.layout.data("community-briefing") / "out")
        self.assertEqual(cfg["notify"]["sidekick"]["token_env"], "SIDEKICK_TOKEN")

    def test_game_calendar_state_matches_morning_brief(self):
        m = project_loader("game-calendar", "gamecal")
        cfg = m.load_config(str(self.ctx.cfg("gamecal")))
        self.assertEqual(cfg["appids"], [570, 730])
        mb = json.loads(self.ctx.cfg("mbrief").read_text(encoding="utf-8"))
        self.assertEqual(Path(cfg["state_file"]), Path(mb["sources"]["game_calendar"]["state"]))

    def test_life_nagger_loader(self):
        m = project_loader("life-nagger", "nagger")
        cfg = m.load_config(str(self.ctx.cfg("nagger")))
        self.assertIs(cfg["mombot"]["allow_sleep"], False)
        self.assertTrue(cfg["outputs"]["sidekick"]["enabled"])
        self.assertFalse(cfg["events"]["enabled"])

    def test_devguard_and_til_configs(self):
        dg = json.loads(self.ctx.cfg("dev-guard").read_text(encoding="utf-8"))
        self.assertEqual(dg["wallet"]["daily_limit_usd"], 15)
        self.assertFalse(dg["wallet"]["prices_confirmed"])
        self.assertTrue(dg["wallet"]["ntfy_url"].endswith(self.ctx.extra("ntfy-topic")))
        til = json.loads(self.ctx.cfg("til-drafter").read_text(encoding="utf-8"))
        self.assertEqual(til["redact_words"], ["우리회사"])


class TokenConsistency(TmpCase):
    def test_tokens_unique_and_long(self):
        ctx = make_ctx(self.tmp, minipc=True)
        toks = list(ctx.secrets["tokens"].values())
        self.assertEqual(len(toks), len(set(toks)))
        self.assertTrue(all(len(t) >= secretstore.MIN_TOKEN_LEN for t in toks))

    def test_one_secret_feeds_every_consumer(self):
        ctx = make_ctx(self.tmp)
        write_all(ctx)
        read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
        sk = ctx.token("sidekick")
        self.assertEqual(read(ctx.cfg("sidekick"))["server"]["token"], sk)
        self.assertEqual(read(ctx.cfg("jarvis-worker"))["handlers"]["notify.say"]["token"], sk)
        self.assertEqual((ctx.layout.token_file("sidekick")).read_text(encoding="utf-8").strip(), sk)
        hub = read(ctx.cfg("jarvis-hub"))
        self.assertEqual(hub["devices"]["s21"]["token"],
                         ctx.layout.token_file("jarvis-s21").read_text(encoding="utf-8").strip())
        self.assertEqual(read(ctx.cfg("phone-home"))["server"]["token"], ctx.token("phone-home"))

    def test_worker_config_has_no_token(self):
        ctx = make_ctx(self.tmp)
        write_all(ctx)
        wk = json.loads(ctx.cfg("jarvis-worker").read_text(encoding="utf-8"))
        self.assertNotIn("token", wk)  # 환경변수 JARVIS_TOKEN 으로만 받는다

    def test_disabled_service_not_in_phone_home(self):
        ctx = make_ctx(self.tmp, enabled=["sidekick", "phone-home"])
        write_all(ctx)
        ph = json.loads(ctx.cfg("phone-home").read_text(encoding="utf-8"))
        self.assertEqual(list(ph["services"]), ["sidekick"])
        self.assertFalse(ctx.cfg("object-memory").exists())


class ApplyRules(TmpCase):
    def setUp(self):
        super().setUp()
        self.ctx = make_ctx(self.tmp)
        self.files, _ = write_all(self.ctx)

    def test_second_run_is_same(self):
        res = configgen.apply(self.files, self.ctx.secrets)
        self.assertTrue(all(r.status == "same" for r in res))

    def test_existing_not_overwritten_without_force(self):
        sk = self.ctx.cfg("sidekick")
        sk.write_text('{"mine": true}\n', encoding="utf-8")
        res = {r.key: r for r in configgen.apply(self.files, self.ctx.secrets)}
        self.assertEqual(res["sidekick"].status, "exists")
        self.assertEqual(sk.read_text(encoding="utf-8"), '{"mine": true}\n')

    def test_force_overwrites_with_backup(self):
        sk = self.ctx.cfg("sidekick")
        sk.write_text('{"mine": true}\n', encoding="utf-8")
        res = {r.key: r for r in configgen.apply(self.files, self.ctx.secrets, force=True)}
        r = res["sidekick"]
        self.assertEqual(r.status, "overwritten")
        self.assertEqual(r.backup.read_text(encoding="utf-8"), '{"mine": true}\n')
        self.assertIn(self.ctx.token("sidekick"), sk.read_text(encoding="utf-8"))
        # 같은 초에 또 덮어써도 백업이 서로 덮어쓰이지 않는다
        sk.write_text('{"mine": 2}\n', encoding="utf-8")
        r2 = {x.key: x for x in configgen.apply(self.files, self.ctx.secrets, force=True,
                                                 clock=lambda: 0)}["sidekick"]
        r3_path = r2.backup
        sk.write_text('{"mine": 3}\n', encoding="utf-8")
        r3 = {x.key: x for x in configgen.apply(self.files, self.ctx.secrets, force=True,
                                                 clock=lambda: 0)}["sidekick"]
        self.assertNotEqual(r3_path, r3.backup)

    def test_dry_run_writes_nothing_and_masks_secrets(self):
        ctx = make_ctx(self.tmp / "other")
        files = configgen.render_all(ctx)
        res = configgen.apply(files, ctx.secrets, dry_run=True)
        self.assertTrue(all(r.status == "would-create" for r in res))
        self.assertFalse((self.tmp / "other" / ".homelab" / "config").exists())
        blob = "\n".join(r.diff for r in res)
        for v in secretstore.all_values(ctx.secrets):
            self.assertNotIn(v, blob)
        self.assertIn("***", blob)

    def test_dry_run_force_shows_diff_for_existing(self):
        sk = self.ctx.cfg("sidekick")
        sk.write_text('{"server": {"token": "%s"}}\n' % self.ctx.token("sidekick"), encoding="utf-8")
        res = {r.key: r for r in configgen.apply(self.files, self.ctx.secrets, force=True, dry_run=True)}
        self.assertEqual(res["sidekick"].status, "would-overwrite")
        self.assertIn("+", res["sidekick"].diff)
        self.assertNotIn(self.ctx.token("sidekick"), res["sidekick"].diff)
        self.assertEqual(json.loads(sk.read_text(encoding="utf-8"))["server"]["token"], self.ctx.token("sidekick"))


if __name__ == "__main__":
    unittest.main()
