import json
import os
import unittest

from shortslint import media
from shortslint.config import load_config
from shortslint import videochecks as vc

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class ProbeTests(unittest.TestCase):
    def test_parse_probe(self):
        info = media.parse_probe(json.loads(fx("ffprobe_shorts.json")))
        self.assertEqual((info.width, info.height), (1080, 1920))
        self.assertAlmostEqual(info.fps, 29.97, places=2)
        self.assertEqual(info.vcodec, "h264")
        self.assertTrue(info.has_audio)
        self.assertAlmostEqual(info.duration, 45.23)

    def test_rotation_swaps(self):
        info = media.parse_probe(json.loads(fx("ffprobe_rotated_landscape.json")))
        self.assertEqual((info.width, info.height), (1080, 1920))
        self.assertFalse(info.has_audio)

    def test_info_checks_ok(self):
        v = load_config()["video"]
        info = media.parse_probe(json.loads(fx("ffprobe_shorts.json")))
        res = {r.name: r for r in vc.checks_from_info(info, v)}
        for n in ("길이", "화면비", "해상도", "프레임레이트", "코덱/컨테이너", "오디오"):
            self.assertEqual(res[n].status, "ok", n)

    def test_info_checks_bad(self):
        v = load_config()["video"]
        info = media.parse_probe(json.loads(fx("ffprobe_rotated_landscape.json")))
        res = {r.name: r for r in vc.checks_from_info(info, v)}
        self.assertEqual(res["길이"].status, "fail")      # 200s > 180
        self.assertEqual(res["오디오"].status, "fail")
        self.assertEqual(res["코덱/컨테이너"].status, "ok")
        self.assertEqual(res["프레임레이트"].status, "ok")

    def test_warn_over_60(self):
        v = load_config()["video"]
        info = media.parse_probe(json.loads(fx("ffprobe_shorts.json")))
        info.duration = 90
        self.assertEqual(vc.checks_from_info(info, v)[0].status, "warn")
        v2 = load_config(profile="shorts-long")["video"]
        self.assertEqual(vc.checks_from_info(info, v2)[0].status, "ok")


class FilterParserTests(unittest.TestCase):
    def test_loudnorm(self):
        ln = media.parse_loudnorm(fx("loudnorm_stderr.txt"))
        self.assertAlmostEqual(ln["input_i"], -19.42)
        self.assertAlmostEqual(ln["input_tp"], -0.35)
        self.assertIsNone(media.parse_loudnorm("nothing"))

    def test_loudnorm_inf(self):
        ln = media.parse_loudnorm('{ "input_i" : "-inf", "input_tp": "-inf" }')
        self.assertEqual(ln["input_i"], float("-inf"))

    def test_silences(self):
        s = media.parse_silences(fx("silence_stderr.txt"), 45.0)
        self.assertEqual(len(s), 3)
        self.assertEqual(s[0][0], 0.0)
        self.assertAlmostEqual(s[1][1], 24.5)
        self.assertEqual(s[2], (40.0, 45.0))

    def test_black(self):
        self.assertEqual(media.parse_black(fx("black_stderr.txt")), [(0.0, 1.4), (30.5, 31.2)])

    def test_freeze(self):
        f = media.parse_freeze(fx("freeze_stderr.txt"), 45.0)
        self.assertEqual(f, [(0.0, 3.5), (40.0, 45.0)])


class AnalysisCheckTests(unittest.TestCase):
    def setUp(self):
        self.v = load_config()["video"]
        self.info = media.parse_probe(json.loads(fx("ffprobe_shorts.json")))
        self.info.duration = 45.0

    def test_audio_checks(self):
        res = {r.name: r for r in vc.checks_from_audio(fx("loudnorm_stderr.txt") + fx("silence_stderr.txt"), self.info, self.v)}
        self.assertEqual(res["라우드니스"].status, "warn")   # -19.4 -> diff -5.4
        self.assertEqual(res["트루피크"].status, "warn")     # -0.35 > -1
        self.assertEqual(res["무음 구간"].status, "warn")

    def test_hook_black_fail(self):
        res = {r.name: r for r in vc.checks_from_video(fx("black_stderr.txt") + fx("freeze_stderr.txt"), self.info, self.v)}
        self.assertEqual(res["첫 프레임 훅"].status, "fail")
        self.assertEqual(res["블랙 프레임"].status, "warn")
        self.assertEqual(res["정지 화면"].status, "warn")

    def test_clean_video(self):
        res = vc.checks_from_video("", self.info, self.v)
        self.assertTrue(all(r.status == "ok" for r in res))


if __name__ == "__main__":
    unittest.main()
