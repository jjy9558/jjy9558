import contextlib
import hashlib
import io
import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tastemap import importers, model, render, stats
from tastemap.cli import main
from tastemap.store import Store
from tastemap.tagdict import TagDict

from helpers import STEAM_GAMES, make_diary, make_steam, make_takeout

NOW = datetime(2026, 12, 20, 12, 0, 0)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.td = TagDict.load()

    def full_store(self):
        s = Store(self.dir / "tm" / "data.json")
        importers.import_steam(s, make_steam(self.dir))
        importers.import_diary(s, make_diary(self.dir))
        importers.import_youtube(s, make_takeout(self.dir))
        return s

    def run_cli(self, *argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["--home", str(self.dir / "home"), *argv], out=buf, now=NOW)
        self.stderr = err.getvalue()
        return code, buf.getvalue()


class ImportTests(Base):
    def test_counts_and_duplicate_watch_merge(self):
        s = Store(self.dir / "d.json")
        r = importers.import_steam(s, make_steam(self.dir))
        self.assertEqual(r["imported"], len(STEAM_GAMES))
        self.assertEqual(r["refunded"], 1)
        self.assertEqual(len(s.steam), len(STEAM_GAMES))
        r = importers.import_youtube(s, make_takeout(self.dir))
        self.assertEqual(r["entries"], 4)       # 광고와 삭제된 영상은 뺀 시청 건수
        self.assertEqual(r["skipped"], 2)
        self.assertEqual(len(s.youtube), 3)     # 같은 영상 2번은 한 항목
        self.assertEqual(sum(importers.watch_count(v) for v in s.youtube.values()), 4)
        counts = sorted(importers.watch_count(v) for v in s.youtube.values())
        self.assertEqual(counts, [1, 1, 2])
        # 같은 파일을 다시 가져와도 합산이 늘지 않는다
        importers.import_youtube(s, make_takeout(self.dir))
        self.assertEqual(sum(importers.watch_count(v) for v in s.youtube.values()), 4)

    def test_diary_aggregates_without_caption_text(self):
        s = Store(self.dir / "d.json")
        r = importers.import_diary(s, make_diary(self.dir))
        self.assertEqual(r["games"], 3)
        self.assertEqual(r["shots"], 5)  # 너무 큰 파일(skip_reason)은 제외
        hades = s.diary["1145360"]
        self.assertEqual(hades["shots"], 3)
        self.assertEqual(hades["tags"]["boss fight"], 2)
        self.assertEqual(hades["moods"]["tense"], 2)
        self.assertEqual(hades["notable"], 1)

    def test_diary_opened_read_only(self):
        p = make_diary(self.dir)
        before = hashlib.sha256(p.read_bytes()).hexdigest()
        importers.import_diary(Store(self.dir / "d.json"), p)
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), before)
        self.assertEqual([x.name for x in self.dir.glob("diary.sqlite3-*")], [])

    def test_bad_inputs_raise_importfailed(self):
        s = Store(self.dir / "d.json")
        bad = self.dir / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        for fn in (importers.import_steam, importers.import_youtube):
            with self.assertRaises(importers.ImportFailed):
                fn(s, bad)
        bad.write_text("{}", encoding="utf-8")
        with self.assertRaises(importers.ImportFailed):
            importers.import_steam(s, bad)
        with self.assertRaises(importers.ImportFailed):
            importers.import_diary(s, self.dir / "none.sqlite")

    def test_compat_used_when_present_examples_ignored(self):
        s = Store(self.dir / "d.json")
        r = importers.import_steam(s, make_steam(self.dir, with_compat=True))
        self.assertTrue(r["compat"])
        self.assertEqual(s.compat, {"7002": {"deck": "unsupported"}})


class PrivacyTests(Base):
    def test_store_keeps_only_title_and_time_for_youtube(self):
        s = self.full_store()
        s.save()
        text = s.path.read_text(encoding="utf-8")
        for secret in ("youtube.com", "youtu.be", "SecretChannel", "UCsecret", "someone@example.com",
                       "Kim_Minsu", "secretname", "aaa111"):
            self.assertNotIn(secret, text)
        for rec in s.youtube.values():
            self.assertEqual(set(rec) - {"undated"}, {"title", "times"})
        self.assertIn("Hades 공략 가이드", text)


class ClusterTests(Base):
    def test_deterministic_and_order_independent(self):
        s = self.full_store()
        a = model.build_map(model.build_units(s, self.td), k=3, seed=7)
        b = model.build_map(model.build_units(s, self.td), k=3, seed=7)
        self.assertEqual(a["labels"], b["labels"])
        self.assertEqual(a["coords"], b["coords"])
        # 저장소 삽입 순서를 뒤집어도 같다
        s2 = Store(self.dir / "other.json")
        for sec in ("steam", "diary", "youtube"):
            for k in reversed(list(s.data[sec])):
                s2.data[sec][k] = s.data[sec][k]
        c = model.build_map(model.build_units(s2, self.td), k=3, seed=7)
        self.assertEqual([u.key for u in a["units"]], [u.key for u in c["units"]])
        self.assertEqual(a["labels"], c["labels"])
        self.assertEqual([c_["members"] for c_ in a["clusters"]], [c_["members"] for c_ in c["clusters"]])

    def test_similar_games_share_cluster(self):
        s = self.full_store()
        m = model.build_map(model.build_units(s, self.td), k=3)
        lab = {u.key: l for u, l in zip(m["units"], m["labels"])}
        self.assertEqual(lab["g:1145360"], lab["g:646570"])
        self.assertNotEqual(lab["g:1145360"], lab["g:9100"])
        self.assertEqual(len(set(lab.values())), m["k"])

    def test_kmeans_edge_cases(self):
        self.assertEqual(model.kmeans([], 3), [])
        self.assertEqual(model.kmeans([[1.0, 0.0]] * 4, 3), [0, 0, 0, 0])
        self.assertEqual(len(set(model.kmeans([[0.0], [0.1], [5.0], [5.1]], 2, seed=1))), 2)

    def test_pca_finds_main_axis(self):
        rows = [[float(i), 2.0 * i, 0.0] for i in range(-3, 4)]
        coords, var = model.pca2(rows)
        self.assertAlmostEqual(var[0], 1.0, places=6)
        xs = [c[0] for c in coords]
        self.assertAlmostEqual(max(xs) - min(xs), 6 * (5 ** 0.5), places=5)
        self.assertEqual(model.pca2([[1.0, 1.0]] * 3)[0], [(0.0, 0.0)] * 3)
        self.assertEqual(model.pca2([]), ([], (0.0, 0.0)))


class HtmlTests(Base):
    EXTERNAL = re.compile(r"https?://|\bsrc\s*=|\bhref\s*=|<link|<script|<img|<iframe|url\(|@import", re.I)

    def test_map_is_self_contained(self):
        s = self.full_store()
        s.steam["777"] = {"appid": 777, "name": "<script>alert(1)</script> & game", "minutes": 60}
        s.manual_tags["g:777"] = ["roguelike"]
        units = model.build_units(s, self.td)
        m = model.build_map(units, k=3)
        html = render.render_map(m, model.profile_lines(units, self.td), self.td)
        self.assertIsNone(self.EXTERNAL.search(html), self.EXTERNAL.search(html))
        self.assertIn("<svg", html)
        self.assertIn("prefers-color-scheme:dark", html)
        self.assertIn('name="viewport"', html)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert(1)", html)
        self.assertEqual(html.count("<li>") >= 5, True)

    def test_wrapped_and_career_self_contained(self):
        s = self.full_store()
        units = model.build_units(s, self.td)
        w = render.render_wrapped(stats.wrapped_stats(s, 2026, NOW))
        c = render.render_career(stats.career_stats(s, units, self.td, "<b>닉</b>", NOW))
        for html in (w, c):
            self.assertIsNone(self.EXTERNAL.search(html))
        self.assertIn("&lt;b&gt;닉", c)
        self.assertNotIn("<b>닉", c)


class RankTests(Base):
    def test_high_satisfaction_genre_ranks_above_low(self):
        s = self.full_store()
        units = model.build_units(s, self.td)
        cands = model.parse_candidates(
            "# 후보\n1794680\n7001,Soccer Pro,sports\n7002\n105600\n1145360 junk\nabc\n7001,dup\n", self.td)
        self.assertEqual([c["appid"] for c in cands], [1794680, 7001, 7002, 105600])
        rows = model.rank_candidates(cands, units, self.td, s)
        order = [r["appid"] for r in rows]
        self.assertEqual(order[0], 1794680)                 # 로그라이크(만족도 높음)
        self.assertLess(order.index(1794680), order.index(7001))
        scored = [r for r in rows if r["score"] is not None]
        self.assertEqual(scored[-1]["appid"], 7001)         # 스포츠(만족도 1)는 점수 있는 것 중 꼴찌
        self.assertLess(scored[-1]["score"], 0)
        self.assertIsNone(rows[-1]["score"])                # 태그 없는 후보는 맨 아래
        self.assertEqual(rows[-1]["appid"], 7002)
        self.assertIn("roguelike", rows[0]["why"])

    def test_handheld_penalty_uses_compat(self):
        s = Store(self.dir / "d.json")
        importers.import_steam(s, make_steam(self.dir, with_compat=True))
        units = model.build_units(s, self.td)
        cands = [{"appid": 7002, "name": "Deck Hater", "tags": ["roguelike"]}]
        plain = model.rank_candidates(cands, units, self.td, s)[0]["score"]
        pen = model.rank_candidates(cands, units, self.td, s, handheld=True)[0]["score"]
        self.assertAlmostEqual(plain - pen, 0.2)

    def test_manual_tags_affect_vectors(self):
        s = self.full_store()
        before = {u.key: u for u in model.build_units(s, self.td)}["g:999001"]
        self.assertEqual(before.vec, {})
        s.manual_tags["g:999001"] = ["puzzle"]
        after = {u.key: u for u in model.build_units(s, self.td)}["g:999001"]
        self.assertEqual(after.vec, {"puzzle": 1.0})


class WrappedTests(Base):
    def test_totals_match_input(self):
        s = self.full_store()
        w = stats.wrapped_stats(s, None, NOW)
        self.assertEqual(w["count"], len(STEAM_GAMES))
        self.assertEqual(w["minutes"], sum(g[3] for g in STEAM_GAMES.values()))
        self.assertEqual(w["spend"], sum(g[2] for g in STEAM_GAMES.values()))
        self.assertAlmostEqual(w["hours"], w["minutes"] / 60)
        w26 = stats.wrapped_stats(s, 2026, NOW)
        expect = [g for g in STEAM_GAMES.values() if g[1].startswith("2026")]
        self.assertEqual(w26["count"], len(expect))
        self.assertEqual(w26["minutes"], sum(g[3] for g in expect))
        self.assertEqual(w26["spend"], sum(g[2] for g in expect))
        html = render.render_wrapped(w26)
        self.assertIn(f"{w26['hours']:,.1f}시간", html)
        self.assertIn(f"{w26['spend']:,.0f}원", html)

    def test_best_worst_shelved(self):
        w = stats.wrapped_stats(self.full_store(), 2026, NOW)
        per = {g[0]: g[2] / (g[3] / 60) for g in STEAM_GAMES.values() if g[3] and g[1].startswith("2026")}
        self.assertEqual(w["best"]["name"], min(per, key=per.get))
        self.assertEqual(w["worst"]["name"], max(per, key=per.get))
        self.assertEqual(w["shelved"]["name"], "Mystery Game")
        self.assertEqual(w["shelved"]["days"], (NOW - datetime(2026, 1, 2, 12)).days)
        self.assertEqual(w["unplayed"], 1)
        self.assertEqual(w["videos_watched"], 3)  # 2026년에 본 영상 시청 3회 (2025-12-30 것은 제외)

    def test_year_end_cap_for_past_years(self):
        s = self.full_store()
        w = stats.wrapped_stats(s, 2026, datetime(2027, 6, 1))
        self.assertEqual(w["shelved"]["days"], (datetime(2026, 12, 31, 23, 59, 59) - datetime(2026, 1, 2, 12)).days)

    def test_career(self):
        s = self.full_store()
        c = stats.career_stats(s, model.build_units(s, self.td), self.td, "닉", NOW)
        total_h = sum(g[3] for g in STEAM_GAMES.values()) / 60
        self.assertAlmostEqual(c["hours"], total_h)
        self.assertEqual(c["rank"], "사원")  # 353시간
        self.assertEqual(c["titles"], len(STEAM_GAMES))
        self.assertEqual(c["five_star"], 3)
        self.assertEqual(c["projects"][0]["name"], "Elden Ring")
        self.assertTrue(c["fields"])
        self.assertIn("2025.12", c["period"])
        html = render.render_career(c)
        self.assertNotIn("25,000", html)  # 가격 같은 금융 정보는 넣지 않는다


class MissingFieldTests(Base):
    def test_minimal_records(self):
        p = self.dir / "min.json"
        p.write_text(json.dumps({"games": {
            "1": {"appid": 1, "name": "OnlyName"},
            "2": {"name": "No Appid Field", "minutes": "x", "rating": 9, "price_paid": None},
            "3": {"appid": 3},
            "4": "junk"}}), encoding="utf-8")
        s = Store(self.dir / "d.json")
        r = importers.import_steam(s, p)
        self.assertEqual(r["imported"], 2)
        self.assertEqual(r["skipped"], 2)
        units = model.build_units(s, self.td)
        self.assertEqual(len(units), 2)
        self.assertEqual(len(model.profile_lines(units, self.td)), 5)
        w = stats.wrapped_stats(s, None, NOW)
        self.assertEqual((w["count"], w["minutes"], w["spend"]), (2, 0, 0))
        self.assertIsNone(w["best"])
        self.assertEqual(w["unplayed"], 2)
        render.render_wrapped(w)
        render.render_career(stats.career_stats(s, units, self.td, "", NOW))

    def test_empty_everything(self):
        s = Store(self.dir / "d.json")
        units = model.build_units(s, self.td)
        self.assertEqual(units, [])
        lines = model.profile_lines(units, self.td)
        self.assertEqual(len(lines), 5)
        m = model.build_map(units)
        self.assertEqual(m["units"], [])
        self.assertEqual(stats.wrapped_stats(s, 2026, NOW)["count"], 0)

    def test_diary_only_and_video_only(self):
        s = Store(self.dir / "d.json")
        importers.import_diary(s, make_diary(self.dir))
        units = model.build_units(s, self.td)
        self.assertEqual(len(model.profile_lines(units, self.td)), 5)
        m = model.build_map(units)
        self.assertTrue(m["units"])
        render.render_map(m, model.profile_lines(units, self.td), self.td)
        s2 = Store(self.dir / "d2.json")
        importers.import_youtube(s2, make_takeout(self.dir))
        s2.youtube["nodate"] = {"title": "roguelike no time", "times": []}
        units = model.build_units(s2, self.td)
        self.assertEqual(len(model.profile_lines(units, self.td)), 5)
        render.render_map(model.build_map(units), model.profile_lines(units, self.td), self.td)

    def test_all_untagged_map_has_no_units(self):
        s = Store(self.dir / "d.json")
        s.steam["1"] = {"appid": 1, "name": "zzz"}
        m = model.build_map(model.build_units(s, self.td))
        self.assertEqual((m["units"], len(m["untagged"])), ([], 1))

    def test_video_cap(self):
        s = Store(self.dir / "d.json")
        importers.import_youtube(s, make_takeout(self.dir, n_extra=30))
        m = model.build_map(model.build_units(s, self.td), max_videos=10)
        self.assertEqual(len([u for u in m["units"] if u.kind == "video"]), 10)
        self.assertGreater(m["hidden_videos"], 0)


class ProfileTests(Base):
    def test_five_lines_with_content(self):
        s = self.full_store()
        lines = model.profile_lines(model.build_units(s, self.td), self.td)
        self.assertEqual(len(lines), 5)
        self.assertIn("로그라이크", lines[0])
        self.assertIn("Elden Ring", lines[1])
        self.assertIn("스크린샷", lines[3])
        self.assertIn("스포츠", lines[4])

    def test_llm_polish_validates(self):
        lines = ["가 1", "나 2", "다 3", "라 4", "마 5"]

        class Stub:
            def __init__(self, ans):
                self.ans = ans

            def chat_json(self, messages, **kw):
                if isinstance(self.ans, Exception):
                    raise self.ans
                return self.ans

        from tastemap._jjkit import llm
        good = {"lines": ["A 1", "B 2", "C 3", "D 4", "E 5"]}
        self.assertEqual(model.polish_with_llm(lines, Stub(good)), good["lines"])
        self.assertEqual(model.polish_with_llm(lines, Stub({"lines": ["x"]})), lines)
        self.assertEqual(model.polish_with_llm(lines, Stub({"lines": ["A 9", "B 2", "C 3", "D 4", "E 5"]})), lines)  # 숫자 바뀜
        self.assertEqual(model.polish_with_llm(lines, Stub(llm.LLMError("x"))), lines)
        self.assertEqual(model.polish_with_llm(lines, Stub("not dict")), lines)

    def test_no_network_modules(self):
        import tastemap
        src = "".join(p.read_text(encoding="utf-8") for p in Path(tastemap.__file__).parent.glob("*.py"))
        for bad in ("urllib.request", "import socket", "http.client", "requests"):
            self.assertNotIn(bad, src)


class CliTests(Base):
    def test_end_to_end(self):
        steam = make_steam(self.dir, with_compat=True)
        code, out = self.run_cli("import", "steam", str(steam))
        self.assertEqual(code, 0)
        self.assertIn(f"{len(STEAM_GAMES)}개", out)
        self.assertEqual(self.run_cli("import", "diary", str(make_diary(self.dir)))[0], 0)
        self.assertEqual(self.run_cli("import", "youtube", str(make_takeout(self.dir)))[0], 0)
        self.assertEqual(self.run_cli("import", "youtube", str(self.dir / "nope.json"))[0], 1)

        code, out = self.run_cli("status")
        self.assertIn("태그 없는 항목", out)
        code, out = self.run_cli("tags", "edit", "Mystery", "--add", "퍼즐, 로그라이크")
        self.assertEqual(code, 0)
        self.assertIn("퍼즐", out)
        code, out = self.run_cli("tags", "edit", "Mystery", "--remove", "puzzle")
        self.assertIn("로그라이크", out)
        self.assertNotIn("퍼즐", out)
        self.assertEqual(self.run_cli("tags", "edit", "없는게임", "--add", "x")[0], 1)
        self.assertEqual(self.run_cli("tags", "edit", "s", "--add", "x")[0], 1)  # 여러 개에 걸림
        self.assertIn("999001", self.run_cli("tags", "list")[1])
        self.assertIn("roguelike", self.run_cli("tags", "dict")[1])

        mp = self.dir / "out" / "map.html"
        code, out = self.run_cli("map", "--out", str(mp), "--k", "3")
        self.assertEqual(code, 0)
        html1 = mp.read_text(encoding="utf-8")
        self.run_cli("map", "--out", str(mp), "--k", "3")
        self.assertEqual(mp.read_text(encoding="utf-8"), html1)  # 같은 입력이면 같은 파일
        code, out = self.run_cli("profile")
        self.assertEqual(len(out.strip().splitlines()), 5)
        code, out = self.run_cli("profile", "--llm")  # LLM 주소가 없으면 그냥 건너뜀
        self.assertEqual(code, 0)

        cand = self.dir / "appids.txt"
        cand.write_text("1794680\n7001,Soccer Pro,sports\n7002\n", encoding="utf-8")
        code, out = self.run_cli("rank", "--candidates", str(cand))
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("1794680", lines[2])
        self.assertIn("태그 없음", out)
        self.assertEqual(self.run_cli("rank", "--candidates", str(self.dir / "no.txt"))[0], 1)

        wp = self.dir / "w.html"
        code, out = self.run_cli("wrapped", "--year", "2026", "--out", str(wp))
        self.assertEqual(code, 0)
        self.assertIn("2026 게임 결산", wp.read_text(encoding="utf-8"))
        cp = self.dir / "c.html"
        code, out = self.run_cli("career", "--name", "테스터", "--out", str(cp))
        self.assertEqual(code, 0)
        self.assertIn("테스터", cp.read_text(encoding="utf-8"))

    def test_empty_home_errors_cleanly(self):
        for argv in (["map"], ["wrapped"], ["career"]):
            code, _ = self.run_cli(*argv)
            self.assertEqual(code, 1)
            self.assertIn("가져온 데이터가 없어요" if argv[0] == "map" else "스팀 기록이 없어요", self.stderr)

    def test_requires_command(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main([], out=io.StringIO())


if __name__ == "__main__":
    unittest.main()
