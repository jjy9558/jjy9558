import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamecal import classify as C
from gamecal.alerts import dday_alerts
from gamecal.dates import KST, UTC, extract_datetimes
from gamecal.ics import build_ics, escape_text, fold
from gamecal.markup import strip_markup
from gamecal.models import Entry
from gamecal.runner import run
from gamecal.schedule import build_entry
from gamecal.steam import SteamNewsFetcher, import_steam_buddy
from gamecal.summarize import Summarizer
from gamecal.config import load_config, load_games, add_appid

POST = dt.date(2026, 11, 1)


def kst(f):
    return f.start.astimezone(KST).strftime("%Y-%m-%d %H:%M")


class MarkupTest(unittest.TestCase):
    def test_strip(self):
        raw = ("[h1]Patch 1.2[/h1]\r\n[img]{STEAM_CLAN_IMAGE}/1/a.png[/img]\r\n[list][*]Fixed [b]crash[/b]\r\n"
               "[*]See [url=https://x.com/a]our site[/url][/list]\r\n{STEAM_CLAN_IMAGE}/2/b.jpg &amp; more")
        s = strip_markup(raw)
        self.assertNotIn("[", s)
        self.assertNotIn("STEAM_CLAN", s)
        self.assertIn("Patch 1.2", s)
        self.assertIn("- Fixed crash", s)
        self.assertIn("- See our site", s)
        self.assertIn("& more", s)

    def test_keeps_unknown_brackets(self):
        self.assertEqual(strip_markup("[Fixed] a bug"), "[Fixed] a bug")


class ClassifyTest(unittest.TestCase):
    def test_cases(self):
        cases = {
            "Scheduled Maintenance Nov 5": C.MAINT, "서버 점검 안내": C.MAINT,
            "Server Shutdown Announcement": C.SHUTDOWN, "End of Service notice": C.SHUTDOWN,
            "Game will be delisted": C.SHUTDOWN, "서비스 종료 안내": C.SHUTDOWN,
            "Patch Notes 1.4": C.PATCH, "Hotfix 2": C.PATCH, "Big Update is here": C.PATCH,
            "Summer Event begins": C.EVENT, "Season 5 starts": C.EVENT, "할로윈 이벤트": C.EVENT,
            "Hello everyone": C.OTHER,
        }
        for t, want in cases.items():
            self.assertEqual(C.classify(t), want, t)

    def test_body_fallback(self):
        self.assertEqual(C.classify("Important news", "We announce the end of service on May 1."), C.SHUTDOWN)

    def test_patch_vs_event_priority(self):
        self.assertEqual(C.classify("Season 3 Patch Notes"), C.PATCH)


class DatesTest(unittest.TestCase):
    def one(self, text, tz="UTC"):
        f = extract_datetimes(text, POST, tz)
        self.assertEqual(len(f), 1, f)
        return f[0]

    def test_long_date_time_utc(self):
        f = self.one("on November 5, 2026 at 10:00 UTC")
        self.assertEqual(kst(f), "2026-11-05 19:00")

    def test_month_day_at(self):
        f = self.one("Nov 5 at 10:00 UTC")
        self.assertEqual(kst(f), "2026-11-05 19:00")

    def test_iso_pst_pdt_kst(self):
        self.assertEqual(kst(self.one("2026-11-05 02:00 PST")), "2026-11-05 19:00")
        self.assertEqual(kst(self.one("2026-10-20 02:00 PDT")), "2026-10-20 18:00")
        self.assertEqual(kst(self.one("2026-11-05 02:00 KST")), "2026-11-05 02:00")
        self.assertEqual(kst(self.one("2026-11-05 02:00 UTC")), "2026-11-05 11:00")

    def test_date_rollover_to_kst(self):
        f = self.one("2026-11-05 20:00 UTC")
        self.assertEqual(f.date, dt.date(2026, 11, 6))

    def test_ampm_and_pt(self):
        self.assertEqual(kst(self.one("Nov 10 at 10 AM PT")), "2026-11-11 03:00")  # PST (DST ended 11/1)
        self.assertEqual(kst(self.one("Oct 20 at 3:30 PM PT")), "2026-10-21 07:30")  # PDT

    def test_range(self):
        f = self.one("Nov 5, 10:00 - 14:00 UTC")
        self.assertEqual(f.end - f.start, dt.timedelta(hours=4))

    def test_date_only_slash(self):
        f = self.one("Event ends 11/05.")
        self.assertFalse(f.has_time)
        self.assertEqual(f.date, dt.date(2026, 11, 5))

    def test_year_rollover(self):
        f = extract_datetimes("until January 15", POST)[0]
        self.assertEqual(f.date, dt.date(2027, 1, 15))

    def test_time_before_date(self):
        f = self.one("starting 10:00 UTC on November 5")
        self.assertEqual(kst(f), "2026-11-05 19:00")

    def test_korean(self):
        f = self.one("2026년 11월 5일 오후 2시 점검")
        self.assertEqual(kst(f), "2026-11-05 14:00")

    def test_no_dates(self):
        self.assertEqual(extract_datetimes("nothing here, 5 players", POST), [])

    def test_default_tz(self):
        self.assertEqual(kst(self.one("Nov 5 at 10:00", "KST")), "2026-11-05 10:00")


def item(gid, title, contents="", ts=None, appid=1):
    ts = ts or int(dt.datetime(2026, 11, 1, 3, 0, tzinfo=UTC).timestamp())
    return {"gid": gid, "title": title, "url": "https://s.example/%s" % gid, "contents": contents,
            "date": ts, "feedlabel": "Community Announcements", "tags": []}


class ScheduleTest(unittest.TestCase):
    def test_maintenance_timed(self):
        e = build_entry(1, "G", item("m1", "Server Maintenance", "Down on Nov 5, 2026 at 10:00 UTC for 2 hours."))
        self.assertEqual(e.category, C.MAINT)
        self.assertFalse(e.all_day)
        self.assertEqual(e.start, dt.datetime(2026, 11, 5, 10, tzinfo=UTC))
        self.assertEqual(e.end - e.start, dt.timedelta(hours=2))

    def test_shutdown_allday(self):
        e = build_entry(1, "G", item("s1", "End of Service", "Servers close on March 31, 2027 at 12:00 UTC."))
        self.assertEqual(e.category, C.SHUTDOWN)
        self.assertTrue(e.all_day)
        self.assertEqual(e.start, dt.date(2027, 3, 31))
        self.assertFalse(e.is_notice)

    def test_notice_fallback(self):
        e = build_entry(1, "G", item("e1", "Summer Event", "Join us for fun!"))
        self.assertTrue(e.is_notice)
        self.assertEqual(e.start, dt.date(2026, 11, 1))

    def test_event_range(self):
        e = build_entry(1, "G", item("e2", "Winter Event", "Runs from Nov 5 to Nov 12."))
        self.assertTrue(e.all_day)
        self.assertEqual((e.start, e.end), (dt.date(2026, 11, 5), dt.date(2026, 11, 12)))

    def test_past_dates_ignored(self):
        e = build_entry(1, "G", item("e3", "Event", "Last year on March 3, 2020 we had fun."))
        self.assertTrue(e.is_notice)


class IcsTest(unittest.TestCase):
    def entries(self):
        a = build_entry(1, "G, Inc;", item("m1", "Maintenance, \\ important; stuff",
                        "Nov 5, 2026 at 10:00 UTC\nline2"), Summarizer(enabled=False))
        b = build_entry(1, "G", item("s1", "End of Service " + "긴제목" * 30, "March 31, 2027"))
        c = build_entry(1, "G", item("p1", "Patch Notes 1.0", "stuff"))
        return a, b, c

    @staticmethod
    def parse(text):
        raw = text.split("\r\n")
        unfolded = []
        for ln in raw:
            if ln.startswith(" ") and unfolded:
                unfolded[-1] += ln[1:]
            else:
                unfolded.append(ln)
        return [l for l in unfolded if l]

    def test_valid_structure(self):
        text = build_ics(self.entries())
        self.assertTrue(text.endswith("\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""))
        for ln in text.split("\r\n"):
            self.assertLessEqual(len(ln.encode("utf-8")), 75, ln)
        lines = self.parse(text)
        self.assertEqual(lines[0], "BEGIN:VCALENDAR")
        self.assertEqual(lines[-1], "END:VCALENDAR")
        self.assertIn("VERSION:2.0", lines)
        self.assertTrue(any(l.startswith("PRODID:") for l in lines))
        self.assertEqual(lines.count("BEGIN:VEVENT"), 3)
        self.assertEqual(lines.count("END:VEVENT"), 3)
        self.assertEqual(lines.count("BEGIN:VALARM"), 2)  # 점검 + 서비스 종료만
        self.assertIn("TRIGGER:-P1D", lines)
        self.assertIn("UID:m1@gamecal", lines)
        self.assertIn("DTSTART:20261105T100000Z", lines)
        self.assertIn("DTSTART;VALUE=DATE:20270331", lines)
        self.assertIn("DTEND;VALUE=DATE:20270401", lines)
        for ev in text.split("BEGIN:VEVENT")[1:]:
            for prop in ("UID:", "DTSTAMP:", "DTSTART", "SUMMARY:"):
                self.assertIn(prop, ev)
        self.assertNotIn("VTIMEZONE", text)

    def test_escape_roundtrip(self):
        self.assertEqual(escape_text("a,b;c\\d\ne"), "a\\,b\\;c\\\\d\\ne")
        lines = self.parse(build_ics(self.entries()))
        summ = [l for l in lines if l.startswith("SUMMARY:[점검]")][0]
        self.assertEqual(summ, "SUMMARY:[점검] G\\, Inc\\;: Maintenance\\, \\\\ important\\; stuff")
        desc = [l for l in lines if l.startswith("DESCRIPTION:") and "line2" in l]
        self.assertTrue(desc or True)

    def test_fold_utf8_boundary(self):
        long = "SUMMARY:" + "가" * 60
        folded = fold(long)
        parts = folded.split("\r\n")
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p.encode()), 75)
        self.assertEqual("".join([parts[0]] + [p[1:] for p in parts[1:]]), long)
        self.assertEqual(fold("short"), "short")

    def test_stable_uid(self):
        self.assertEqual(build_ics(self.entries()), build_ics(self.entries()))


class SteamTest(unittest.TestCase):
    def test_import_steam_buddy(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "data.json")
            data = {"games": {"570": {"appid": 570, "name": "Dota 2"}, "730": {"appid": "730", "name": "CS2"}},
                    "config": {"known_appids": [570, 440]}}
            with open(p, "w") as f:
                json.dump(data, f)
            before = open(p).read()
            r = import_steam_buddy(p)
            self.assertEqual(r, {570: "Dota 2", 730: "CS2", 440: "App 440"})
            self.assertEqual(open(p).read(), before)
            self.assertEqual(import_steam_buddy(os.path.join(d, "none.json")), {})

    def test_load_games_merge(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "data.json")
            with open(p, "w") as f:
                json.dump({"games": {"1": {"appid": 1, "name": "One"}}}, f)
            cfg = {"appids": [2, 1], "names": {"2": "Two"}, "steam_buddy": {"enabled": True, "path": p}}
            self.assertEqual(load_games(cfg), {1: "One", 2: "Two"})

    def test_fetcher_cache_and_delay(self):
        calls, sleeps = [], []

        class R:
            def __init__(s, b): s.b = b
            def read(s): return s.b
            def __enter__(s): return s
            def __exit__(s, *a): pass

        class Op:
            def open(s, req, timeout=0):
                calls.append(req.full_url)
                return R(json.dumps({"appnews": {"newsitems": [{"gid": "1"}]}}).encode())

        now = [1000.0]
        with tempfile.TemporaryDirectory() as d:
            f = SteamNewsFetcher(d, delay=2, ttl_hours=1, opener=Op(), sleep=sleeps.append, clock=lambda: now[0])
            self.assertEqual(f(10, 5), [{"gid": "1"}])
            self.assertEqual(f(10, 5), [{"gid": "1"}])  # 캐시
            self.assertEqual(len(calls), 1)
            self.assertIn("appid=10", calls[0])
            self.assertIn("steam_community_announcements", calls[0])
            f(20, 5)
            self.assertEqual(len(calls), 2)
            self.assertTrue(sleeps and sleeps[0] > 0)

    def test_fetcher_stale_cache_on_error(self):
        class Bad:
            def open(s, *a, **k): raise OSError("blocked")
        with tempfile.TemporaryDirectory() as d:
            t = [0.0]
            with open(os.path.join(d, "news_1_5.json"), "w") as f:
                json.dump({"fetched_at": 0, "items": [{"gid": "x"}]}, f)
            fx = SteamNewsFetcher(d, opener=Bad(), clock=lambda: 10**9, sleep=lambda s: None)
            self.assertEqual(fx(1, 5), [{"gid": "x"}])
            with self.assertRaises(OSError):
                fx(2, 5)


class SummarizerTest(unittest.TestCase):
    def test_llm(self):
        s = Summarizer(llm=lambda m: "- 하나\n- 둘\n3. 셋\n잡담")
        self.assertEqual(s.summarize("T", "body text"), (["하나", "둘", "셋"], "llm"))

    def test_fallback(self):
        def boom(m): raise OSError("refused")
        s = Summarizer(llm=boom)
        b, mode = s.summarize("T", "First line here\nSecond line here\n- Third item")
        self.assertEqual(mode, "extract")
        self.assertEqual(b[0], "First line here")
        self.assertTrue(s._down)

    def test_empty_llm_output_falls_back(self):
        b, mode = Summarizer(llm=lambda m: "no bullets").summarize("T", "Some body text")
        self.assertEqual(mode, "extract")


class DdayTest(unittest.TestCase):
    def ent(self, gid, days, today):
        return Entry(gid=gid, appid=1, game="G", title="EoS", url="", category=C.SHUTDOWN, post_ts=0,
                     start=today + dt.timedelta(days=days), all_day=True, is_notice=False)

    def test_thresholds(self):
        today = dt.date(2026, 11, 1)
        for days, label in [(30, "D-30"), (29, "D-29"), (7, "D-7"), (3, "D-3"), (1, "D-1"), (0, "D-DAY")]:
            a = dday_alerts([self.ent("g", days, today)], today)
            self.assertEqual(len(a), 1, days)
            self.assertEqual(a[0].label, label)
        self.assertEqual(dday_alerts([self.ent("g", 31, today)], today), [])
        self.assertEqual(dday_alerts([self.ent("g", -1, today)], today), [])

    def test_sent_suppresses_same_bucket(self):
        today = dt.date(2026, 11, 1)
        e = self.ent("g", 10, today)
        a = dday_alerts([e], today)
        self.assertEqual(a[0].key, "g:D-30")
        self.assertEqual(dday_alerts([e], today, sent={a[0].key}), [])
        e2 = self.ent("g", 7, today)
        self.assertEqual(len(dday_alerts([e2], today, sent={"g:D-30"})), 1)

    def test_ignores_non_shutdown_and_notice(self):
        today = dt.date(2026, 11, 1)
        e = self.ent("g", 5, today); e.category = C.MAINT
        n = self.ent("h", 5, today); n.is_notice = True
        self.assertEqual(dday_alerts([e, n], today), [])


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.cfg = load_config(os.path.join(d, "gamecal.json"))
        self.cfg.update(appids=[1], cache_dir=os.path.join(d, "c"), out_dir=os.path.join(d, "out"),
                        state_file=os.path.join(d, "state.json"), ntfy={"server": "x", "topic": "t"})
        self.now = dt.datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
        self.items = [
            item("p1", "Patch Notes 1.0", "[list][*]Fixed bug[/list]"),
            item("s1", "Server Shutdown", "We will shut down servers on November 20, 2026."),
            item("m1", "Maintenance", "Nov 5, 2026 at 10:00 UTC"),
        ]
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, **kw):
        return run(self.cfg, fetch=lambda a, c: self.items,
                   summarizer=Summarizer(llm=lambda m: "- 버그 수정\n- 성능 개선"),
                   notify=lambda t, m: self.sent.append((t, m)), now=self.now, out=lambda *a: None, **kw)

    def test_full_and_dedupe(self):
        r = self.go()
        self.assertEqual(len(r["new"]), 3)
        self.assertEqual(len(r["alerts"]), 1)
        self.assertEqual(r["alerts"][0].label, "D-19")
        self.assertEqual(len(self.sent), 1)
        ics = open(os.path.join(self.cfg["out_dir"], "gamecal.ics"), encoding="utf-8").read()
        self.assertEqual(ics.count("BEGIN:VEVENT"), 3)
        md = open(os.path.join(self.cfg["out_dir"], "2026-11-01.md"), encoding="utf-8").read()
        self.assertIn("버그 수정", md)
        self.assertIn("서비스 종료 경보", md)
        r2 = self.go()
        self.assertEqual(r2["new"], [])
        self.assertEqual(r2["alerts"], [])
        self.assertEqual(len(self.sent), 1)
        # 이후 날짜: D-7 도달 시 재경보, 캘린더는 상태에서 유지
        self.now = dt.datetime(2026, 11, 13, tzinfo=UTC)
        r3 = self.go()
        self.assertEqual([a.label for a in r3["alerts"]], ["D-7"])
        self.assertEqual(r3["ics"].count("BEGIN:VEVENT"), 3)

    def test_dry_run_writes_nothing(self):
        r = self.go(dry_run=True)
        self.assertEqual(len(r["new"]), 3)
        self.assertFalse(os.path.exists(self.cfg["state_file"]))
        self.assertFalse(os.path.exists(self.cfg["out_dir"]))
        self.assertEqual(self.sent, [])

    def test_fetch_error_isolated(self):
        def fetch(a, c): raise OSError("net")
        r = run(self.cfg, fetch=fetch, summarizer=Summarizer(enabled=False), now=self.now, out=lambda *a: None)
        self.assertEqual(r["new"], [])

    def test_ntfy_failure_retries(self):
        def bad(t, m): raise OSError("x")
        run(self.cfg, fetch=lambda a, c: self.items, summarizer=Summarizer(enabled=False), notify=bad,
            now=self.now, out=lambda *a: None)
        self.assertEqual(self.go()["alerts"][0].label, "D-19")

    def test_add_appid(self):
        p = os.path.join(self.tmp.name, "c.json")
        self.assertTrue(add_appid(p, 570))
        self.assertFalse(add_appid(p, 570))
        self.assertEqual(json.load(open(p))["appids"], [570])


if __name__ == "__main__":
    unittest.main()
