import json
import os
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta

from gamediary import cli
from gamediary.album import render_album
from gamediary.config import load_config, DEFAULTS
from gamediary.diary import group_sessions, pick_highlights, render_diary, week_range
from gamediary.llm import caption_image, parse_caption_json, LLMClient, LLMError
from gamediary.sources import image_size, parse_timestamp, scan_sources, game_from_filename
from gamediary.store import Store
from tests.helpers import FakeLLM, make_jpeg, make_png


def shot(game, ts, **kw):
    d = {"path": "/x/%s_%s.png" % (game, ts.strftime("%H%M%S")), "game": game, "ts": ts,
         "caption": "", "mood": "", "tags": [], "notable": False, "width": 1, "height": 1,
         "thumb": None}
    d.update(kw)
    return d


class TimestampTests(unittest.TestCase):
    def test_steam(self):
        self.assertEqual(parse_timestamp("20261101213005_1.jpg"), datetime(2026, 11, 1, 21, 30, 5))

    def test_gamebar(self):
        self.assertEqual(parse_timestamp("Game 2026-11-01 21-30-05.png"), datetime(2026, 11, 1, 21, 30, 5))
        self.assertEqual(game_from_filename("Halo Infinite 2026-11-01 21-30-05.png"), "Halo Infinite")

    def test_nvidia(self):
        self.assertEqual(parse_timestamp("Elden Ring 2026.11.01 - 21.30.05.01.png"),
                         datetime(2026, 11, 1, 21, 30, 5))

    def test_invalid_and_none(self):
        self.assertIsNone(parse_timestamp("20261301213005_1.jpg"))
        self.assertIsNone(parse_timestamp("random.png"))

    def test_image_size(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(image_size(make_png(os.path.join(d, "a.png"), 7, 4)), (7, 4))
            self.assertEqual(image_size(make_jpeg(os.path.join(d, "a.jpg"), 3, 5)), (3, 5))
            bad = os.path.join(d, "b.png")
            open(bad, "wb").write(b"nope")
            self.assertIsNone(image_size(bad))


class ScanTests(unittest.TestCase):
    def test_steam_layout_and_names(self):
        with tempfile.TemporaryDirectory() as d:
            sd = os.path.join(d, "Steam", "userdata", "123", "760", "remote", "570", "screenshots")
            make_jpeg(os.path.join(sd, "20261101213005_1.jpg"))
            make_jpeg(os.path.join(sd, "thumbnails", "20261101213005_1.jpg"))
            make_png(os.path.join(d, "Caps", "Halo 2026-11-01 20-00-00.png"))
            cfg = dict(DEFAULTS, sources=[{"path": os.path.join(d, "Steam"), "kind": "steam"},
                                          {"path": os.path.join(d, "Caps"), "kind": "gamebar"}])
            shots = list(scan_sources(cfg, {"570": "Dota 2"}))
            self.assertEqual(len(shots), 2)
            steam = [s for s in shots if s["kind"] == "steam"][0]
            self.assertEqual((steam["game"], steam["appid"]), ("Dota 2", "570"))
            self.assertTrue(steam["thumb"].endswith(os.path.join("thumbnails", "20261101213005_1.jpg")))
            self.assertEqual(steam["ts"], datetime(2026, 11, 1, 21, 30, 5))
            bar = [s for s in shots if s["kind"] == "gamebar"][0]
            self.assertEqual(bar["game"], "Halo")

    def test_unknown_app_and_mtime_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p = make_png(os.path.join(d, "999", "screenshots", "shot.png"))
            cfg = dict(DEFAULTS, sources=[{"path": d, "kind": "steam"}])
            s = list(scan_sources(cfg, {}))[0]
            self.assertEqual(s["game"], "Steam 앱 999")
            self.assertAlmostEqual(s["ts"].timestamp(), os.path.getmtime(p), delta=2)

    def test_missing_folder_warns(self):
        cfg = dict(DEFAULTS, sources=[{"path": "/no/such/dir", "kind": "folder"}])
        self.assertIn("_warn", list(scan_sources(cfg, {}))[0])


class SessionTests(unittest.TestCase):
    def test_gap_splits(self):
        t = datetime(2026, 11, 1, 20, 0)
        shots = [shot("A", t), shot("A", t + timedelta(minutes=119)),
                 shot("A", t + timedelta(minutes=119 + 121)), shot("B", t + timedelta(minutes=5))]
        ses = group_sessions(shots, 2.0)
        self.assertEqual([(s["game"], len(s["shots"])) for s in ses], [("A", 2), ("B", 1), ("A", 1)])

    def test_highlights(self):
        t = datetime(2026, 11, 1, 20, 0)
        shots = [shot("A", t + timedelta(minutes=i), notable=(i < 8), caption="c%d" % i) for i in range(12)]
        self.assertEqual(len(pick_highlights(shots)), 5)
        few = [shot("A", t + timedelta(minutes=i), notable=(i == 0), caption="c") for i in range(10)]
        self.assertEqual(len(pick_highlights(few)), 3)
        self.assertEqual(len(pick_highlights(few[:2])), 2)


class ParseTests(unittest.TestCase):
    def test_clean(self):
        r = parse_caption_json('{"caption":"a","mood":"m","tags":["x","y"],"notable":true}')
        self.assertEqual((r["caption"], r["tags"], r["notable"]), ("a", ["x", "y"], True))

    def test_fenced_with_prose_and_trailing_comma(self):
        r = parse_caption_json('설명입니다\n```json\n{"caption": "총격전 {중}", "tags": ["a",], "notable": "true",}\n```')
        self.assertEqual(r["caption"], "총격전 {중}")
        self.assertEqual(r["tags"], ["a"])
        self.assertTrue(r["notable"])

    def test_think_block_and_string_tags(self):
        r = parse_caption_json('<think>{"caption":"no"}</think>{"caption":"yes","tags":"#a, #b","notable":"아니"}')
        self.assertEqual((r["caption"], r["tags"], r["notable"]), ("yes", ["a", "b"], False))

    def test_truncated_and_plain(self):
        r = parse_caption_json('{"caption": "잘린 응답", "mood": "평화')
        self.assertEqual(r["caption"], "잘린 응답")
        r = parse_caption_json("그냥 문장입니다")
        self.assertEqual((r["caption"], r["tags"], r["notable"]), ("그냥 문장입니다", [], False))
        self.assertEqual(parse_caption_json("")["caption"], "")

    def test_client_no_proxy_and_error(self):
        c = LLMClient("http://127.0.0.1:1/v1", timeout=1)
        with self.assertRaises(LLMError):
            c.chat([{"role": "user", "content": "hi"}])


class CachingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)  # registered first => runs after store.close cleanups
        d = self.tmp.name
        self.sd = os.path.join(d, "570", "screenshots")
        make_png(os.path.join(self.sd, "20261101200000_1.png"), color=(1, 2, 3))
        make_png(os.path.join(self.sd, "20261101200100_2.png"), color=(1, 2, 3))  # 동일 내용
        make_png(os.path.join(self.sd, "20261101200200_3.png"), color=(9, 9, 9))
        self.cfg = dict(DEFAULTS, sources=[{"path": d, "kind": "steam"}],
                        db_path=os.path.join(d, "db.sqlite3"), out_dir=os.path.join(d, "out"),
                        names_cache="", use_steam_buddy=False, _path=os.path.join(d, "c.json"))

    def test_hash_cache_and_rerun(self):
        store = Store(self.cfg["db_path"])
        self.addCleanup(store.close)
        shots = list(scan_sources(self.cfg, {}))
        st = store.upsert_shots(shots, 10 ** 6)
        self.assertEqual(st["new"], 3)
        self.assertEqual(store.upsert_shots(shots, 10 ** 6)["unchanged"], 3)
        self.assertEqual(len(store.pending()), 2)  # 해시 중복 제거
        fake = FakeLLM()
        import argparse
        args = argparse.Namespace(limit=None, no_llm=False)
        self.assertEqual(cli.cmd_caption(args, self.cfg, client=fake), 0)
        self.assertEqual(len(fake.calls), 2)
        cli.cmd_caption(args, self.cfg, client=fake)  # 재실행: 호출 없음
        self.assertEqual(len(fake.calls), 2)
        s2 = Store(self.cfg["db_path"])
        self.addCleanup(s2.close)
        allc = s2.all_shots()
        self.assertEqual(len(allc), 3)
        self.assertTrue(all(s["caption"] == "보스 앞에서 한 컷" for s in allc))

    def test_image_sent_as_data_uri(self):
        fake = FakeLLM()
        p = os.path.join(self.sd, "20261101200000_1.png")
        caption_image(fake, p, "Dota 2")
        part = fake.calls[0][0]["content"][1]
        self.assertTrue(part["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("Dota 2", fake.calls[0][0]["content"][0]["text"])

    def test_limit_and_oversize_skip(self):
        store = Store(self.cfg["db_path"])
        self.addCleanup(store.close)
        st = store.upsert_shots(list(scan_sources(self.cfg, {})), 10)  # 10바이트 초과 전부 건너뜀
        self.assertEqual(st["skipped"], 3)
        self.assertEqual(store.pending(), [])
        self.assertEqual(len(store.skipped()), 3)
        store.upsert_shots(list(scan_sources(self.cfg, {})), 10 ** 6)
        self.assertEqual(len(store.pending(1)), 1)

    def test_no_llm_caption_and_diary_pipeline(self):
        import argparse
        self.assertEqual(cli.cmd_scan(argparse.Namespace(), self.cfg), 0)
        self.assertEqual(cli.cmd_caption(argparse.Namespace(limit=None, no_llm=True), self.cfg), 0)
        self.assertEqual(cli.cmd_diary(argparse.Namespace(week=True, date="2026-11-01", no_llm=True), self.cfg), 0)
        md = open(os.path.join(self.cfg["out_dir"], "diary-week-2026-10-26.md"), encoding="utf-8").read()
        self.assertIn("주간 게임 일기", md)
        self.assertEqual(cli.cmd_album(argparse.Namespace(embed_thumbs=False), self.cfg), 0)
        self.assertTrue(os.path.exists(os.path.join(self.cfg["out_dir"], "album.html")))


class DiaryTests(unittest.TestCase):
    def shots(self):
        t = datetime(2026, 11, 1, 20, 0)
        return [shot("Elden Ring", t, caption="보스 등장", mood="긴장", tags=["보스"], notable=True),
                shot("Elden Ring", t + timedelta(minutes=30), caption="승리", mood="긴장", notable=True),
                shot("Elden Ring", t + timedelta(minutes=40), caption="휴식"),
                shot("Hades", t + timedelta(days=1), caption="다음 날")]

    def test_template_fallback(self):
        md = render_diary(self.shots(), date(2026, 11, 1), date(2026, 11, 1), None, "/x/out")
        self.assertIn("# 게임 일기 2026-11-01 (일)", md)
        self.assertIn("## Elden Ring · 20:00–20:40 (3장)", md)
        self.assertIn("보스 등장", md)
        self.assertNotIn("Hades", md)
        self.assertIn("![보스 등장](../", md)

    def test_llm_narrative_and_failure_fallback(self):
        md = render_diary(self.shots(), date(2026, 11, 1), date(2026, 11, 1), FakeLLM(), "/x/out")
        self.assertIn("오늘은 즐거운 하루였다.", md)

        class Boom:
            def chat(self, *a, **k):
                raise LLMError("down")
        md = render_diary(self.shots(), date(2026, 11, 1), date(2026, 11, 1), Boom(), "/x/out")
        self.assertIn("Elden Ring 세션", md)

    def test_weekly_and_empty(self):
        s, e = week_range(date(2026, 11, 1))
        self.assertEqual((s, e), (date(2026, 10, 26), date(2026, 11, 1)))
        s, e = week_range(date(2026, 11, 2))
        md = render_diary(self.shots(), s, e, None, "/x/out", weekly=True)
        self.assertIn("Hades", md)
        self.assertIn("2026-11-02", md)
        self.assertIn("기록된 스크린샷이 없습니다", render_diary([], s, e))


class AlbumTests(unittest.TestCase):
    def test_escaping_and_self_containment(self):
        evil = '</script><img src=x onerror=alert(1)>'
        t = datetime(2026, 11, 1, 20, 0)
        with tempfile.TemporaryDirectory() as d:
            p = make_png(os.path.join(d, "shots", "a b.png"))
            out = os.path.join(d, "out")
            s = shot('G"<b>', t, path=p, caption=evil, tags=[evil], mood="&amp;")
            html_ = render_album([s], out, title="<i>앨범</i> {{DATA}}")
        self.assertNotIn("</script><img", html_)
        self.assertNotIn("<i>앨범", html_)
        self.assertIn("&lt;i&gt;앨범", html_)
        self.assertEqual(html_.count("</script>"), 2)
        data = re.search(r'<script id="data" type="application/json">(.*?)</script>', html_, re.S).group(1)
        items = json.loads(data)
        self.assertEqual(items[0]["caption"], evil)
        self.assertEqual(items[0]["src"], "../shots/a%20b.png")
        self.assertFalse(items[0].get("thumb"))
        # 외부 리소스 없음, 이미지 base64 내장 없음
        self.assertNotRegex(html_, r'(src|href)="https?://')
        self.assertNotIn("data:image", html_)
        self.assertNotIn("innerHTML", html_)

    def test_embed_thumbs_only_small(self):
        t = datetime(2026, 11, 1, 20, 0)
        with tempfile.TemporaryDirectory() as d:
            p = make_png(os.path.join(d, "a.png"))
            th = make_png(os.path.join(d, "t.png"))
            s = shot("G", t, path=p, thumb=th)
            plain = render_album([s], d, embed_thumbs=False)
            emb = render_album([s], d, embed_thumbs=True)
        self.assertNotIn("data:image/png;base64", plain)
        self.assertIn("data:image/png;base64", emb)

    def test_config_defaults(self):
        c = load_config("/nonexistent/c.json")
        self.assertEqual(c["base_url"], "http://127.0.0.1:8080/v1")

    def test_out_dir_default_is_user_dir_with_legacy_fallback(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                c = load_config("/nonexistent/c.json")
                self.assertIn(".gamediary", c["out_dir"])
                os.makedirs("out")
                open(os.path.join("out", "album.html"), "w").close()
                c = load_config("/nonexistent/c.json")
                self.assertEqual(c["out_dir"], "out")
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
