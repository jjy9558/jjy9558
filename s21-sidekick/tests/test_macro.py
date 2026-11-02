import datetime as dt
import json
import os
import secrets
import tempfile
import unittest
from unittest import mock

from helpers import FakeClock, FakeMono, LiveServer, config
from sidekick import macro as macro_mod
from sidekick import winkeys
from sidekick.config import ConfigError, build_config, validate
from sidekick.macro import MacroPad, validate_macros
from sidekick.server import Sidekick


def key():
    return secrets.token_urlsafe(24)


class FakeSender(winkeys.KeySender):
    def __init__(self):
        self.calls = []

    def send_combo(self, vks):
        self.calls.append(("combo", list(vks)))

    def type_text(self, text):
        self.calls.append(("text", text))


def items_basic():
    return [
        {"id": "code", "label": "VS Code", "type": "launch", "program": "C:/x/Code.exe", "args": ["--new-window", "a b; rm -rf /"]},
        {"id": "web", "label": "Web", "type": "url", "url": "https://example.com/"},
        {"id": "mute", "label": "Mute", "type": "keys", "keys": "mute"},
        {"id": "sig", "label": "Sig", "type": "text", "text": "안녕 😀"},
        {"id": "dep", "label": "Dep", "type": "sidekick", "action": "departure"},
        {"id": "both", "label": "Both", "type": "chain", "steps": ["web", {"id": "mute", "delay": 2}]},
    ]


class ValidationTests(unittest.TestCase):
    def v(self, items, **kw):
        return validate_macros(dict({"items": items}, **kw))

    def test_valid_set(self):
        self.assertEqual(self.v(items_basic()), [])

    def test_unknown_type_and_missing_fields(self):
        p = self.v([{"id": "a", "label": "A", "type": "explode"}])
        self.assertTrue(any("알 수 없는 type" in x for x in p))
        for bad in ({"id": "a", "type": "url", "url": "https://x.y"},  # label 없음
                    {"id": "a", "label": "A", "type": "launch"},
                    {"id": "a", "label": "A", "type": "launch", "program": "x", "args": "notalist"},
                    {"id": "a", "label": "A", "type": "url", "url": "file:///etc/passwd"},
                    {"id": "a", "label": "A", "type": "url", "url": "javascript:alert(1)"},
                    {"id": "a", "label": "A", "type": "keys"},
                    {"id": "a", "label": "A", "type": "keys", "keys": "ctrl+nope"},
                    {"id": "a", "label": "A", "type": "text", "text": ""},
                    {"id": "a", "label": "A", "type": "sidekick", "action": "rm"},
                    {"id": "a", "label": "A", "type": "sidekick", "action": "stadium", "event": "zzz"},
                    {"id": "bad id!", "label": "A", "type": "text", "text": "x"},
                    {"id": "a", "label": "A", "type": "text", "text": "x", "color": "red"},
                    {"id": "a", "label": "A", "type": "text", "text": "x", "confirm": "yes"},
                    {"id": "a", "label": "A", "type": "chain"}):
            self.assertTrue(self.v([bad]), bad)

    def test_duplicate_ids(self):
        t = {"label": "A", "type": "text", "text": "x"}
        self.assertTrue(any("중복" in x for x in self.v([dict(t, id="a"), dict(t, id="a")])))

    def test_chain_recursion_and_references(self):
        t = {"label": "A", "type": "text", "text": "x"}
        self_ref = {"id": "c", "label": "C", "type": "chain", "steps": ["c"]}
        self.assertTrue(any("재귀" in x for x in self.v([self_ref])))
        nested = [dict(t, id="a"), {"id": "c1", "label": "C", "type": "chain", "steps": ["a"]},
                  {"id": "c2", "label": "C", "type": "chain", "steps": ["c1"]}]
        self.assertTrue(any("재귀" in x for x in self.v(nested)))
        missing = [{"id": "c", "label": "C", "type": "chain", "steps": ["nope"]}]
        self.assertTrue(any("없는 매크로" in x for x in self.v(missing)))

    def test_chain_limits(self):
        t = {"label": "A", "type": "text", "text": "x", "id": "a"}
        long = {"id": "c", "label": "C", "type": "chain", "steps": ["a"] * 9}
        self.assertTrue(any("최대" in x for x in self.v([t, long])))
        self.assertEqual(self.v([t, long], max_chain=9), [])
        slow = {"id": "c", "label": "C", "type": "chain", "steps": [{"id": "a", "delay": 99}]}
        self.assertTrue(self.v([t, slow]))

    def test_nfc_key_rules(self):
        k = key()
        t = {"label": "A", "type": "text", "text": "x"}
        self.assertEqual(self.v([dict(t, id="a", nfc_key=k)]), [])
        self.assertTrue(self.v([dict(t, id="a", nfc_key="short")]))
        self.assertTrue(self.v([dict(t, id="a", nfc_key=k), dict(t, id="b", nfc_key=k)]))
        self.assertTrue(validate_macros({"items": [dict(t, id="a", nfc_key=k)]}, server_token=k))

    def test_config_validate_includes_macros(self):
        cfg = config(macro={"items": [{"id": "a", "label": "A", "type": "nope"}]})
        with self.assertRaises(ConfigError):
            validate(cfg)
        validate(config(macro={"items": items_basic()}))


class WinkeysTests(unittest.TestCase):
    def test_parse_combo(self):
        self.assertEqual(winkeys.parse_combo("ctrl+shift+esc"), [0x11, 0x10, 0x1B])
        self.assertEqual(winkeys.parse_combo("Win + D"), [0x5B, 0x44])
        self.assertEqual(winkeys.parse_combo("shift+ctrl+alt+7"), [0x10, 0x11, 0x12, 0x37])
        self.assertEqual(winkeys.parse_combo("f1"), [0x70])
        self.assertEqual(winkeys.parse_combo("alt+F24"), [0x12, 0x87])
        self.assertEqual(winkeys.parse_combo("a+ctrl"), [0x11, 0x41])  # 수정 키가 앞으로

    def test_media_keys(self):
        for name, vk in (("play/pause", 0xB3), ("next", 0xB0), ("prev", 0xB1), ("volumeup", 0xAF),
                         ("volumedown", 0xAE), ("mute", 0xAD)):
            self.assertEqual(winkeys.parse_combo(name), [vk])

    def test_parse_errors(self):
        for bad in ("", "ctrl", "ctrl+", "a+b", "ctrl+ctrl+a", "f25", "f0", "nope", "ctrl+가", None, 5):
            with self.assertRaises(winkeys.KeyParseError, msg=repr(bad)):
                winkeys.parse_combo(bad)

    def test_combo_inputs_structure(self):
        ev = winkeys.key_combo_inputs([0x11, 0x43])
        self.assertEqual([(e["vk"], bool(e["flags"] & winkeys.KEYEVENTF_KEYUP)) for e in ev],
                         [(0x11, False), (0x43, False), (0x43, True), (0x11, True)])
        arrow = winkeys.key_combo_inputs([0x25])
        self.assertTrue(arrow[0]["flags"] & winkeys.KEYEVENTF_EXTENDEDKEY)

    def test_text_inputs_structure(self):
        ev = winkeys.text_inputs("a가")
        self.assertEqual(len(ev), 4)
        self.assertEqual([e["scan"] for e in ev], [0x61, 0x61, 0xAC00, 0xAC00])
        self.assertEqual([e["flags"] for e in ev], [4, 6, 4, 6])
        self.assertTrue(all(e["vk"] == 0 for e in ev))

    def test_text_inputs_surrogates_and_newline(self):
        ev = winkeys.text_inputs("😀\n")
        self.assertEqual([e["scan"] for e in ev[:4]], [0xD83D, 0xD83D, 0xDE00, 0xDE00])
        self.assertEqual([(e["vk"], e["flags"]) for e in ev[4:]], [(0x0D, 0), (0x0D, 2)])

    def test_text_too_long(self):
        with self.assertRaises(ValueError):
            winkeys.text_inputs("x" * (winkeys.MAX_TEXT + 1))

    @unittest.skipIf(os.name == "nt", "비 Windows 전용")
    def test_windows_only_error(self):
        with self.assertRaises(winkeys.WindowsOnlyError) as cm:
            winkeys.default_sender()
        self.assertIn("Windows", str(cm.exception))


class PadTests(unittest.TestCase):
    def setUp(self):
        self.launched, self.opened, self.slept, self.internal_calls = [], [], [], []
        self.sender = FakeSender()
        self.mono = FakeMono()
        self.cfg = build_config({"macro": {"items": items_basic()}})["macro"]
        self.pad = self.make(self.cfg)

    def make(self, cfg, **kw):
        args = dict(launcher=lambda argv, cwd=None: self.launched.append((argv, cwd)),
                    opener=lambda u: self.opened.append(u) or True, key_sender=self.sender,
                    internal={"departure": lambda it: self.internal_calls.append(it["id"]) or {"ok": True}},
                    sleep=self.slept.append, clock=FakeClock(dt.datetime(2026, 11, 2, 9, 0)),
                    monotonic=self.mono)
        args.update(kw)
        return MacroPad(cfg, **args)

    def test_launch_argument_list_untouched(self):
        r = self.pad.run("code")
        self.assertTrue(r["ok"])
        self.assertEqual(self.launched, [(["C:/x/Code.exe", "--new-window", "a b; rm -rf /"], None)])

    def test_launch_uses_popen_with_shell_false(self):
        pad = MacroPad(self.cfg, opener=lambda u: True, key_sender=self.sender, monotonic=self.mono)
        with mock.patch.object(macro_mod.subprocess, "Popen") as popen:
            r = pad.run("code")
        self.assertTrue(r["ok"], r)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["C:/x/Code.exe", "--new-window", "a b; rm -rf /"])
        self.assertIs(kwargs["shell"], False)

    def test_launch_failure_reported(self):
        def boom(argv, cwd=None):
            raise FileNotFoundError("nope")
        r = self.make(self.cfg, launcher=boom).run("code")
        self.assertFalse(r["ok"])
        self.assertIn("실행 실패", r["error"])

    def test_url_uses_webbrowser(self):
        pad = MacroPad(self.cfg, launcher=lambda *a, **k: None, key_sender=self.sender, monotonic=self.mono)
        with mock.patch.object(macro_mod.webbrowser, "open", return_value=True) as op:
            r = pad.run("web")
        self.assertTrue(r["ok"])
        op.assert_called_once_with("https://example.com/")

    def test_url_open_false_is_error(self):
        r = self.make(self.cfg, opener=lambda u: False).run("web")
        self.assertFalse(r["ok"])

    def test_keys_and_text(self):
        self.assertTrue(self.pad.run("mute")["ok"])
        self.mono.t += 5
        self.assertTrue(self.pad.run("sig")["ok"])
        self.assertEqual(self.sender.calls, [("combo", [0xAD]), ("text", "안녕 😀")])

    def test_keys_on_non_windows_clear_error(self):
        if os.name == "nt":
            self.skipTest("Windows")
        r = self.make(self.cfg, key_sender=None).run("mute")
        self.assertFalse(r["ok"])
        self.assertIn("Windows", r["error"])

    def test_sidekick_internal(self):
        self.assertTrue(self.pad.run("dep")["ok"])
        self.assertEqual(self.internal_calls, ["dep"])

    def test_sidekick_internal_failure(self):
        pad = self.make(self.cfg, internal={"departure": lambda it: {"ok": False, "cooldown": True}})
        self.assertFalse(pad.run("dep")["ok"])

    def test_chain_runs_in_order_with_delay(self):
        r = self.pad.run("both")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.opened, ["https://example.com/"])
        self.assertEqual(self.sender.calls, [("combo", [0xAD])])
        self.assertEqual(self.slept, [2.0])
        subs = [e for e in self.pad.recent_audit() if e["source"] == "chain:both"]
        self.assertEqual(len(subs), 2)

    def test_chain_stops_at_failure(self):
        r = self.make(self.cfg, opener=lambda u: False).run("both")
        self.assertFalse(r["ok"])
        self.assertEqual(self.sender.calls, [])

    def test_chain_runtime_guard_against_nested(self):
        cfg = dict(self.cfg, items=[{"id": "c", "label": "C", "type": "chain", "steps": ["d"]},
                                    {"id": "d", "label": "D", "type": "chain", "steps": ["c"]}])
        r = self.make(cfg).run("c")
        self.assertFalse(r["ok"])

    def test_unknown_id(self):
        with self.assertRaises(KeyError):
            self.pad.run("nope")

    def test_disabled(self):
        r = self.make(dict(self.cfg, enabled=False)).run("web")
        self.assertTrue(r["disabled"])
        self.assertEqual(self.opened, [])

    def test_confirm_flag(self):
        items = [{"id": "x", "label": "X", "type": "url", "url": "https://example.com", "confirm": True}]
        pad = self.make(dict(self.cfg, items=items))
        r = pad.run("x")
        self.assertTrue(r["needs_confirm"])
        self.assertFalse(r["ok"])
        self.assertEqual(self.opened, [])
        self.assertTrue(pad.run("x", confirmed=True)["ok"])
        self.assertEqual(self.opened, ["https://example.com"])
        self.assertEqual([e["outcome"] for e in pad.recent_audit()], ["ok", "needs_confirm"])

    def test_rate_limit(self):
        pad = self.make(dict(self.cfg, max_per_minute=2, min_interval_seconds=0))
        self.assertTrue(pad.run("web")["ok"])
        self.assertTrue(pad.run("web")["ok"])
        r = pad.run("web")
        self.assertTrue(r["rate_limited"])
        self.assertGreaterEqual(r["retry_after"], 1)
        self.assertEqual(len(self.opened), 2)
        self.mono.t += 61
        self.assertTrue(pad.run("web")["ok"])

    def test_audit_log(self):
        self.pad.run("web", source="nfc")
        self.mono.t += 5
        self.pad.run("mute")
        a = self.pad.recent_audit()
        self.assertEqual([(e["id"], e["source"], e["outcome"]) for e in a],
                         [("mute", "tab", "ok"), ("web", "nfc", "ok")])
        self.assertEqual(a[0]["time"], "2026-11-02T09:00:00")

    def test_audit_file_and_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit.jsonl")
            pad = self.make(dict(self.cfg, audit_keep=3, min_interval_seconds=0, max_per_minute=100), audit_path=path)
            for _ in range(5):
                pad.run("web")
            self.assertEqual(len(pad.recent_audit(50)), 3)
            with open(path, encoding="utf-8") as f:
                lines = [json.loads(x) for x in f]
            self.assertEqual(len(lines), 5)
            self.assertEqual(set(lines[0]), {"time", "id", "source", "outcome", "detail"})

    def test_public_list_hides_actions_and_keys(self):
        items = items_basic()
        items[0]["nfc_key"] = key()
        s = json.dumps(self.make(dict(self.cfg, items=items)).public_list(), ensure_ascii=False)
        self.assertNotIn("Code.exe", s)
        self.assertNotIn("nfc_key", s)
        self.assertNotIn("example.com", s)


class ServerMacroTests(unittest.TestCase):
    def setUp(self):
        self.main = "secret-token"  # helpers.config의 기본 토큰 (테스트 전용 더미)
        self.k_web, self.k_mute = key(), key()
        items = items_basic()
        items[1]["nfc_key"] = self.k_web
        items[2]["nfc_key"] = self.k_mute
        items.append({"id": "danger", "label": "Danger", "type": "url", "url": "https://example.org", "confirm": True,
                      "nfc_key": key()})
        self.launched, self.opened = [], []
        self.sender = FakeSender()
        cfg = config(macro={"items": items, "min_interval_seconds": 0, "max_per_minute": 5})
        self.app = Sidekick(cfg, clock=FakeClock(dt.datetime(2026, 11, 2, 9, 0)),
                            spawn=lambda fn, *a: fn(*a), macro_sleep=lambda s: None,
                            macro_launcher=lambda argv, cwd=None: self.launched.append(argv),
                            macro_opener=lambda u: self.opened.append(u) or True, macro_keys=self.sender)
        self.live = LiveServer(self.app)
        self.addCleanup(self.live.close)

    def post(self, body, token):
        return self.live.call("/api/macro", body, token=token)

    def test_main_token_runs_any_macro(self):
        code, j = self.post({"id": "code"}, self.main)
        self.assertEqual((code, j["ok"]), (200, True))
        self.assertEqual(self.launched[0][0], "C:/x/Code.exe")
        self.assertEqual(self.app.macros.recent_audit()[0]["source"], "tab")

    def test_no_token_and_wrong_token(self):
        self.assertEqual(self.post({"id": "web"}, None)[0], 401)
        self.assertEqual(self.post({"id": "web"}, key())[0], 401)
        self.assertEqual(self.opened, [])

    def test_nfc_key_scoped_to_its_macro(self):
        code, j = self.post({"id": "web"}, self.k_web)
        self.assertEqual((code, j["ok"]), (200, True))
        self.assertEqual(self.opened, ["https://example.com/"])
        self.assertEqual(self.app.macros.recent_audit()[0]["source"], "nfc")
        # 다른 매크로엔 못 쓴다 (존재 여부도 같은 401)
        self.assertEqual(self.post({"id": "mute"}, self.k_web)[0], 401)
        self.assertEqual(self.post({"id": "code"}, self.k_web)[0], 401)
        self.assertEqual(self.post({"id": "nope"}, self.k_web)[0], 401)
        self.assertEqual(self.sender.calls, [])
        self.assertEqual(self.app.macros.recent_audit()[0]["outcome"], "denied")

    def test_nfc_key_not_valid_for_other_endpoints(self):
        for path, body in (("/api/config", None), ("/api/macro/audit", None), ("/api/say", {"text": "hi"}),
                           ("/api/ping", None)):
            self.assertEqual(self.live.call(path, body, token=self.k_web)[0], 401, path)

    def test_nfc_disabled_flag_revokes(self):
        self.app.macros.items["web"]["nfc_enabled"] = False
        self.assertEqual(self.post({"id": "web"}, self.k_web)[0], 401)

    def test_confirm_over_http(self):
        code, j = self.post({"id": "danger"}, self.main)
        self.assertEqual((code, j.get("needs_confirm")), (200, True))
        self.assertEqual(self.opened, [])
        code, j = self.post({"id": "danger", "confirmed": True}, self.main)
        self.assertEqual((code, j["ok"]), (200, True))
        self.assertEqual(self.opened, ["https://example.org"])
        # confirmed는 JSON true만 인정
        self.assertTrue(self.post({"id": "danger", "confirmed": "true"}, self.main)[1].get("needs_confirm"))

    def test_unknown_and_bad_requests(self):
        self.assertEqual(self.post({"id": "nope"}, self.main)[0], 404)
        self.assertEqual(self.post({}, self.main)[0], 400)
        self.assertEqual(self.post({"id": 5}, self.main)[0], 400)

    def test_rate_limit_429(self):
        for _ in range(5):
            self.assertEqual(self.post({"id": "web"}, self.main)[0], 200)
        code, j = self.post({"id": "web"}, self.main)
        self.assertEqual(code, 429)
        self.assertIn("retry_after", j)

    def test_failure_is_502(self):
        self.app.macros.launcher = lambda argv, cwd=None: (_ for _ in ()).throw(OSError("x"))
        code, j = self.post({"id": "code"}, self.main)
        self.assertEqual((code, j["ok"]), (502, False))

    def test_config_lists_macros_without_secrets_and_audit_endpoint(self):
        code, j = self.live.call("/api/config")
        s = json.dumps(j, ensure_ascii=False)
        self.assertEqual({m["id"] for m in j["macros"]}, {"code", "web", "mute", "sig", "dep", "both", "danger"})
        self.assertNotIn(self.k_web, s)
        self.assertNotIn("Code.exe", s)
        self.post({"id": "web"}, self.main)
        code, j = self.live.call("/api/macro/audit")
        self.assertEqual(j["audit"][0]["id"], "web")

    def test_sidekick_departure_and_stadium_reuse(self):
        cfg = config(macro={"min_interval_seconds": 0, "items": [
            {"id": "dep", "label": "D", "type": "sidekick", "action": "departure"},
            {"id": "st", "label": "S", "type": "sidekick", "action": "stadium", "event": "ci_ok"}]},
            departure={"providers": [{"name": "reminders", "items": ["지갑"]}]})
        app = Sidekick(cfg, clock=FakeClock(dt.datetime(2026, 11, 2, 9, 0)), spawn=lambda fn, *a: fn(*a))
        self.assertTrue(app.macros.run("dep")["ok"])
        self.assertTrue(app.macros.run("st")["ok"])
        kinds = [e["kind"] for e in app.events.since(0)]
        self.assertEqual(kinds, ["say", "stadium"])
        self.assertIn("지갑", app.events.since(0)[0]["text"])


if __name__ == "__main__":
    unittest.main()
