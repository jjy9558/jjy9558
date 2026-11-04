import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamecal import classify as C
from gamecal.alerts import alerts_payload, upcoming_items
from gamecal.cli import main
from gamecal.contract import SCHEMA_VERSION, validate
from gamecal.dates import KST, UTC
from gamecal.models import Entry
from gamecal.state import save_state

TODAY = dt.date(2026, 11, 4)


def ent(gid, category, start, end=None, all_day=True, is_notice=False, game="G", title="T", appid=1, url=""):
    return Entry(gid=gid, appid=appid, game=game, title=title, url=url, category=category, post_ts=0,
                 start=start, end=end, all_day=all_day, is_notice=is_notice)


def day(n):
    return TODAY + dt.timedelta(days=n)


def gids(payload):
    return [it["title"] for it in payload["items"]]


def base_payload(items, today=TODAY):
    return {"version": SCHEMA_VERSION, "generated_for": today.isoformat(), "items": items}


def item(**kw):
    """기본 항목 (generated_for=TODAY). ongoing/ends_in_days는 start/end로부터 계산 (명시하면 그 값 사용)."""
    d = {"kind": "event", "game": "G", "appid": 1, "title": "T", "start": "2026-11-05T00:00:00+09:00",
         "end": "2026-11-06T00:00:00+09:00", "all_day": True, "d_day": 1, "ends_in_days": None,
         "ongoing": None, "url": None}
    d.update(kw)
    s = dt.datetime.fromisoformat(d["start"]) if d["start"] else None
    e = dt.datetime.fromisoformat(d["end"]) if d["end"] else None
    if "ongoing" not in kw:
        d["ongoing"] = bool(s is not None and s.date() < TODAY)
    if "ends_in_days" not in kw:
        d["ends_in_days"] = (e.date() - TODAY).days if e is not None else None
    return d


class SchemaTest(unittest.TestCase):
    def test_generated_payload_validates(self):
        entries = [
            ent("m1", C.MAINT, dt.datetime(2026, 11, 10, 10, tzinfo=UTC),
                end=dt.datetime(2026, 11, 10, 12, tzinfo=UTC), all_day=False, title="점검", url="https://x/1"),
            ent("e1", C.EVENT, day(2), end=day(4), title="이벤트"),
            ent("s1", C.SHUTDOWN, day(30), title="종료"),
        ]
        p = alerts_payload(entries, TODAY)
        validate(p)
        self.assertEqual(p["version"], 1)
        self.assertEqual(p["generated_for"], "2026-11-04")
        self.assertEqual(set(p["items"][0]), {"kind", "game", "appid", "title", "start", "end", "all_day",
                                              "d_day", "ends_in_days", "ongoing", "url"})

    def test_timed_item_format(self):
        e = ent("m1", C.MAINT, dt.datetime(2026, 11, 10, 10, tzinfo=UTC),
                end=dt.datetime(2026, 11, 10, 12, tzinfo=UTC), all_day=False, title="점검", url="https://x/1")
        it = alerts_payload([e], TODAY)["items"][0]
        self.assertEqual(it["kind"], "maintenance")
        self.assertEqual(it["start"], "2026-11-10T19:00:00+09:00")
        self.assertEqual(it["end"], "2026-11-10T21:00:00+09:00")
        self.assertFalse(it["all_day"])
        self.assertEqual(it["d_day"], 6)
        self.assertEqual(it["url"], "https://x/1")

    def test_allday_item_format_end_exclusive(self):
        it = alerts_payload([ent("e1", C.EVENT, day(1), end=day(3))], TODAY)["items"][0]
        self.assertEqual(it["start"], "2026-11-05T00:00:00+09:00")
        self.assertEqual(it["end"], "2026-11-08T00:00:00+09:00")
        self.assertTrue(it["all_day"])
        self.assertIsNone(it["url"])

    def test_validate_accepts_minimal(self):
        validate(base_payload([]))
        validate(base_payload([item()]))

    def test_validate_rejects_bad_shapes(self):
        bad = [
            {"version": 2, "generated_for": "2026-11-04", "items": []},
            {"version": 1, "generated_for": "2026-11-04"},
            {"version": 1, "generated_for": "2026-11-04", "items": [], "extra": 1},
            {"version": True, "generated_for": "2026-11-04", "items": []},
            {"version": 1, "generated_for": "11/04", "items": []},
            base_payload([item(kind="raid")]),
            base_payload([item(kind="shutdown", title=None)]),
            base_payload([item(appid=True)]),
            base_payload([item(appid="570")]),
            base_payload([item(all_day="yes")]),
            base_payload([item(start="2026-11-05T00:00:00+00:00", end=None, d_day=1)]),
            base_payload([item(start="2026-11-05T00:00:00", end=None, d_day=1)]),
            base_payload([item(d_day=2)]),
            base_payload([item(d_day=True)]),
            base_payload([item(start="2026-11-05T09:00:00+09:00", all_day=True, end=None, d_day=1)]),
            base_payload([item(start=None, d_day=1, end=None)]),
            base_payload([item(url=5)]),
            base_payload([item(game=None)]),
        ]
        for obj in bad:
            with self.assertRaises(ValueError, msg=str(obj)):
                validate(obj)

    def test_validate_rejects_unsorted_and_null_not_last(self):
        a = item(title="late", start="2026-11-09T00:00:00+09:00", end=None, d_day=5)
        b = item(title="early", start="2026-11-05T00:00:00+09:00", end=None, d_day=1)
        with self.assertRaises(ValueError):
            validate(base_payload([a, b]))
        n = item(title="nodate", start=None, end=None, d_day=None)
        with self.assertRaises(ValueError):
            validate(base_payload([n, b]))
        validate(base_payload([b, a, n]))


class SortingTest(unittest.TestCase):
    def test_sorted_by_absolute_start_across_kinds(self):
        entries = [
            ent("e1", C.EVENT, day(5), title="all-day day5"),
            ent("m1", C.MAINT, dt.datetime(2026, 11, 5, 14, tzinfo=UTC), all_day=False,
                end=dt.datetime(2026, 11, 5, 15, tzinfo=UTC), title="timed 23:00 KST day1"),
            ent("s1", C.SHUTDOWN, day(3), title="shutdown day3"),
            ent("e2", C.EVENT, day(1), title="all-day day1"),
        ]
        out = upcoming_items(entries, TODAY)
        self.assertEqual([i["title"] for i in out],
                         ["all-day day1", "timed 23:00 KST day1", "shutdown day3", "all-day day5"])
        starts = [i["start"] for i in out]
        self.assertEqual(starts, sorted(starts))

    def test_ties_broken_by_game_then_title(self):
        entries = [ent("b", C.EVENT, day(1), game="Zeta", title="a"),
                   ent("a", C.EVENT, day(1), game="Alpha", title="b"),
                   ent("c", C.EVENT, day(1), game="Alpha", title="a")]
        out = upcoming_items(entries, TODAY)
        self.assertEqual([(i["game"], i["title"]) for i in out],
                         [("Alpha", "a"), ("Alpha", "b"), ("Zeta", "a")])


class WindowTest(unittest.TestCase):
    def test_default_14_day_window_inclusive(self):
        entries = [ent("in0", C.EVENT, day(0), title="today"),
                   ent("in14", C.MAINT, day(14), title="d14"),
                   ent("out15", C.EVENT, day(15), title="d15"),
                   ent("past", C.EVENT, day(-1), title="yesterday")]
        p = alerts_payload(entries, TODAY)
        self.assertEqual(gids(p), ["today", "d14"])
        self.assertEqual([i["d_day"] for i in p["items"]], [0, 14])

    def test_custom_days(self):
        entries = [ent("a", C.EVENT, day(3), title="d3"), ent("b", C.EVENT, day(4), title="d4")]
        self.assertEqual(gids(alerts_payload(entries, TODAY, days=3)), ["d3"])
        self.assertEqual(gids(alerts_payload(entries, TODAY, days=10)), ["d3", "d4"])

    def test_timed_item_uses_kst_date_for_window(self):
        # 2026-11-04 23:30 UTC = 2026-11-05 08:30 KST (d_day 1)
        e = ent("m", C.MAINT, dt.datetime(2026, 11, 4, 23, 30, tzinfo=UTC), all_day=False, title="late")
        p = alerts_payload([e], TODAY)
        self.assertEqual(p["items"][0]["d_day"], 1)
        self.assertEqual(p["items"][0]["start"], "2026-11-05T08:30:00+09:00")

    def test_notices_and_unknown_categories_excluded(self):
        entries = [ent("n", C.EVENT, day(1), is_notice=True, title="notice"),
                   ent("o", C.OTHER, day(1), title="other"),
                   ent("p", C.PATCH, day(1), title="patch notice", is_notice=True)]
        self.assertEqual(alerts_payload(entries, TODAY)["items"], [])

    def test_patch_kind_when_dated(self):
        it = alerts_payload([ent("p", C.PATCH, day(2), title="dated patch")], TODAY)["items"][0]
        self.assertEqual(it["kind"], "patch")


class ShutdownTest(unittest.TestCase):
    def test_shutdown_within_60_days_beyond_14(self):
        entries = [ent("s30", C.SHUTDOWN, day(30), title="s30"),
                   ent("s60", C.SHUTDOWN, day(60), title="s60"),
                   ent("s61", C.SHUTDOWN, day(61), title="s61")]
        p = alerts_payload(entries, TODAY)
        self.assertEqual(gids(p), ["s30", "s60"])
        self.assertEqual([i["d_day"] for i in p["items"]], [30, 60])
        self.assertEqual([i["kind"] for i in p["items"]], ["shutdown", "shutdown"])
        validate(p)

    def test_shutdown_dday_zero_and_past_excluded(self):
        entries = [ent("s0", C.SHUTDOWN, day(0), title="today"), ent("sp", C.SHUTDOWN, day(-1), title="gone")]
        p = alerts_payload(entries, TODAY)
        self.assertEqual(gids(p), ["today"])
        self.assertEqual(p["items"][0]["d_day"], 0)

    def test_shutdown_window_not_shrunk_by_days(self):
        entries = [ent("s", C.SHUTDOWN, day(20), title="s20")]
        self.assertEqual(gids(alerts_payload(entries, TODAY, days=1)), ["s20"])

    def test_undated_shutdown_notice_excluded(self):
        entries = [ent("s", C.SHUTDOWN, day(1), is_notice=True, title="undated")]
        self.assertEqual(alerts_payload(entries, TODAY)["items"], [])


class EmptyTest(unittest.TestCase):
    def test_empty_entries(self):
        p = alerts_payload([], TODAY)
        self.assertEqual(p, {"version": 1, "generated_for": "2026-11-04", "items": []})
        validate(p)

    def test_cli_without_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "gamecal.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"state_file": "state.json"}, f)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["alerts", "--json", "--config", cfg])
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            validate(out)
            self.assertEqual(out["items"], [])
            self.assertFalse(os.path.exists(os.path.join(d, "state.json")))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.cfg = os.path.join(d, "gamecal.json")
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"state_file": "state.json", "cache_dir": "c", "out_dir": "out"}, f)
        self.today = dt.datetime.now(UTC).astimezone(KST).date()

    def tearDown(self):
        self.tmp.cleanup()

    def write_state(self, entries):
        state = {"seen": {}, "entries": {e.gid: e.to_dict() for e in entries}, "alerts_sent": {}}
        save_state(os.path.join(self.tmp.name, "state.json"), state)

    def run_cli(self, *args):
        buf = io.StringIO()
        # 네트워크를 쓰면 실패하도록 막습니다 (alerts는 state만 읽어야 함)
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network")), \
                contextlib.redirect_stdout(buf):
            rc = main(["alerts", "--json", "--config", self.cfg, *args])
        return rc, json.loads(buf.getvalue())

    def test_cli_outputs_window_and_shutdowns(self):
        t = self.today
        self.write_state([
            ent("e1", C.EVENT, t + dt.timedelta(days=2), end=t + dt.timedelta(days=3), title="여름 이벤트",
                game="Dota 2", appid=570, url="https://s/e1"),
            ent("m1", C.MAINT, dt.datetime.combine(t + dt.timedelta(days=5), dt.time(1), tzinfo=UTC),
                end=dt.datetime.combine(t + dt.timedelta(days=5), dt.time(3), tzinfo=UTC),
                all_day=False, title="점검", game="CS2", appid=730),
            ent("far", C.EVENT, t + dt.timedelta(days=20), title="too far"),
            ent("s1", C.SHUTDOWN, t + dt.timedelta(days=40), title="종료 40일", game="Old", appid=9),
        ])
        rc, out = self.run_cli("--days", "7")
        self.assertEqual(rc, 0)
        validate(out)
        self.assertEqual(out["generated_for"], t.isoformat())
        self.assertEqual([i["title"] for i in out["items"]], ["여름 이벤트", "점검", "종료 40일"])
        self.assertEqual([i["d_day"] for i in out["items"]], [2, 5, 40])
        self.assertEqual(out["items"][0]["url"], "https://s/e1")
        self.assertIsNone(out["items"][1]["url"])

    def test_cli_requires_json_flag(self):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                main(["alerts", "--config", self.cfg])
        self.assertEqual(cm.exception.code, 2)

    def test_cli_rejects_nonpositive_days(self):
        for bad in ("0", "-3", "x"):
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    main(["alerts", "--json", "--config", self.cfg, "--days", bad])
            self.assertEqual(cm.exception.code, 2, bad)


class OngoingTest(unittest.TestCase):
    # Entry.end는 포함(inclusive) 마지막 날짜이고, 출력 end는 그 다음 날 00:00 (배타적)입니다.
    def test_started_last_week_ends_in_two_days(self):
        p = alerts_payload([ent("o", C.EVENT, day(-7), end=day(1), title="ongoing")], TODAY)
        self.assertEqual(len(p["items"]), 1)
        it = p["items"][0]
        self.assertTrue(it["ongoing"])
        self.assertEqual((it["d_day"], it["ends_in_days"]), (-7, 2))
        self.assertEqual(it["end"], "2026-11-06T00:00:00+09:00")
        validate(p)

    def test_last_day_today_included(self):
        out = alerts_payload([ent("o", C.EVENT, day(-2), end=day(0), title="t")], TODAY)["items"]
        self.assertEqual([(i["ongoing"], i["ends_in_days"]) for i in out], [(True, 1)])

    def test_ended_yesterday_excluded(self):
        self.assertEqual(alerts_payload([ent("o", C.EVENT, day(-5), end=day(-1))], TODAY)["items"], [])

    def test_past_single_day_excluded(self):
        self.assertEqual(alerts_payload([ent("o", C.EVENT, day(-1))], TODAY)["items"], [])

    def test_ends_in_window_boundary(self):
        inside = ent("a", C.EVENT, day(-30), end=day(13), title="inside")   # ends_in_days 14
        outside = ent("b", C.EVENT, day(-30), end=day(14), title="outside")  # ends_in_days 15
        self.assertEqual(gids(alerts_payload([inside, outside], TODAY)), ["inside"])
        self.assertEqual(sorted(gids(alerts_payload([inside, outside], TODAY, days=15))), ["inside", "outside"])

    def test_future_items_not_ongoing(self):
        it = alerts_payload([ent("f", C.EVENT, day(3), end=day(5))], TODAY)["items"][0]
        self.assertFalse(it["ongoing"])
        self.assertEqual((it["d_day"], it["ends_in_days"]), (3, 6))

    def test_timed_ongoing_boundary(self):
        start = dt.datetime.combine(day(-1), dt.time(1), tzinfo=UTC)          # KST 어제 10:00
        at_midnight = dt.datetime.combine(day(-1), dt.time(15), tzinfo=UTC)   # KST 오늘 00:00 (이미 끝남)
        after = dt.datetime.combine(day(-1), dt.time(17), tzinfo=UTC)         # KST 오늘 02:00 (진행 중)
        out = alerts_payload([ent("t1", C.MAINT, start, end=at_midnight, all_day=False, title="ended"),
                              ent("t2", C.MAINT, start, end=after, all_day=False, title="running")], TODAY)["items"]
        self.assertEqual(gids({"items": out}), ["running"])
        self.assertEqual((out[0]["ongoing"], out[0]["d_day"], out[0]["ends_in_days"]), (True, -1, 0))
        validate(alerts_payload([ent("t2", C.MAINT, start, end=after, all_day=False)], TODAY))

    def test_timed_past_without_end_excluded(self):
        start = dt.datetime.combine(day(-1), dt.time(1), tzinfo=UTC)
        self.assertEqual(alerts_payload([ent("t", C.MAINT, start, all_day=False)], TODAY)["items"], [])

    def test_shutdowns_not_ongoing(self):
        out = alerts_payload([ent("s", C.SHUTDOWN, day(-3), title="past"),
                              ent("s0", C.SHUTDOWN, day(0), title="today")], TODAY)["items"]
        self.assertEqual(gids({"items": out}), ["today"])
        self.assertFalse(out[0]["ongoing"])
        self.assertEqual((out[0]["d_day"], out[0]["ends_in_days"]), (0, 1))

    def test_validate_ongoing_consistency(self):
        s = "2026-10-28T00:00:00+09:00"
        good = item(start=s, end="2026-11-06T00:00:00+09:00", d_day=-7)
        self.assertTrue(good["ongoing"])
        validate(base_payload([good]))
        bad = [
            item(start=s, end="2026-11-06T00:00:00+09:00", d_day=-7, ongoing=False),
            item(start=s, end="2026-11-04T00:00:00+09:00", d_day=-7, ends_in_days=0),
            item(start=s, end=None, d_day=-7, ends_in_days=None),
            item(start=s, end="2026-11-06T00:00:00+09:00", d_day=-7, ends_in_days=5),
            item(start="2026-11-05T00:00:00+09:00", end="2026-11-06T00:00:00+09:00", d_day=1, ongoing=True),
        ]
        for obj in bad:
            with self.assertRaises(ValueError, msg=str(obj)):
                validate(base_payload([obj]))


if __name__ == "__main__":
    unittest.main()


class ListHintAndDefaultPathTests(unittest.TestCase):
    def test_list_empty_prints_hint(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "gamecal.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"state_file": "state.json"}, f)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["list", "--config", cfg]), 0)
            self.assertIn("gamecal add", buf.getvalue())

    def test_default_config_path(self):
        from gamecal.config import default_config_path
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(default_config_path(env={"GAMECAL_HOME": d}, cwd=d), os.path.join(d, "gamecal.json"))
            p = default_config_path(env={}, cwd=d)
            self.assertTrue(p.endswith(os.path.join(".gamecal", "gamecal.json")))
            open(os.path.join(d, "gamecal.json"), "w").close()
            if not os.path.exists(p):
                self.assertEqual(default_config_path(env={}, cwd=d), os.path.join(d, "gamecal.json"))
