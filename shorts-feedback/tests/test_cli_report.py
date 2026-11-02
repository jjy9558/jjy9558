import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shortsfb import linter
from shortsfb.cli import main

from helpers import FIX, write_pngs
from synth import make_comments


def run(argv):
    out = io.StringIO()
    code = main(argv, out=out)
    return code, out.getvalue()


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = str(self.root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv):
        return run(["--data", self.data, *argv])

    def test_vote_create_result_list(self):
        imgs = [str(p) for p in write_pngs(self.root, 2)]
        code, text = self.cli("vote", "create", *imgs, "--question", "뭐가 좋아요?")
        self.assertEqual(code, 0)
        code, text = self.cli("vote", "result")
        self.assertIn("표본: 0명", text)
        self.assertIn("아직 투표가 없습니다", text)
        code, text = self.cli("vote", "result", "--json")
        self.assertEqual(json.loads(text)["n"], 0)
        self.assertIn("뭐가 좋아요?", self.cli("vote", "list")[1])

    def test_vote_errors_are_exit_2(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(self.cli("vote", "create", str(self.root / "nope.png"), str(self.root / "no2.png"))[0], 2)
            self.assertEqual(self.cli("vote", "result", "--poll", "deadbeef")[0], 2)

    def test_comments_cluster_cli_and_json_out(self):
        texts, _ = make_comments(30, seed=3)
        src = self.root / "c.json"
        src.write_text(json.dumps([{"authorDisplayName": "홍길동실명", "textOriginal": t} for t in texts],
                                  ensure_ascii=False), encoding="utf-8")
        outj = self.root / "out.json"
        code, text = self.cli("comments", "cluster", str(src), "--k", "4", "--json", str(outj))
        self.assertEqual(code, 0)
        self.assertIn("다음 사연 주제 TOP4", text)
        saved = outj.read_text(encoding="utf-8")
        self.assertNotIn("홍길동실명", saved)
        self.assertEqual(json.loads(saved)["k"], 4)

    def test_comments_llm_flag_without_env_falls_back(self):
        texts, _ = make_comments(10, seed=3)
        src = self.root / "c.txt"
        src.write_text("\n".join(texts), encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SHORTSFB_LLM_URL", None)
            code, text = self.cli("comments", "cluster", str(src), "--llm")
        self.assertEqual(code, 0)
        self.assertIn("n-gram 이름", text)

    def test_csv_report_cli(self):
        code, text = self.cli("csv-report", str(FIX / "studio_ko.csv"), "--types", str(FIX / "types.json"))
        self.assertEqual(code, 0)
        self.assertIn("시댁", text)
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(self.cli("csv-report", str(FIX / "studio_ko.csv"), "--map", "badfield=x")[0], 2)

    def test_autopsy_and_fatigue_cli(self):
        self.cli("autopsy", "add", "vid1", "--views", "10", "--causes", "hook_weak", "--date", "2026-10-01")
        code, text = self.cli("autopsy", "add", "vid1", "--views", "10", "--causes", "hook_weak", "--date", "2026-10-01")
        self.assertIn("갱신", text)
        self.assertIn("첫 3초 훅이 약함", self.cli("autopsy", "patterns")[1])
        out = self.root / "check.md"
        self.cli("autopsy", "checklist", "--out", str(out))
        self.assertIn("- [ ]", out.read_text(encoding="utf-8"))
        self.assertIn("hook_weak", self.cli("autopsy", "causes")[1])
        self.cli("fatigue", "add", "6", "--date", "2026-11-02")
        code, text = self.cli("fatigue", "check", "--today", "2026-11-02", "--strict")
        self.assertEqual(code, 1)
        self.assertIn("쉬어 가세요", text)
        code, text = self.cli("fatigue", "check", "--today", "2026-12-25", "--strict")
        self.assertEqual(code, 0)

    def test_fatigue_notify_without_channel(self):
        self.cli("fatigue", "add", "6", "--date", "2026-11-02")
        env = dict(os.environ)
        env.pop("SHORTSFB_NTFY_TOPIC", None)
        with mock.patch.dict(os.environ, env, clear=True):
            code, text = self.cli("fatigue", "check", "--today", "2026-11-02", "--notify")
        self.assertIn("알림 채널이 없습니다", text)

    # WP11 인수: shorts-linter가 없어도 리포트가 나온다 (D-5 (5))
    def test_report_without_linter(self):
        meta = self.root / "meta.json"
        meta.write_text(json.dumps({"title": "테스트"}), encoding="utf-8")
        texts, _ = make_comments(20, seed=3)
        src = self.root / "c.txt"
        src.write_text("\n".join(texts), encoding="utf-8")
        self.cli("autopsy", "add", "vid1", "--causes", "hook_weak")
        with mock.patch.dict(os.environ, {"SHORTSFB_LINTER": str(self.root / "missing")}), \
                mock.patch.object(linter, "find_linter", return_value=None):
            code, text = self.cli("report", "--comments", str(src), "--csv", str(FIX / "studio_en.csv"),
                                  "--meta", str(meta))
        self.assertEqual(code, 0)
        self.assertIn("# 쇼츠 피드백 리포트", text)
        self.assertIn("다음 사연 주제", text)
        self.assertIn("스튜디오 CSV 요약", text)
        self.assertIn("부검 노트", text)
        self.assertIn("shorts-linter를 찾지 못해 건너뜀", text)

    def test_report_with_poll_and_out_file(self):
        imgs = [str(p) for p in write_pngs(self.root, 2)]
        self.cli("vote", "create", *imgs)
        out = self.root / "r.md"
        code, text = self.cli("report", "--poll", "--out", str(out))
        self.assertEqual(code, 0)
        body = out.read_text(encoding="utf-8")
        self.assertIn("## 썸네일 투표", body)
        self.assertIn("아직 투표가 없습니다", body)

    def test_report_skips_bad_inputs(self):
        code, text = self.cli("report", "--csv", str(self.root / "nope.csv"), "--poll", "deadbeef")
        self.assertEqual(code, 0)
        self.assertEqual(text.count("건너뜀"), 2)


class LinterCase(unittest.TestCase):
    def test_missing_linter_and_meta(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(linter, "find_linter", return_value=None):
                r = linter.run_promise(Path(d) / "m.json")
            self.assertFalse(r["available"])
            fake = Path(d) / "fake-linter"
            (fake / "shortslint").mkdir(parents=True)
            r = linter.run_promise(Path(d) / "nope.json", linter=fake)
            self.assertTrue(r["available"] and not r["ok"])
            self.assertIn("meta.json이 없어", r["note"])

    def test_calls_subprocess_titles(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "fake-linter"
            (fake / "shortslint").mkdir(parents=True)
            (fake / "shortslint" / "__main__.py").write_text(
                "import sys\nprint('ARGS', ' '.join(sys.argv[1:2]))\nprint('점수 90')\n", encoding="utf-8")
            meta = Path(d) / "meta.json"
            meta.write_text("{}", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SHORTSFB_LINTER": str(fake)}):
                self.assertEqual(linter.find_linter(), fake)
                r = linter.run_promise(meta)
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["lines"], ["ARGS titles", "점수 90"])

    def test_failing_linter_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "fake-linter"
            (fake / "shortslint").mkdir(parents=True)
            (fake / "shortslint" / "__main__.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
            meta = Path(d) / "meta.json"
            meta.write_text("{}", encoding="utf-8")
            r = linter.run_promise(meta, linter=fake)
            self.assertFalse(r["ok"])
            self.assertIn("종료 코드 3", r["note"])


if __name__ == "__main__":
    unittest.main()
