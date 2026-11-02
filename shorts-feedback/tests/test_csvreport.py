import tempfile
import unittest
from pathlib import Path

from shortsfb import csvreport as R

from helpers import FIX


def by_type(rep):
    return {t["type"]: t for t in rep["types"]}


class CsvReportCase(unittest.TestCase):
    # WP11 인수 (5): 한/영 헤더 두 픽스처
    def test_english_and_korean_headers_give_same_summary(self):
        en = R.build(FIX / "studio_en.csv")
        ko = R.build(FIX / "studio_ko.csv")
        self.assertEqual(en["videos"], 8)
        self.assertEqual(ko["videos"], 8)
        self.assertEqual(en["types"], ko["types"])
        self.assertEqual(set(en["columns"]), set(ko["columns"]))
        for f in ("id", "title", "views", "ctr", "avg_pct", "stayed", "duration", "avg_duration"):
            self.assertIn(f, ko["columns"], f)

    def test_summary_numbers_by_title_prefix(self):
        t = by_type(R.build(FIX / "studio_en.csv"))
        self.assertEqual(t["직장"]["n"], 3)
        self.assertEqual(t["직장"]["views_mean"], 1000.0)
        self.assertEqual(t["직장"]["views_median"], 1000)
        self.assertEqual(t["직장"]["retention_mean"], round((69.1 + 60.0 + 50.0) / 3, 1))
        self.assertEqual(t["이웃"]["n"], 2)
        self.assertTrue(t["이웃"]["small_sample"])
        self.assertEqual(t["기타"]["n"], 1)
        self.assertIsNone(t["기타"]["retention_mean"])
        self.assertEqual(t["직장"]["retention_weighted"],
                         round((69.1 * 1200 + 60.0 * 1000 + 50.0 * 800) / 3000, 1))

    def test_total_row_skipped_and_ordering(self):
        rep = R.build(FIX / "studio_en.csv")
        self.assertNotIn("Total", [t["type"] for t in rep["types"]])
        rets = [t["retention_mean"] for t in rep["types"] if t["retention_mean"] is not None]
        self.assertEqual(rets, sorted(rets, reverse=True))
        self.assertIsNone(rep["types"][-1]["retention_mean"])  # 값 없는 유형은 맨 뒤

    def test_low_performers(self):
        rep = R.build(FIX / "studio_en.csv")
        ids = [v["id"] for v in rep["low"]]
        self.assertIn("vid0007", ids)  # 150 < 중앙값의 50%
        self.assertIn("vid0008", ids)
        self.assertNotIn("vid0001", ids)

    def test_types_json_overrides_prefix(self):
        rep = R.build(FIX / "studio_en.csv", FIX / "types.json")
        t = by_type(rep)
        self.assertEqual(t["직장"]["n"], 3)
        self.assertEqual(t["시댁"]["n"], 2)
        self.assertEqual(t["이웃"]["n"], 2)

    def test_retention_derived_when_column_missing(self):
        text = "Content,Video title,Duration,Views,Average view duration\nv1,[A] x,0:40,100,0:20\nv2,[A] y,1:00,300,0:15\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_text(text, encoding="utf-8")
            rep = R.build(p)
        self.assertTrue(rep["retention_derived"])
        self.assertEqual(rep["types"][0]["retention_mean"], 37.5)  # (50 + 25) / 2
        self.assertIn("avg_pct", rep["missing"])
        self.assertIn("계산했습니다", R.format_text(rep))

    def test_map_override_and_errors(self):
        text = "id,제목,내맘대로열\nv1,[A] x,10\nv2,[A] y,30\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                R.build(p)  # 조회수 열을 찾지 못함
            rep = R.build(p, overrides={"views": "내맘대로열"})
            self.assertEqual(rep["types"][0]["views_mean"], 20.0)
            with self.assertRaises(ValueError):
                R.build(p, overrides={"views": "없는열"})

    def test_cp949_tab_separated_and_bom(self):
        rows = "동영상 제목\t조회수\t평균 시청 비율(%)\n[A] 가\t1,000\t50\n[A] 나\t3,000\t70\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_bytes(rows.encode("cp949"))
            rep = R.build(p)
            self.assertEqual(rep["types"][0]["views_mean"], 2000.0)
            self.assertEqual(rep["types"][0]["retention_mean"], 60.0)
            q = Path(d) / "y.csv"
            q.write_bytes(b"\xef\xbb\xbf" + rows.encode("utf-8"))
            self.assertEqual(R.build(q)["videos"], 2)

    def test_parsers(self):
        self.assertEqual(R.parse_duration("1:05"), 65.0)
        self.assertEqual(R.parse_duration("0:01:05"), 65.0)
        self.assertEqual(R.parse_duration("1분 5초"), 65.0)
        self.assertEqual(R.parse_duration("42"), 42.0)
        self.assertIsNone(R.parse_duration("-"))
        self.assertEqual(R.parse_num("1,234.5%"), 1234.5)
        self.assertIsNone(R.parse_num("abc"))
        self.assertEqual(R.hkey("Impressions click-through rate (%)"), "impressionsclickthroughrate")

    def test_format_text_mentions_small_samples(self):
        text = R.format_text(R.build(FIX / "studio_ko.csv"))
        self.assertIn("직장", text)
        self.assertIn("2*", text)
        self.assertIn("부검 후보", text)

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e.csv"
            p.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                R.build(p)


if __name__ == "__main__":
    unittest.main()
