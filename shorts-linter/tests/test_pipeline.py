import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from shortslint import cli, media, runner
from shortslint import videochecks as vc
from shortslint.config import load_config

HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class MissingFfmpegTests(unittest.TestCase):
    def test_skip_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.mp4")
            with open(p, "wb") as f:
                f.write(b"0" * 1000)
            with mock.patch("shutil.which", return_value=None), \
                    mock.patch("subprocess.run", side_effect=AssertionError("호출되면 안 됨")):
                res, info = vc.run_video_checks(p, load_config()["video"])
            self.assertIsNone(info)
            st = {r.name: r.status for r in res}
            self.assertEqual(st["파일 크기"], "ok")
            self.assertEqual(st["ffprobe 기반 검사"], "skip")
            self.assertEqual(st["ffmpeg 분석"], "skip")
            self.assertIn("미설치", [r for r in res if r.status == "skip"][0].message)

    def test_cli_exit_zero_when_only_skips(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.mp4")
            with open(p, "wb") as f:
                f.write(b"0")
            with mock.patch("shutil.which", return_value=None):
                self.assertEqual(cli.main(["check", p, "--quiet"]), 0)


class BatchTests(unittest.TestCase):
    def test_batch_duplicates_and_exit(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("a", "b", "c"):
                with open(os.path.join(d, n + ".mp4"), "wb") as f:
                    f.write(b"0")
            for n, t in (("a", "시어머니의 비밀 #shorts"), ("b", "시어머니의 비밀 #shorts"), ("c", "전혀 다른 이야기 #shorts")):
                with open(os.path.join(d, n + ".meta.json"), "w", encoding="utf-8") as f:
                    json.dump({"title": t, "ai_disclosure": True}, f, ensure_ascii=False)
            out_json = os.path.join(d, "r.json")
            out_md = os.path.join(d, "r.md")
            with mock.patch("shutil.which", return_value=None):
                rc = cli.main(["check-dir", d, "--quiet", "--json", out_json, "--md", out_md])
            self.assertEqual(rc, 1)  # 중복 제목 -> ❌
            with open(out_json, encoding="utf-8") as f:
                data = json.load(f)
            st = {os.path.basename(r["path"]): r["status"] for r in data["reports"]}
            self.assertEqual(st["a.mp4"], "fail")
            self.assertNotEqual(st["c.mp4"], "fail")
            with open(out_md, encoding="utf-8") as f:
                self.assertIn("| 상태 |", f.read())

    def test_strict(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.mp4")
            with open(p, "wb") as f:
                f.write(b"0")
            with open(os.path.join(d, "a.srt"), "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:02,000\n안녕\n")
            with mock.patch("shutil.which", return_value=None):
                self.assertEqual(cli.main(["check", p, "--quiet"]), 0)
                self.assertEqual(cli.main(["check", p, "--quiet", "--strict"]), 1)  # SRT 위치 경고

    def test_missing_target(self):
        self.assertEqual(cli.main(["check", "/nonexistent.mp4"]), 2)


@unittest.skipUnless(HAVE_FF, "ffmpeg/ffprobe 없음")
class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.video = os.path.join(cls.tmp.name, "clip.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:r=30:d=5",
                        "-f", "lavfi", "-i", "sine=frequency=440:d=5",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", cls.video],
                       check=True, shell=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_end_to_end(self):
        res, info = vc.run_video_checks(self.video, load_config()["video"])
        st = {r.name: r for r in res}
        self.assertEqual((info.width, info.height), (1080, 1920))
        self.assertEqual(st["화면비"].status, "ok")
        self.assertEqual(st["해상도"].status, "ok")
        self.assertEqual(st["오디오"].status, "ok")
        self.assertIn("라우드니스", st)       # loudnorm JSON 파싱 성공
        self.assertIn("트루피크", st)
        self.assertEqual(st["첫 프레임 훅"].status, "ok")
        self.assertEqual(st["정지 화면"].status, "warn")  # 단색 영상 = 정지

    def test_black_start_detected(self):
        p = os.path.join(self.tmp.name, "black.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30:d=3",
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "3", p],
                       check=True, shell=False)
        res, _ = vc.run_video_checks(p, load_config()["video"])
        st = {r.name: r for r in res}
        self.assertEqual(st["첫 프레임 훅"].status, "fail")
        self.assertEqual(st["무음 구간"].status, "warn")
        self.assertEqual(st["라우드니스"].status, "fail")

    def test_cli_exit_code(self):
        rc = cli.main(["check", self.video, "--quiet"])
        self.assertEqual(rc, 1)  # -20 LUFS대 사인파 -> 라우드니스 ❌
