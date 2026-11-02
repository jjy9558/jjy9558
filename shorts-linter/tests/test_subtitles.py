import os
import unittest

from shortslint import subtitles as sb
from shortslint.config import load_config

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def read(n):
    with open(os.path.join(FIX, n), encoding="utf-8") as f:
        return f.read()


class SrtTests(unittest.TestCase):
    def test_parse(self):
        cues = sb.parse_srt(read("sample.srt"))
        self.assertEqual(len(cues), 3)
        self.assertAlmostEqual(cues[1].end, 2.5)
        self.assertEqual(cues[2].lines, ["안녕하세요", "두 번째 줄"])  # 태그 제거

    def test_checks(self):
        cfg = load_config()
        res = {r.name: r for r in sb.check_subtitles(sb.parse_srt(read("sample.srt")), cfg)}
        self.assertEqual(res["자막 줄 길이"].status, "warn")   # 1/4줄
        self.assertIn("16자", res["자막 줄 길이"].message)
        self.assertEqual(res["자막 속도(CPS)"].status, "warn")  # 0.5초에 긴 문장
        self.assertEqual(res["자막 하단 UI 영역"].status, "warn")  # SRT는 일반 경고

    def test_fail_ratio(self):
        cfg = load_config()
        cue = sb.Cue(0, 10, "가" * 30)
        res = sb.check_subtitles([cue, cue], cfg)
        self.assertEqual(res[0].status, "fail")

    def test_empty(self):
        self.assertEqual(sb.check_subtitles([], load_config())[0].status, "warn")


class AssTests(unittest.TestCase):
    def test_parse_positions(self):
        cues, info = sb.parse_ass(read("sample.ass"))
        self.assertEqual(info["res_y"], 1920)
        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[0].lines, ["아래쪽 자막", "둘째 줄"])
        self.assertAlmostEqual(cues[0].anchor_y, 1 - 100 / 1920.0, places=3)  # 하단 UI 영역
        self.assertAlmostEqual(cues[1].anchor_y, 1 - 700 / 1920.0, places=3)
        self.assertEqual(cues[1].text, "위쪽 자막, 쉼표 포함")
        self.assertAlmostEqual(cues[2].anchor_y, 1850 / 1920.0, places=3)

    def test_ui_zone(self):
        cfg = load_config()
        cues, _ = sb.parse_ass(read("sample.ass"))
        res = {r.name: r for r in sb.check_subtitles(cues, cfg, is_ass=True)}
        r = res["자막 하단 UI 영역"]
        self.assertEqual(r.status, "fail")  # 2/3 >= 0.5
        self.assertIn("2/3", r.message)

    def test_ui_zone_ok(self):
        cfg = load_config()
        cues, _ = sb.parse_ass(read("sample.ass"))
        res = sb.check_subtitles(cues[1:2], cfg, is_ass=True)
        self.assertEqual([r for r in res if r.name == "자막 하단 UI 영역"][0].status, "ok")


if __name__ == "__main__":
    unittest.main()
