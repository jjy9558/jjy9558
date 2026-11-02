import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sellstudio import cli, jpegmeta, pcjudge
from sellstudio import config as cfg
from sellstudio import page as pg
from sellstudio._jjkit import llm
from tests.helpers import FakeOpener, make_jpeg, run

PASTE = "RTX 3060 12GB 28만원\nRTX 3060 25만 5천\n270,000원\n290000\n262,000원\n"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.src = self.dir / "원본사진"
        self.src.mkdir()
        self.gps = self.src / "IMG_0001.jpg"
        self.gps.write_bytes(make_jpeg(gps=True))
        self.plain = self.src / "IMG_0002.jpg"
        self.plain.write_bytes(make_jpeg(gps=False))
        self.item = self.dir / "item"

    def tearDown(self):
        self._t.cleanup()

    def new(self, *extra, name="ASUS RTX 3060 12GB 그래픽카드"):
        args = ["new", str(self.gps), str(self.plain), "--out", str(self.item), "--name", name,
                "--grade", "A급", "--condition", "사용 1년", "--defect", "백플레이트 기스", "--included", "본품, 박스",
                "--trade", "직거래 강남구 역삼동"] + list(extra)
        return run(args)

    def data(self):
        return json.loads((self.item / "item.json").read_text(encoding="utf-8"))


class NewAndPrivacyTests(Base):
    def test_new_copies_and_keeps_original(self):
        before = (sha(self.gps), sha(self.plain))
        c, o, _ = self.new()
        self.assertEqual(c, 0)
        self.assertTrue((self.item / "photos" / "photo01.jpg").is_file())
        self.assertEqual((sha(self.gps), sha(self.plain)), before)
        d = self.data()
        self.assertEqual(d["photos"][0]["original_name"], "IMG_0001.jpg")
        self.assertEqual(d["photos"][0]["width"], 64)

    def test_new_refuses_existing(self):
        self.new()
        c, _, e = self.new()
        self.assertEqual(c, 2)
        self.assertIn("이미", e)

    def test_new_rejects_non_image_and_missing(self):
        c, _, e = run(["new", str(self.dir / "없음.jpg"), "--out", str(self.item)])
        self.assertEqual(c, 2)
        txt = self.dir / "a.txt"
        txt.write_text("x", encoding="utf-8")
        c, _, e = run(["new", str(txt), "--out", str(self.item)])
        self.assertEqual(c, 2)

    def test_privacy_warns_on_gps(self):
        self.new()
        c, o, _ = run(["privacy-check", str(self.item)])
        self.assertEqual(c, 1)
        self.assertIn("GPS", o)
        self.assertIn("37.50000", o)
        self.assertIn("2장 중 GPS 포함 1장", o)

    def test_strip_makes_gps_free_copy_and_original_hash_unchanged(self):
        self.new()
        photos = self.item / "photos"
        orig_hash = {p.name: sha(p) for p in photos.iterdir()}
        user_hash = sha(self.gps)
        c, o, _ = run(["privacy-check", str(self.item), "--strip"])
        self.assertEqual(c, 0)
        clean = self.item / "photos_clean"
        for p in clean.iterdir():
            self.assertFalse(jpegmeta.parse_exif(p.read_bytes()).has_gps, p.name)
        self.assertTrue(jpegmeta.parse_exif((photos / "photo01.jpg").read_bytes()).has_gps)  # 사본의 원본은 그대로
        self.assertEqual({p.name: sha(p) for p in photos.iterdir()}, orig_hash)
        self.assertEqual(sha(self.gps), user_hash)
        self.assertEqual(jpegmeta.parse_exif((clean / "photo01.jpg").read_bytes()).orientation, 6)
        # 정리본을 다시 점검하면 GPS 없음
        c, o, _ = run(["privacy-check", str(clean)])
        self.assertEqual(c, 0)
        self.assertIn("GPS 포함 0장", o)

    def test_strip_plain_files_to_out_dir(self):
        out = self.dir / "clean"
        before = sha(self.gps)
        c, o, _ = run(["privacy-check", str(self.src), "--strip", "--out", str(out)])
        self.assertEqual(c, 0)
        self.assertEqual(sha(self.gps), before)
        self.assertFalse(jpegmeta.parse_exif((out / "IMG_0001.jpg").read_bytes()).has_gps)

    def test_strip_refuses_in_place(self):
        c, _, e = run(["privacy-check", str(self.src), "--strip", "--out", str(self.src)])
        self.assertEqual(c, 2)
        self.assertEqual(jpegmeta.parse_exif(self.gps.read_bytes()).has_gps, True)

    def test_filename_clue(self):
        bad = self.src / "송장_택배.jpg"
        bad.write_bytes(make_jpeg(gps=False))
        c, o, _ = run(["privacy-check", str(bad)])
        self.assertIn("단서", o)

    def test_caption_privacy_flags_surface(self):
        self.new()
        d = self.data()
        d["photos"][0]["privacy_flags"] = ["택배 송장이 보임"]
        (self.item / "item.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        _, o, _ = run(["privacy-check", str(self.item)])
        self.assertIn("택배 송장이 보임", o)

    def test_png_notice(self):
        png = self.src / "a.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
        c, o, _ = run(["privacy-check", str(png)])
        self.assertIn("EXIF를 읽지 못했습니다", o)


class CaptionTests(Base):
    def test_no_llm_template(self):
        self.new()
        c, o, _ = run(["caption", str(self.item), "--no-llm"])
        self.assertEqual(c, 0)
        d = self.data()
        self.assertEqual(d["photos"][0]["caption_source"], "template")
        self.assertIn("1/2", d["photos"][0]["caption"])

    def test_vlm_caption_with_fake_opener(self):
        self.new(name="")
        fake = FakeOpener([{"item": "ASUS 그래픽카드", "caption": "팬 두 개 달린 그래픽카드 정면", "defects": ["backplate 기스"],
                            "visible_text": ["DUAL RTX3060"], "privacy_flags": []},
                           "```json\n{\"caption\": \"뒷면 사진\", \"item\": \"ASUS 그래픽카드\",}\n```"])
        client = llm.LLMClient("http://127.0.0.1:8080/v1", opener=fake)
        with mock.patch.object(cli, "make_client", return_value=client):
            c, o, _ = run(["caption", str(self.item)])
        self.assertEqual(c, 0)
        d = self.data()
        self.assertEqual(d["photos"][0]["caption_source"], "vlm")
        self.assertEqual(d["photos"][1]["caption"], "뒷면 사진")
        # 사진 바이트는 data URI 로 가고, GPS 가 든 APP1 은 제거돼서 간다
        body = json.loads(fake.requests[0].data.decode("utf-8"))
        uri = body["messages"][-1]["content"][1]["image_url"]["url"]
        import base64
        sent = base64.b64decode(uri.split(",", 1)[1])
        self.assertTrue(uri.startswith("data:image/jpeg;base64,"))
        self.assertFalse(jpegmeta.parse_exif(sent).has_gps)
        # 이름이 비었으면 VLM 이 말한 물품명으로 초안을 만든다
        c, o, _ = run(["draft", str(self.item), "--market", "daangn", "--no-pc-check"])
        self.assertIn("ASUS 그래픽카드", o)

    def test_vlm_failure_falls_back_to_template(self):
        self.new()
        fake = FakeOpener([OSError("down")])
        client = llm.LLMClient("http://127.0.0.1:8080/v1", opener=fake)
        with mock.patch.object(cli, "make_client", return_value=client):
            c, o, _ = run(["caption", str(self.item)])
        self.assertEqual(c, 0)
        self.assertIn("템플릿으로 대체", o)
        self.assertEqual(len(fake.requests), 1)  # 서버가 죽었으면 사진마다 다시 시도하지 않음
        self.assertTrue(all(p["caption_source"] == "template" for p in self.data()["photos"]))

    def test_llm_url_must_be_local_unless_allowed(self):
        self.new()
        c, _, e = run(["caption", str(self.item), "--llm-url", "https://api.example.com/v1"])
        self.assertEqual(c, 2)
        self.assertIn("로컬", e)

    def test_local_client_ignores_proxy_env(self):
        import urllib.request
        with mock.patch.dict(os.environ, {"http_proxy": "http://proxy.invalid:3128", "HTTP_PROXY": "http://proxy.invalid:3128"}):
            ns = type("A", (), {"no_llm": False, "llm_url": "http://127.0.0.1:8080/v1", "llm_model": None, "allow_remote": False})()
            client = cli.make_client(ns)
        proxies = [h.proxies for h in client.opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
        self.assertFalse(any(proxies))
        self.assertTrue(client.base_url.startswith("http://127.0.0.1"))


class DraftAndPageTests(Base):
    def test_draft_all_markets_within_limits_and_saved(self):
        self.new()
        run(["caption", str(self.item), "--no-llm"])
        c, o, _ = run(["price", str(self.item), "--paste", self._paste(), "--no-pc-check"])
        self.assertEqual(c, 0)
        self.assertIn("중앙값 270,000원", o)
        c, o, _ = run(["draft", str(self.item), "--no-pc-check"])
        self.assertEqual(c, 0, o)
        markets = cfg.load_markets()
        d = self.data()
        for m in ("daangn", "bunjang", "smartstore"):
            self.assertLessEqual(len(d["drafts"][m]["title"]), markets[m]["title_max"])
            self.assertTrue((self.item / "drafts" / (m + ".txt")).is_file())
            self.assertEqual(d["drafts"][m]["price"], 270000)
        self.assertIn("근사치", o)
        self.assertIn("자동 게시는 하지 않습니다", o)

    def test_unknown_market(self):
        self.new()
        c, _, e = run(["draft", str(self.item), "--market", "ebay"])
        self.assertEqual(c, 2)

    def test_draft_flags_lint_errors(self):
        self.new(name="전국 최저가 그래픽카드")
        c, o, _ = run(["draft", str(self.item), "--market", "daangn", "--no-pc-check"])
        self.assertEqual(c, 1)
        self.assertIn("금칙어", o)

    def test_price_no_numbers(self):
        self.new()
        p = self.dir / "empty.txt"
        p.write_text("가격 없음\n", encoding="utf-8")
        c, _, e = run(["price", str(self.item), "--paste", str(p)])
        self.assertEqual(c, 2)

    def test_price_hint_only_from_paste(self):
        self.new()
        run(["draft", str(self.item), "--market", "daangn", "--no-pc-check"])
        d = self.data()
        self.assertIsNone(d["drafts"]["daangn"]["price"])  # 시세를 안 넣으면 가격을 지어내지 않는다

    def _paste(self):
        p = self.dir / "paste.txt"
        p.write_text(PASTE, encoding="utf-8")
        return str(p)

    def test_page_self_contained_relative_images(self):
        self.new()
        run(["caption", str(self.item), "--no-llm"])
        run(["privacy-check", str(self.item), "--strip"])
        c, o, _ = run(["page", str(self.item)])
        self.assertEqual(c, 0, o)
        doc = (self.item / "detail.html").read_text(encoding="utf-8")
        self.assertEqual(pg.self_contained_problems(doc), [])
        self.assertNotIn("http", doc.lower().replace("<!doctype html>", ""))
        self.assertIn('src="photos_clean/photo01.jpg"', doc)
        self.assertIn("<style>", doc)
        self.assertNotIn("<script", doc.lower())
        self.assertNotIn("<link", doc.lower())
        self.assertIn('lang="ko"', doc)
        self.assertIn("viewport", doc)
        # 이미지 상대경로가 실제 파일을 가리킴
        import re
        for src in re.findall(r'<img src="([^"]+)"', doc):
            self.assertTrue((self.item / src).is_file(), src)

    def test_page_warns_without_clean_copies(self):
        self.new()
        c, o, _ = run(["page", str(self.item)])
        self.assertIn("photos_clean", o)
        doc = (self.item / "detail.html").read_text(encoding="utf-8")
        self.assertIn('src="photos/photo01.jpg"', doc)

    def test_page_escapes_html(self):
        self.new(name="<script>alert(1)</script> 카드")
        run(["page", str(self.item)])
        doc = (self.item / "detail.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", doc)
        self.assertIn("&lt;script&gt;", doc)

    def test_page_out_must_stay_in_item_folder(self):
        self.new()
        c, _, e = run(["page", str(self.item), "--out", "../x.html"])
        self.assertEqual(c, 2)

    def test_lint_item_finds_leak_in_notes(self):
        self.new("--note", "연락 010-" + "1234" + "-" + "5678" + " 로")
        run(["draft", str(self.item), "--market", "daangn", "--no-pc-check"])
        c, o, _ = run(["lint", str(self.item)])
        self.assertEqual(c, 1)
        self.assertIn("개인정보", o)

    def test_lint_file_and_stdin(self):
        f = self.dir / "t.txt"
        f.write_text("최저가!!!\n", encoding="utf-8")
        c, o, _ = run(["lint", str(f)])
        self.assertEqual(c, 1)
        f.write_text("깨끗한 글\n", encoding="utf-8")
        c, o, _ = run(["lint", str(f)])
        self.assertEqual(c, 0)
        f.write_text("완벽한 상태\n", encoding="utf-8")
        self.assertEqual(run(["lint", str(f)])[0], 0)
        self.assertEqual(run(["lint", str(f), "--strict"])[0], 1)


class PcJudgeTests(Base):
    def _fake_judge(self):
        p = self.dir / "fake_judge.py"
        p.write_text("import sys\nprint('가짜 판정: ' + open(sys.argv[2], encoding='utf-8').read().splitlines()[0])\n",
                     encoding="utf-8")
        return '%s %s' % (sys.executable, p)

    def test_pc_keyword_triggers_judge_when_present(self):
        self.new()  # kind 를 안 줘도 이름의 RTX 3060 으로 PC 부품 판단
        c, o, _ = run(["price", str(self.item), "--paste", self._paste(), "--judge-cmd", self._fake_judge()])
        self.assertEqual(c, 0)
        self.assertIn("조회 완료", o)
        self.assertIn("가짜 판정: ASUS RTX 3060", o)
        self.assertEqual(self.data()["pc_check"]["status"], "ran")

    def test_skipped_when_judge_absent(self):
        self.new()
        empty = self.dir / "비어있는폴더"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"PATH": str(empty)}):
            c, o, _ = run(["price", str(self.item), "--paste", self._paste(), "--judge-dir", str(empty)])
        self.assertEqual(c, 0)
        self.assertIn("건너뜀", o)
        self.assertEqual(self.data()["pc_check"]["status"], "skipped")

    def test_skipped_when_judge_cmd_missing(self):
        self.new()
        res = pcjudge.run_pc_check(self.data(), 270000, judge_cmd=str(self.dir / "nope-judge"))
        self.assertEqual(res["status"], "skipped")

    def test_skipped_when_judge_fails(self):
        bad = self.dir / "bad.py"
        bad.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        res = pcjudge.run_pc_check({"name": "RTX 3060"}, None, judge_cmd="%s %s" % (sys.executable, bad))
        self.assertEqual(res["status"], "skipped")
        self.assertIn("오류", res["reason"])

    def test_non_pc_item_not_queried(self):
        res = pcjudge.run_pc_check({"name": "에어프라이어", "photos": []}, None, judge_cmd=self._fake_judge())
        self.assertEqual(res["status"], "not_pc")

    def test_kind_pc_forces_check(self):
        self.assertTrue(pcjudge.looks_like_pc({"kind": "pc", "name": "뭔가"}))
        self.assertTrue(pcjudge.looks_like_pc({"name": "삼성 DDR4 16GB 램"}))
        self.assertFalse(pcjudge.looks_like_pc({"name": "에어프라이어", "photos": []}))

    def test_draft_shows_skip_for_pc(self):
        self.new()
        empty = self.dir / "e"
        empty.mkdir()
        c, o, _ = run(["draft", str(self.item), "--market", "daangn", "--judge-dir", str(empty)])
        self.assertIn("건너뜀", o)

    @unittest.skipUnless((Path(__file__).resolve().parents[2] / "used-pc-judge" / "usedpc" / "__main__.py").is_file(),
                         "used-pc-judge 폴더 없음")
    def test_real_sibling_judge(self):
        self.new()
        c, o, _ = run(["price", str(self.item), "--paste", self._paste()])
        self.assertIn("used-pc-judge", o)
        self.assertEqual(self.data()["pc_check"]["status"], "ran")

    def _paste(self):
        p = self.dir / "paste.txt"
        p.write_text(PASTE, encoding="utf-8")
        return str(p)


class ConfigTests(unittest.TestCase):
    def test_dump(self):
        c, o, _ = run(["config", "dump", "markets"])
        self.assertEqual(c, 0)
        self.assertIn("title_max", json.loads(o)["daangn"])
        c, o, _ = run(["config", "dump", "rules"])
        self.assertIn("banned", json.loads(o))


if __name__ == "__main__":
    unittest.main()
