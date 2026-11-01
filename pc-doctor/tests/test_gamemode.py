import copy
import unittest

from fakes import FakeSystem, TempHome

from pc_doctor import gamemode, parsers
from pc_doctor.config import DEFAULT_CONFIG

HIGH = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
BAL = "381b4222-f2b5-4f9c-a9d4-e6b4e9ca5e9a"


def cfg(**gm):
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["gamemode"].update(gm)
    return c


def quiet(*_a, **_k):
    pass


def make_sys():
    return FakeSystem(tasks=["OneDrive.exe", "explorer.exe", "Discord.exe"],
                      services={"SysMain": "RUNNING", "Spooler": "STOPPED"},
                      process_paths=[{"Name": "OneDrive", "Id": 1, "Path": r"C:\Users\me\OneDrive\OneDrive.exe"}])


class GameModeTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.home.__enter__()

    def tearDown(self):
        self.home.__exit__(None, None, None)

    def test_on_off_round_trip(self):
        s = make_sys()
        c = cfg(stop_processes=["OneDrive.exe", "NotRunning.exe"], stop_services=["SysMain", "Spooler"])
        self.assertTrue(gamemode.turn_on(s, c, yes=True, out=quiet))
        self.assertEqual(parsers.parse_powercfg_active(s.powercfg)["guid"], HIGH)
        self.assertIn(("kill", "onedrive.exe"), s.calls)
        self.assertNotIn(("kill", "notrunning.exe"), s.calls)
        self.assertEqual(s.services["SysMain"], "STOPPED")
        self.assertNotIn(("service_stop", "Spooler"), s.calls)  # 원래 멈춰 있던 건 건드리지 않음
        st = gamemode.load_state()
        self.assertEqual(st["previous_power_plan"], BAL)
        self.assertEqual(st["services"], ["SysMain"])

        self.assertTrue(gamemode.turn_off(s, c, out=quiet))
        self.assertEqual(parsers.parse_powercfg_active(s.powercfg)["guid"], BAL)
        self.assertEqual(s.services["SysMain"], "RUNNING")
        self.assertEqual(s.services["Spooler"], "STOPPED")
        self.assertEqual(s.launched, [r"C:\Users\me\OneDrive\OneDrive.exe"])
        self.assertIsNone(gamemode.load_state())

    def test_protected_targets_refused(self):
        s = make_sys()
        lines = []
        c = cfg(stop_processes=["explorer.exe", "lsass.exe", "MsMpEng.exe", "bad;name"], stop_services=["WinDefend", "mpssvc", "wuauserv"])
        gamemode.turn_on(s, c, yes=True, out=lines.append)
        self.assertFalse(any(k in ("kill", "service_stop") for k, *_ in s.calls))
        self.assertEqual(sum("🚫" in l for l in lines), 7)

    def test_requires_confirmation(self):
        s = make_sys()
        c = cfg(stop_processes=["OneDrive.exe"])
        self.assertFalse(gamemode.turn_on(s, c, confirm=lambda q: False, out=quiet))
        self.assertEqual([x for x in s.calls if x[0] != "ps"], [])
        self.assertIsNone(gamemode.load_state())
        self.assertTrue(gamemode.turn_on(s, c, confirm=lambda q: True, out=quiet))

    def test_dry_run(self):
        s = make_sys()
        self.assertFalse(gamemode.turn_on(s, cfg(stop_processes=["OneDrive.exe"]), dry_run=True, out=quiet))
        self.assertEqual([x for x in s.calls if x[0] != "ps"], [])

    def test_already_on_and_off_when_not_on(self):
        s = make_sys()
        self.assertFalse(gamemode.turn_off(s, cfg(), out=quiet))
        gamemode.turn_on(s, cfg(), yes=True, out=quiet)
        self.assertFalse(gamemode.turn_on(s, cfg(), yes=True, out=quiet))

    def test_bad_power_plan(self):
        with self.assertRaises(gamemode.GameModeError):
            gamemode.resolve_plan("turbo; rm -rf")
        self.assertEqual(gamemode.resolve_plan("ultimate"), "e9a42b02-d5df-448d-aa00-03f14749eb61")

    def test_watch_toggles_automatically(self):
        s = make_sys()
        c = cfg(game_exes=["Cyberpunk2077.exe"], stop_processes=["OneDrive.exe"], poll_seconds=2)
        timeline = iter([None, "start", None, "stop", None])

        def fake_sleep(_sec):
            ev = next(timeline, None)
            if ev == "start":
                s.tasks.add("cyberpunk2077.exe")
            elif ev == "stop":
                s.tasks.discard("cyberpunk2077.exe")

        states = []
        orig = gamemode.load_state

        gamemode.watch(s, c, out=quiet, sleep=lambda x: (fake_sleep(x), states.append(bool(orig()))), max_iterations=5)
        self.assertEqual(states, [False, False, True, True, False])
        self.assertIsNone(gamemode.load_state())

    def test_watch_does_not_turn_off_manual_mode(self):
        s = make_sys()
        c = cfg(game_exes=["game.exe"])
        gamemode.turn_on(s, c, yes=True, out=quiet)
        gamemode.watch(s, c, out=quiet, sleep=lambda x: None, max_iterations=2)
        self.assertIsNotNone(gamemode.load_state())

    def test_watch_needs_games(self):
        with self.assertRaises(gamemode.GameModeError):
            gamemode.watch(make_sys(), cfg(), out=quiet, max_iterations=1)


if __name__ == "__main__":
    unittest.main()
