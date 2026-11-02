import contextlib
import io
import json
import os
import tempfile
import unittest

from shortslint import cli, disclose, similar, sources, titles
from shortslint.config import load_config

A = "시어머니가 몰래 숨겨둔 세 가지 비밀을 며느리가 우연히 발견했습니다 결국 가족 모두가 충격에 빠졌고 마지막에 반전이 있었습니다"
A2 = A.replace("우연히", "그날") + " 정말입니다"
B = "퇴근길 지하철에서 만난 낯선 할머니가 건넨 오래된 편지 한 통이 제 인생을 완전히 바꿔 놓았습니다"


def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def w(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


class SimilarTests(unittest.TestCase):
    def test_near_duplicate_warns_and_different_passes(self):
        with tempfile.TemporaryDirectory() as d:
            w(d, "a.txt", A)
            w(d, "b.txt", A2)
            w(d, "c.srt", "1\n00:00:00,000 --> 00:00:03,000\n%s\n" % B)
            code, out1 = run(["similar", d])
            _, out2 = run(["similar", d])
            self.assertEqual(out1, out2)
            lines = out1.splitlines()
            self.assertTrue(lines[0].startswith("⚠️ a: 재활용 콘텐츠 위험"))
            self.assertIn("'b'", lines[0])
            c_line = next(l for l in lines if l.startswith(("✅ c", "⚠️ c")))
            self.assertTrue(c_line.startswith("✅"))
            self.assertEqual(code, 0)
            self.assertEqual(run(["similar", d, "--strict"])[0], 1)

    def test_top3_and_words(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(5):
                w(d, "x%d.txt" % i, A + " %d" % i)
            rows, _ = similar.analyze(d, load_config(), use_words=True)
            self.assertEqual(len(rows[0]["nearest"]), 3)


class DiscloseTests(unittest.TestCase):
    def test_combinations(self):
        t = lambda **k: disclose.draft({"ai_used": k})["text"]
        self.assertIn("AI 합성 음성", t(voice=True))
        self.assertNotIn("이미지", t(voice=True))
        both = t(voice=True, image=True)
        self.assertIn("AI 합성 음성(TTS) 및 AI로 생성한 이미지", both)
        script = t(script=True)
        self.assertIn("대본", script)
        self.assertIn("합성이 아닙니다", script)
        self.assertIn("AI 합성 요소가 없다", t())
        d = disclose.draft({"ai_used": {"voice": True}})
        self.assertTrue(any("공식 문서 확인" in g for g in d["guide"]))
        self.assertTrue(any("변경되거나 합성된 콘텐츠" in g for g in d["guide"]))

    def test_cli_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = w(d, "m.json", json.dumps({"ai_used": {"image": True}}))
            code, out = run(["disclose", p])
            self.assertEqual(code, 0)
            self.assertIn("AI로 생성한 이미지", out)
            p2 = w(d, "n.json", "{}")
            self.assertEqual(cli.main(["disclose", p2]), 2)


class SourcesTests(unittest.TestCase):
    def test_empty_license_warns(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "s.csv")
            self.assertEqual(run(["sources", "add", "--file", f, "--name", "bgm1", "--license", "CC0",
                                  "--url", "https://x.example/1", "--ai", "no"])[0], 0)
            self.assertEqual(run(["sources", "add", "--file", f, "--name", "img1", "--url", "https://x.example/2",
                                  "--ai", "yes"])[0], 0)
            code, out = run(["sources", "check", "--file", f])
            self.assertIn("img1: 라이선스 미기재", out)
            self.assertNotIn("bgm1", out)
            self.assertEqual(code, 0)
            self.assertEqual(run(["sources", "check", "--file", f, "--strict"])[0], 1)
            self.assertIn("bgm1 | CC0", run(["sources", "list", "--file", f])[1])
            sources.add(f, "img1", "CC-BY", "https://x.example/2", "yes")
            self.assertIn("✅", run(["sources", "check", "--file", f])[1])
            self.assertEqual(len(sources.load(f)), 2)


class TitlesTests(unittest.TestCase):
    def test_deterministic_ranking(self):
        cfg = load_config()
        meta = {"title_candidates": ["충격 실화 세상에!! 믿을 수 없", "시어머니가 숨긴 세 가지 비밀", "비밀", "시어머니가 숨긴 7가지 비밀"]}
        r1 = titles.rank(meta, A, cfg)
        r2 = titles.rank(meta, A, cfg)
        self.assertEqual(r1, r2)
        self.assertEqual(r1[0]["title"], "시어머니가 숨긴 세 가지 비밀")
        self.assertEqual(r1[-1]["title"], "비밀")
        by = {r["title"]: r for r in r1}
        self.assertLess(by["시어머니가 숨긴 7가지 비밀"]["score"], by["시어머니가 숨긴 세 가지 비밀"]["score"])
        self.assertTrue(any("과장" in n for n in by["충격 실화 세상에!! 믿을 수 없"]["notes"]))

    def test_cli_reads_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            w(d, "clip.txt", A)
            p = w(d, "clip.meta.json", json.dumps({"title_candidates": ["시어머니가 숨긴 세 가지 비밀", "시어머니 비밀"]}))
            code, out = run(["titles", p])
            self.assertEqual(code, 0)
            self.assertTrue(out.startswith("1. [") and "세 가지" in out.splitlines()[0])
            self.assertEqual(out, run(["titles", p])[1])


class CheckUnchangedTests(unittest.TestCase):
    def test_check_parser_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cli.main(["check", os.path.join(d, "none.mp4"), "--quiet"]), 2)
            self.assertEqual(cli.main(["check-dir", d, "--quiet"]), 2)


if __name__ == "__main__":
    unittest.main()
