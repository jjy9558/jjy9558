import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from lifecourt import courts, engine, store
from lifecourt._jjkit.textwidth import width
from lifecourt.cli import main

NOW = datetime(2026, 11, 2, 23, 30)


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name).resolve()
        p = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    def run_cli(self, argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        rc = main(argv, out, err=err, now=NOW, env=kw.pop("env", {}), **kw)
        return rc, out.getvalue(), err.getvalue()


class Determinism(Base):
    def test_same_input_same_verdict(self):
        a = ["night-snack", "--food", "치킨", "--time", "23:40", "--kcal", "900", "--no-save", "--date", "2026-11-02"]
        self.assertEqual(self.run_cli(a)[1], self.run_cli(a)[1])
        b = self.run_cli(a + ["--seed", "다른 시드"])[1]
        self.assertNotEqual(self.run_cli(a)[1], b)

    def test_bet_and_toast_deterministic(self):
        bet = ["bet", "--a", "민수", "--b", "지연", "--condition", "축구", "--winner", "민수"]
        self.assertEqual(self.run_cli(bet)[1], self.run_cli(bet)[1])
        toast = ["toast", "민수:개발자", "지연", "--occasion", "송년회"]
        self.assertEqual(self.run_cli(toast)[1], self.run_cli(toast)[1])


class Recidivism(Base):
    def test_priors_raise_score_and_sentence(self):
        scores = []
        for day in ("2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"):
            rc, out, _ = self.run_cli(["night-snack", "--food", "라면", "--time", "23:10", "--date", day,
                                       "--seed", "고정"])
            self.assertEqual(rc, 0)
            scores.append(int(out.split("점수 ")[1].split(")")[0]))
        self.assertEqual(scores, sorted(scores))
        self.assertGreater(scores[-1], scores[0])
        log = store.Log(self.home / ".life-court", "night")
        self.assertEqual(len(log.load()), 4)

    def test_rerun_does_not_inflate(self):
        a = ["night-snack", "--food", "치킨", "--time", "23:40", "--date", "2026-11-02", "--seed", "s"]
        first = self.run_cli(a)[1]
        self.assertEqual(self.run_cli(a)[1], first)

    def test_old_records_ignored(self):
        self.run_cli(["night-snack", "--food", "치킨", "--time", "23:40", "--date", "2026-10-01"])
        _, out, _ = self.run_cli(["night-snack", "--food", "치킨", "--time", "23:40", "--date", "2026-11-02"])
        self.assertIn("전과 없음", out)


class LateConsent(Base):
    ARGS = ["late", "--minutes", "20", "--meeting", "점심"]

    def test_requires_consent(self):
        rc, out, err = self.run_cli(self.ARGS)
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("동의", err)
        self.assertFalse((self.home / ".life-court").exists())

    def test_self_defendant_ok(self):
        rc, out, _ = self.run_cli(self.ARGS + ["--consent", "--me", "정윤"])
        self.assertEqual(rc, 0)
        self.assertIn("정윤", out)
        self.assertIn("자진", out)

    def test_other_defendant_needs_agreement(self):
        rc, out, err = self.run_cli(self.ARGS + ["--consent", "--me", "정윤", "--defendant", "철수"])
        self.assertEqual(rc, 2)
        self.assertIn("본인", err)
        rc, out, _ = self.run_cli(self.ARGS + ["--consent", "--me", "정윤", "--defendant", "철수",
                                              "--other-agreed"])
        self.assertEqual(rc, 0)
        self.assertIn("철수", out)

    def test_prior_lates_add_cups_and_forget(self):
        base = self.ARGS + ["--consent", "--seed", "x"]
        for d in ("2026-10-20", "2026-10-25", "2026-10-30"):
            self.run_cli(base + ["--date", d])
        _, out, _ = self.run_cli(base + ["--date", "2026-11-02"])
        self.assertIn("전력 3건", out)
        self.run_cli(["forget", "--who", "나"])
        _, out, _ = self.run_cli(base + ["--date", "2026-11-02", "--no-save"])
        self.assertIn("전력 없음", out)


class DataStaysInHome(Base):
    def test_outside_home_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            other = str(Path(other).resolve())
            rc, _, err = self.run_cli(["late", "--consent", "--minutes", "5", "--meeting", "a",
                                       "--data-dir", other])
            self.assertEqual(rc, 2)
            self.assertIn("홈 폴더", err)
            self.assertEqual(list(Path(other).iterdir()), [])
            with self.assertRaises(store.StoreError):
                store.data_dir("..", {})  # 홈의 부모
            rc, _, _ = self.run_cli(["night-snack", "--food", "x", "--data-dir", other])
            self.assertEqual(rc, 2)

    def test_env_dir_checked_and_friend_file_inside_home(self):
        rc, _, _ = self.run_cli(["late", "--consent", "--minutes", "5", "--meeting", "a", "--me", "A",
                                 "--defendant", "친구", "--other-agreed", "--data-dir", str(self.home / "d")])
        self.assertEqual(rc, 0)
        f = self.home / "d" / "late.json"
        self.assertTrue(f.is_file())
        self.assertIn("친구", f.read_text(encoding="utf-8"))
        rc, _, _ = self.run_cli(["forget", "--late"], env={"LIFECOURT_DIR": "/"})
        self.assertEqual(rc, 2)

    def test_bet_and_toast_store_nothing(self):
        self.run_cli(["bet", "--a", "가", "--b", "나", "--condition", "c", "--winner", "가"])
        self.run_cli(["toast", "가", "나"])
        self.assertFalse((self.home / ".life-court").exists())


class EmptyInput(Base):
    def test_messages(self):
        cases = [
            (["night-snack", "--food", "  "], "음식 이름"),
            (["bet", "--a", "", "--b", "나", "--condition", "c", "--winner", "나"], "첫 번째 이름"),
            (["bet", "--a", "가", "--b", "나", "--condition", "", "--winner", "나"], "내기 조건"),
            (["bet", "--a", "가", "--b", "나", "--condition", "c", "--winner", "다"], "승자는"),
            (["toast"], "이름이 비었습니다"),
            (["toast", " ", ":"], "이름이 비었습니다"),
            (["late", "--consent", "--minutes", "10", "--meeting", ""], "약속"),
            (["late", "--consent", "--minutes", "0", "--meeting", "a"], "지각 분"),
            (["night-snack", "--food", "a", "--time", "25:00"], "시각"),
        ]
        for argv, needle in cases:
            rc, out, err = self.run_cli(argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn(needle, err, argv)
            self.assertEqual(out, "", argv)


class Layout(Base):
    def test_box_lines_have_equal_display_width(self):
        runs = [["night-snack", "--food", "치킨 두 마리와 매운 떡볶이", "--time", "01:10", "--kcal", "2500",
                 "--count", "3", "--no-save"],
                ["late", "--consent", "--minutes", "45", "--meeting", "아주아주긴약속이름그리고또길게",
                 "--excuse", "알람이 안 울렸어요", "--no-save"],
                ["bet", "--a", "민수", "--b", "지연", "--condition", "축구 승부 예측 내기", "--winner", "무승부"],
                ["toast", "민수:개발자", "지연:디자이너", "--occasion", "송년회"]]
        for argv in runs:
            rc, out, _ = self.run_cli(argv)
            self.assertEqual(rc, 0, argv)
            widths = {width(ln) for ln in out.splitlines()}
            self.assertEqual(len(widths), 1, (argv, widths))

    def test_wrap_long_word(self):
        for ln in engine.wrap("가" * 50, 20):
            self.assertLessEqual(width(ln), 20)


class Outputs(Base):
    def test_md_and_html_files(self):
        md, ht = self.home / "o" / "a.md", self.home / "o" / "a.html"
        rc, out, err = self.run_cli(["bet", "--a", "<b>민수", "--b", "지연", "--condition", "x&y",
                                     "--winner", "지연", "--md", str(md), "--html", str(ht)])
        self.assertEqual(rc, 0)
        self.assertIn("# 내 기 판 결 문", md.read_text(encoding="utf-8"))
        h = ht.read_text(encoding="utf-8")
        self.assertIn("&lt;b&gt;민수", h)
        self.assertNotIn("<b>민수", h)
        self.assertIn("@media print", h)
        self.assertNotIn("http://", h)
        self.assertNotIn("src=", h)

    def test_josa(self):
        self.assertEqual(engine.fix_josa("치킨을(를) 민수은(는) 지연이(가)"), "치킨을 민수는 지연이")
        self.assertEqual(engine.fix_josa("'라면'을(를)"), "'라면'을")


class Speak(Base):
    def test_speak_posts_ruling(self):
        calls = []

        class Opener:
            def open(self, req, timeout=None):
                calls.append((req.full_url, dict(req.header_items()), req.data))
                return FakeResp()

        opener = Opener()

        env = {"LIFECOURT_SIDEKICK_TOKEN": "t" + "k" * 6, "LIFECOURT_SIDEKICK_URL": "http://127.0.0.1:1"}
        rc, _, err = self.run_cli(["bet", "--a", "가", "--b", "나", "--condition", "c", "--winner", "가",
                                   "--speak"], env=env, opener=opener)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith("/api/say"))
        self.assertIn("승소", json.loads(calls[0][2])["text"])

    def test_speak_without_token_warns(self):
        rc, _, err = self.run_cli(["toast", "가", "--speak"])
        self.assertEqual(rc, 0)
        self.assertIn("TOKEN", err)


class FakeResp:
    status = 200

    def read(self, *a):
        return b'{"ok": true}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return 200


class FakeLLM:
    def __init__(self, fn):
        self.fn = fn

    def chat_json(self, messages, **kw):
        return self.fn(json.loads(messages[0]["content"].split("\n", 1)[1]))


class LlmPolish(Base):
    ARGS = ["bet", "--a", "민수", "--b", "지연", "--condition", "축구 3대1", "--winner", "민수", "--llm"]

    def test_valid_rewrite_used(self):
        _, plain, _ = self.run_cli(self.ARGS[:-1])
        rc, out, err = self.run_cli(self.ARGS, llm_client=FakeLLM(lambda t: [x + " (다듬음)" for x in t]))
        self.assertIn("(다듬음)", out)
        self.assertNotIn("(다듬음)", plain)

    def test_fact_loss_falls_back(self):
        _, plain, _ = self.run_cli(self.ARGS[:-1])
        rc, out, err = self.run_cli(self.ARGS, llm_client=FakeLLM(lambda t: ["내용 없음"] * len(t)))
        self.assertEqual(out, plain)
        self.assertIn("원문", err)

    def test_no_url_falls_back(self):
        rc, out, err = self.run_cli(self.ARGS)
        self.assertEqual(rc, 0)
        self.assertIn("LLM", err)

    def test_remote_llm_refused(self):
        rc, out, err = self.run_cli(self.ARGS, env={"LIFECOURT_LLM_URL": "https://example.com/v1"})
        self.assertEqual(rc, 0)
        self.assertIn("경고", err)


class Misc(Base):
    def test_draw_and_same_names(self):
        j = courts.judge_bet("가", "나", "c", "무승부", date(2026, 1, 1))
        self.assertIn("반씩", "".join(j.sections[0][1]))
        with self.assertRaises(courts.CourtError):
            courts.judge_bet("가", "가", "c", "가", date(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
