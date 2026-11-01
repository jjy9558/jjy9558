import copy
import json
import os
import unittest

from fakes import FakeSystem, TempHome

from pc_doctor import tweaks
from pc_doctor.config import read_json as load, write_json_atomic
from pc_doctor.system import RegValue
from pc_doctor.tweaks_data import ADV, CLASSIC_MENU_CLSID, TWEAKS


def quiet(*_a, **_k):
    pass


def base_registry():
    """일부 값은 원래 있고(HideFileExt=1), 나머지는 없는 상태."""
    return {
        (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced").upper(): {
            "hidefileext": RegValue("REG_DWORD", 1),
            "start_trackdocs": RegValue("REG_DWORD", 1),
        },
        (r"HKCU\Control Panel\Mouse").upper(): {"mousespeed": RegValue("REG_SZ", "1")},
        (r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power").upper(): {
            "hiberbootenabled": RegValue("REG_DWORD", 1)},
    }


class TweakDataTests(unittest.TestCase):
    def test_all_builtin_tweaks_valid_and_unique(self):
        ts = tweaks.all_tweaks()
        self.assertEqual(len({t["id"] for t in ts}), len(ts))
        self.assertTrue(any(t.get("default") for t in ts))

    def test_admin_marking(self):
        by = {t["id"]: t for t in tweaks.all_tweaks()}
        self.assertTrue(tweaks.is_admin_tweak(by["disable_fast_startup"]))
        self.assertTrue(tweaks.is_admin_tweak(by["widgets_policy"]))
        self.assertFalse(tweaks.is_admin_tweak(by["show_extensions"]))
        # 기본 프로필은 관리자 없이 전부 적용 가능해야 함
        self.assertFalse(any(tweaks.is_admin_tweak(t) for t in tweaks.get_tweaks()))

    def test_security_paths_rejected(self):
        for path, name in ((r"HKLM\SOFTWARE\Policies\Microsoft\Windows Defender", "DisableAntiSpyware"),
                           (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", "EnableLUA"),
                           (r"HKLM\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\StandardProfile", "EnableFirewall"),
                           (r"HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity", "Enabled"),
                           (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer", "SmartScreenEnabled")):
            bad = {"id": "bad", "title": "x", "values": [{"path": path, "name": name, "type": "REG_DWORD", "data": 0}]}
            with self.assertRaises(tweaks.TweakError, msg=path):
                tweaks.validate_tweak(bad)

    def test_no_builtin_touches_security(self):
        blob = json.dumps(TWEAKS).lower()
        for word in ("defender", "enablelua", "firewall", "smartscreen", "deviceguard", "windowsupdate"):
            self.assertNotIn(word, blob)

    def test_unknown_id(self):
        with self.assertRaises(tweaks.TweakError):
            tweaks.get_tweaks(["nope"])


class TweakApplyRestoreTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.home.__enter__()

    def tearDown(self):
        self.home.__exit__(None, None, None)

    def test_round_trip_including_values_and_keys_that_did_not_exist(self):
        sysm = FakeSystem(registry=base_registry())
        before = copy.deepcopy(sysm.registry)
        ids = ["show_extensions", "classic_context_menu", "start_no_recommendations", "no_ads_suggestions",
               "mouse_no_acceleration"]
        r = tweaks.apply(sysm, ids, out=quiet)
        self.assertEqual(r["errors"], [])
        self.assertEqual(sorted(r["applied"]), sorted(ids))
        self.assertEqual(sysm.reg_get(ADV, "HideFileExt"), RegValue("REG_DWORD", 0))
        self.assertEqual(sysm.reg_get(CLASSIC_MENU_CLSID + r"\InprocServer32", ""), RegValue("REG_SZ", ""))
        self.assertIn("explorer", r["restart"])

        b = load(r["backup"])
        hide = [e for e in b["values"] if e["name"] == "HideFileExt"][0]
        self.assertTrue(hide["existed"])
        self.assertEqual(hide["previous"], {"type": "REG_DWORD", "data": 1})
        iris = [e for e in b["values"] if e["name"] == "Start_IrisRecommendations"][0]
        self.assertFalse(iris["existed"])
        self.assertIsNone(iris["previous"])
        self.assertEqual(b["keys"], [{"tweak": "classic_context_menu", "key": CLASSIC_MENU_CLSID, "existed": False}])

        res = tweaks.restore(sysm, out=quiet)
        self.assertEqual(res["errors"], [])
        # 레지스트리가 *정확히* 원래대로 (없던 값/키는 삭제, 있던 값은 원래 형식으로)
        after = {k: v for k, v in sysm.registry.items() if v or k in before}
        self.assertEqual(after, before)
        self.assertFalse(sysm.reg_key_exists(CLASSIC_MENU_CLSID))
        self.assertIsNotNone(load(r["backup"])["restored_at"])
        # 다시 restore 해도 대상 백업 없음
        self.assertEqual(tweaks.restore(sysm, out=quiet)["restored"], [])

    def test_backup_written_before_any_change(self):
        sysm = FakeSystem(registry=base_registry())
        order = []
        orig_set = sysm.reg_set

        def spy(path, name, value):
            order.append(("set", len(tweaks.list_backups())))
            orig_set(path, name, value)
        sysm.reg_set = spy
        tweaks.apply(sysm, ["show_extensions"], out=quiet)
        self.assertEqual(order, [("set", 1)])

    def test_backup_failure_aborts_without_changes(self):
        sysm = FakeSystem(registry=base_registry())
        before = copy.deepcopy(sysm.registry)
        import pc_doctor.config as cfgmod
        orig = cfgmod.write_json_atomic

        def fail(path, obj):
            raise OSError("disk full")
        cfgmod.write_json_atomic = fail
        try:
            with self.assertRaises(OSError):
                tweaks.apply(sysm, ["show_extensions"], out=quiet)
        finally:
            cfgmod.write_json_atomic = orig
        self.assertEqual(sysm.registry, before)

    def test_multiple_applies_restore_to_original(self):
        sysm = FakeSystem(registry=base_registry())
        before = copy.deepcopy(sysm.registry)
        import datetime as dt
        tweaks.apply(sysm, ["show_extensions"], out=quiet, now=dt.datetime(2026, 1, 1, 10))
        sysm.reg_set(ADV, "HideFileExt", RegValue("REG_DWORD", 5))  # 사용자가 중간에 바꿈
        tweaks.apply(sysm, ["show_extensions", "show_hidden_files"], out=quiet, now=dt.datetime(2026, 1, 2, 10))
        tweaks.restore(sysm, out=quiet)
        self.assertEqual({k: v for k, v in sysm.registry.items() if v or k in before}, before)

    def test_dry_run_changes_nothing_and_writes_no_backup(self):
        sysm = FakeSystem(registry=base_registry())
        before = copy.deepcopy(sysm.registry)
        lines = []
        r = tweaks.apply(sysm, None, dry_run=True, out=lines.append)
        self.assertEqual(sysm.registry, before)
        self.assertIsNone(r["backup"])
        self.assertEqual(tweaks.list_backups(), [])
        self.assertTrue(any("[dry-run]" in l and "HideFileExt" in l for l in lines))

    def test_admin_tweaks_skipped_without_flag(self):
        sysm = FakeSystem(registry=base_registry(), admin=True)
        r = tweaks.apply(sysm, ["show_extensions", "disable_fast_startup"], out=quiet)
        self.assertEqual(r["skipped"], ["disable_fast_startup"])
        self.assertEqual(r["applied"], ["show_extensions"])
        self.assertEqual(sysm.reg_get(r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power", "HiberbootEnabled"),
                         RegValue("REG_DWORD", 1))
        self.assertFalse(any(c[0] == "reg_set" and c[1].startswith("HKLM") for c in sysm.calls))

    def test_admin_tweaks_with_flag_and_restore(self):
        sysm = FakeSystem(registry=base_registry(), admin=True)
        p = r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
        r = tweaks.apply(sysm, ["disable_fast_startup"], admin=True, out=quiet)
        self.assertEqual(r["applied"], ["disable_fast_startup"])
        self.assertEqual(sysm.reg_get(p, "HiberbootEnabled").data, 0)
        # 관리자 없이 restore → 건너뛰고 백업은 미복원 상태로 남음
        res = tweaks.restore(sysm, out=quiet)
        self.assertEqual(res["restored"], [])
        self.assertEqual(sysm.reg_get(p, "HiberbootEnabled").data, 0)
        self.assertEqual(len(tweaks.list_backups()), 1)
        tweaks.restore(sysm, admin=True, out=quiet)
        self.assertEqual(sysm.reg_get(p, "HiberbootEnabled").data, 1)
        self.assertEqual(tweaks.list_backups(), [])

    def test_partial_failure_reported_and_restorable(self):
        sysm = FakeSystem(registry=base_registry(), readonly_values=[(ADV.upper(), "taskbarda")])
        before = copy.deepcopy(sysm.registry)
        r = tweaks.apply(sysm, ["show_extensions", "taskbar_hide_widgets"], out=quiet)
        self.assertEqual(r["applied"], ["show_extensions"])
        self.assertEqual(len(r["errors"]), 1)
        tweaks.restore(sysm, out=quiet)
        self.assertEqual({k: v for k, v in sysm.registry.items() if v or k in before}, before)

    def test_status(self):
        sysm = FakeSystem(registry=base_registry())
        t = tweaks.get_tweaks(["start_no_recommendations"])[0]
        self.assertEqual(tweaks.status(sysm, t), "not_applied")
        sysm.reg_set(ADV, "Start_TrackDocs", RegValue("REG_DWORD", 0))
        self.assertEqual(tweaks.status(sysm, t), "partial")
        tweaks.apply(sysm, ["start_no_recommendations"], out=quiet)
        self.assertEqual(tweaks.status(sysm, t), "applied")

    def test_restore_rejects_foreign_file(self):
        p = os.path.join(os.environ["PC_DOCTOR_HOME"], "x.json")
        write_json_atomic(p, {"kind": "other"})
        with self.assertRaises(tweaks.TweakError):
            tweaks.restore(FakeSystem(), backup_path=p, out=quiet)


if __name__ == "__main__":
    unittest.main()
