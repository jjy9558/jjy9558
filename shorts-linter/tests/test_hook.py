import contextlib
import io
import os
import tempfile
import unittest

from shortslint import cli, hookcheck, phrasebook
from shortslint.config import load_config
from shortslint.subtitles import Cue

CFG = load_config()
SILENT = ("Input #0\n  Stream #0:1: Audio: aac\n[silencedetect @ 0x1] silence_start: -0.0021\n"
          "[silencedetect @ 0x1] silence_end: 1.2 | silence_duration: 1.2\n")


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


class ScoreTests(unittest.TestCase):
    def test_greeting_is_penalised(self):
        r = hookcheck.score_hook("안녕하세요 여러분 오늘은 시어머니 이야기입니다", "시어머니가 숨긴 비밀", CFG)
        self.assertFalse(r["ok"])
        self.assertEqual(r["parts"]["도입"], 0)
        self.assertTrue(any("인사" in m for _, m in r["reasons"]))

    def test_concrete_hook_passes(self):
        r = hookcheck.score_hook("\"3억을 빌려줬는데 시어머니가 모른 척했어요\"", "시어머니가 숨긴 비밀 3억", CFG)
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(r["parts"]["도입"], 32)

    def test_missing_promise_keyword_and_length(self):
        r = hookcheck.score_hook("그날은 날씨가 참 좋았고 저는 천천히 걸어서 집으로 돌아가고 있었는데 "
                                 "길가에 꽃이 많이 피어 있었고 바람도 불어서 기분이 좋았습니다 그래서 한참을 더 걸었습니다", "시어머니가 숨긴 비밀", CFG)
        self.assertEqual(r["parts"]["약속"], 0)
        self.assertEqual(r["parts"]["길이"], 10)
        self.assertFalse(r["ok"])

    def test_no_title_is_neutral(self):
        r = hookcheck.score_hook("왜 그랬을까요?", "", CFG)
        self.assertEqual(r["parts"]["약속"], 15)


class WindowTests(unittest.TestCase):
    def test_srt_window_uses_timings(self):
        cues = [Cue(0.0, 1.5, "첫 줄"), Cue(1.5, 2.9, "둘째 줄"), Cue(3.0, 5.0, "셋째 줄")]
        txt, origin, warns = hookcheck.first_window(cues, "", CFG)
        self.assertEqual(txt, "첫 줄 둘째 줄")
        self.assertEqual(warns, [])
        self.assertIn("자막", origin)

    def test_late_first_cue_warns(self):
        txt, _, warns = hookcheck.first_window([Cue(4.0, 6.0, "늦은 말")], "", CFG)
        self.assertEqual(txt, "늦은 말")
        self.assertEqual(len(warns), 1)

    def test_text_fallback_first_sentence_and_truncation(self):
        txt, _, _ = hookcheck.first_window([], "첫 문장입니다. 두 번째 문장입니다.", CFG)
        self.assertEqual(txt, "첫 문장입니다.")
        long = "가나다라 마바사아 자차카타 파하가나 다라마바 사아자차 카타파하 " * 2
        txt, _, _ = hookcheck.first_window([], long, CFG)
        self.assertLess(len(txt.replace(" ", "")), 30)


class SuggestTests(unittest.TestCase):
    META = {"title": "시어머니가 숨긴 3가지 비밀", "claims": ["실화"]}

    def test_deterministic_and_filled(self):
        book = phrasebook.load("/nonexistent/hooks.json")
        a = phrasebook.suggest(book, self.META)
        b = phrasebook.suggest(book, self.META)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 3)
        for r in a:
            self.assertNotIn("{", r["filled"])
        self.assertEqual(a[0]["filled"], "시어머니가 비밀한 이유")

    def test_josa(self):
        self.assertEqual(phrasebook.josa("시어머니", "이/가"), "가")
        self.assertEqual(phrasebook.josa("남편", "이/가"), "이")
        self.assertEqual(phrasebook.josa("서울", "으로/로"), "로")
        self.assertEqual(phrasebook.josa("집", "으로/로"), "으로")

    def test_unfillable_templates_skipped(self):
        rows = phrasebook.suggest(phrasebook.load("/nonexistent/x.json"), {"title": "!!"})
        self.assertEqual(rows, [])


class BookTests(unittest.TestCase):
    def test_ships_about_30_defaults(self):
        n = len(phrasebook.default_templates())
        self.assertGreaterEqual(n, 30)
        self.assertEqual(len({t["id"] for t in phrasebook.default_templates()}), n)

    def test_crud_roundtrip_and_cli(self):
        with tempfile.TemporaryDirectory() as d:
            bp = os.path.join(d, "sub", "hooks.json")
            code, out = run(["hooks", "add", "--book", bp, "--text", "{인물}의 {키워드} 사건", "--tags", "테스트,갈등"])
            self.assertEqual(code, 0)
            tid = out.split(":")[1].strip()
            _, out = run(["hooks", "list", "--book", bp, "--tag", "테스트"])
            self.assertEqual(out.strip().count("\n"), 0)
            self.assertIn("{인물}의 {키워드} 사건", out)
            self.assertEqual(run(["hooks", "edit", tid, "--book", bp, "--text", "수정 {인물}"])[0], 0)
            self.assertIn("수정 {인물}", run(["hooks", "list", "--book", bp, "--tag", "테스트"])[1])
            self.assertEqual(run(["hooks", "remove", tid, "--book", bp])[0], 0)
            self.assertEqual(run(["hooks", "list", "--book", bp, "--tag", "테스트"])[1].strip(), "해당하는 템플릿이 없습니다")
            self.assertEqual(len(phrasebook.load(bp)["templates"]), 30)
            self.assertEqual(run(["hooks", "remove", "zzz", "--book", bp])[0], 2)

    def test_duplicate_rejected(self):
        book = phrasebook.load("/nonexistent/x.json")
        with self.assertRaises(ValueError):
            phrasebook.add(book, book["templates"][0]["text"])

    def test_overuse_warning(self):
        book = phrasebook.load("/nonexistent/x.json")
        phrasebook.use(book, "t01", "v1")
        phrasebook.use(book, "t02", "v2")
        self.assertFalse(phrasebook.use(book, "t01", "v1"))      # 같은 영상 중복 무시
        self.assertEqual(phrasebook.overused(book, 5, 1), {})
        phrasebook.use(book, "t01", "v3")
        self.assertEqual(phrasebook.overused(book, 5, 1), {"t01": 2})
        # 창 밖으로 밀려나면 해제
        for i in range(5):
            phrasebook.use(book, "t10", "x%d" % i)
        self.assertEqual(phrasebook.overused(book, 5, 1), {"t10": 5})
        self.assertNotIn("t01", phrasebook.overused(book, 5, 1))

    def test_overused_template_sorted_last_in_suggestions(self):
        book = phrasebook.load("/nonexistent/x.json")
        meta = TestMeta = {"title": "시어머니가 숨긴 3가지 비밀"}
        first = phrasebook.suggest(book, meta)[0]["id"]
        phrasebook.use(book, first, "a")
        phrasebook.use(book, first, "b")
        rows = phrasebook.suggest(book, meta, n=40)
        self.assertEqual(rows[-1]["id"], first)
        self.assertTrue(rows[-1]["overused"])

    def test_use_cli_prints_warning(self):
        with tempfile.TemporaryDirectory() as d:
            bp = os.path.join(d, "h.json")
            run(["hooks", "use", "t01", "--video", "a", "--book", bp])
            code, out = run(["hooks", "use", "t01", "--video", "b", "--book", bp])
            self.assertIn("⚠️ t01", out)
            self.assertEqual(code, 0)
            self.assertEqual(run(["hooks", "check", "--book", bp, "--strict"])[0], 1)

    def test_data_dir_env(self):
        old = os.environ.get("SHORTSLINT_DATA_DIR")
        os.environ["SHORTSLINT_DATA_DIR"] = "/tmp/zz-data"
        try:
            self.assertEqual(phrasebook.default_path(), os.path.join("/tmp/zz-data", "hooks.json"))
        finally:
            if old is None:
                del os.environ["SHORTSLINT_DATA_DIR"]
            else:
                os.environ["SHORTSLINT_DATA_DIR"] = old


class AudioTests(unittest.TestCase):
    def test_skip_without_ffmpeg(self):
        with tempfile.TemporaryDirectory() as d:
            v = w(d, "a.mp4", "x")
            st, msg = hookcheck.check_audio_start(v, CFG, which=lambda: None)
            self.assertEqual(st, "skip")
            self.assertIn("ffmpeg", msg)

    def test_skip_without_video(self):
        self.assertEqual(hookcheck.check_audio_start(None, CFG)[0], "skip")

    def test_late_audio_warns_and_early_ok(self):
        with tempfile.TemporaryDirectory() as d:
            v = w(d, "a.mp4", "x")
            st, _ = hookcheck.check_audio_start(v, CFG, run_fn=lambda c, t: (0, "", SILENT), which=lambda: "ffmpeg")
            self.assertEqual(st, "warn")
            early = "Stream #0:1: Audio\n[silencedetect @ 0x1] silence_start: 2.0\n"
            st, _ = hookcheck.check_audio_start(v, CFG, run_fn=lambda c, t: (0, "", early), which=lambda: "ffmpeg")
            self.assertEqual(st, "ok")


class EndToEndTests(unittest.TestCase):
    def test_cli_hook_on_meta_with_srt(self):
        with tempfile.TemporaryDirectory() as d:
            w(d, "a.meta.json", '{"title": "시어머니가 숨긴 3가지 비밀"}')
            w(d, "a.srt", "1\n00:00:00,000 --> 00:00:02,000\n안녕하세요 여러분\n\n"
                          "2\n00:00:05,000 --> 00:00:07,000\n뒤의 말\n")
            bp = os.path.join(d, "book.json")
            code, out1 = run(["hook", os.path.join(d, "a.meta.json"), "--book", bp])
            _, out2 = run(["hook", os.path.join(d, "a.meta.json"), "--book", bp])
            self.assertEqual(out1, out2)
            self.assertEqual(code, 0)
            self.assertTrue(out1.startswith("⚠️ 첫 3초 훅 점검"))
            self.assertIn('"안녕하세요 여러분"', out1)
            self.assertNotIn("뒤의 말", out1)
            self.assertIn("재작성 제안", out1)
            self.assertIn("⏭️ 오디오 시작 점검 건너뜀", out1)
            self.assertEqual(run(["hook", os.path.join(d, "a.meta.json"), "--book", bp, "--strict"])[0], 1)

    def test_txt_input_and_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            t = w(d, "b.txt", "남편이 3년간 숨긴 통장을 발견했습니다. 그 뒤로 벌어진 일.")
            code, out = run(["hook", t, "--no-audio", "--book", os.path.join(d, "x.json")])
            self.assertEqual(code, 0)
            self.assertIn("대본 첫 문장", out)
            self.assertEqual(run(["hook", os.path.join(d, "none.txt")])[0], 2)


if __name__ == "__main__":
    unittest.main()
