import unittest

from sellstudio import config as cfg
from sellstudio import markets as mk


def fake_item(name="삼성 갤럭시 버즈2 프로 그라파이트 정품 미개봉급 풀박스 세트", **kw):
    d = {"name": name, "spec": "블루투스 이어폰 노이즈캔슬링", "grade": "A급", "condition": "사용 3개월",
         "defects": [], "included": ["본품", "케이스", "충전 케이블"], "trade": "직거래 가능",
         "photos": [{"file": "photo01.jpg", "caption": "이어폰 케이스 정면", "item": "이어폰"}] * 3}
    d.update(kw)
    return d


class TitleTests(unittest.TestCase):
    def test_every_market_respects_title_limit(self):
        markets = cfg.load_markets()
        for name in cfg.market_names(markets):
            for nm in ("버즈", "갤럭시 버즈2 프로", "삼성 갤럭시 버즈2 프로 그라파이트 정품 미개봉급 풀박스 세트 " * 3):
                d = mk.build_draft(fake_item(nm), name, markets[name])
                self.assertLessEqual(len(d["title"]), markets[name]["title_max"], (name, nm))
                self.assertTrue(d["title"])

    def test_limits_differ_by_market(self):
        markets = cfg.load_markets()
        lens = {n: markets[n]["title_max"] for n in cfg.market_names(markets)}
        self.assertEqual(set(lens), {"daangn", "bunjang", "smartstore"})
        self.assertGreater(lens["smartstore"], lens["bunjang"])

    def test_optional_parts_dropped_first(self):
        d = mk.build_draft(fake_item("RTX 3060 12GB"), "bunjang", cfg.load_markets()["bunjang"])
        self.assertTrue(d["title"].startswith("RTX 3060 12GB"))

    def test_marked_approximate(self):
        markets = cfg.load_markets()
        self.assertIn("근사치", cfg.APPROX_NOTE)
        d = mk.build_draft(fake_item(), "daangn", markets["daangn"])
        self.assertTrue(d["approximate"])
        self.assertIn("근사치", mk.render_draft(d))

    def test_missing_name_uses_placeholder_and_todo(self):
        d = mk.build_draft({"photos": []}, "daangn", cfg.load_markets()["daangn"])
        self.assertIn("[상품명 입력]", d["title"])
        self.assertTrue(d["todo"])

    def test_name_from_vlm_caption_when_not_given(self):
        data = fake_item(name="")
        data["photos"] = [{"file": "a", "caption": "c", "item": "무선 이어폰"}]
        d = mk.build_draft(data, "daangn", cfg.load_markets()["daangn"])
        self.assertIn("무선 이어폰", d["title"])

    def test_price_priority(self):
        data = fake_item(price={"median": 100000}, price_final=90000)
        self.assertEqual(mk.suggested_price(data), 90000)
        self.assertEqual(mk.suggested_price(data, 80000), 80000)
        del data["price_final"]
        self.assertEqual(mk.suggested_price(data), 100000)
        self.assertIsNone(mk.suggested_price({}))

    def test_desc_limit_trims_sections(self):
        conf = dict(cfg.load_markets()["daangn"], desc_max=120)
        d = mk.build_draft(fake_item(notes="긴 설명 " * 50), "daangn", conf)
        self.assertTrue(d["notes"])

    def test_user_markets_file_overrides(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "m.json"
            p.write_text(json.dumps({"daangn": {"title_max": 12}, "mine": {"label": "내장터", "title_max": 20}}, ensure_ascii=False),
                         encoding="utf-8")
            m = cfg.load_markets(str(p))
        self.assertEqual(m["daangn"]["title_max"], 12)
        self.assertEqual(m["daangn"]["label"], "당근")
        self.assertIn("mine", cfg.market_names(m))
        d = mk.build_draft(fake_item(), "daangn", m["daangn"])
        self.assertLessEqual(len(d["title"]), 12)

    def test_bad_markets_file(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "m.json"
            p.write_text(json.dumps({"daangn": {"title_max": "x"}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                cfg.load_markets(str(p))


if __name__ == "__main__":
    unittest.main()
