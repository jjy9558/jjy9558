# -*- coding: utf-8 -*-
import io
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime

from vjournal import checkout as C
from vjournal.cli import main
from tests.helpers import Base, at


class CheckoutTests(Base):
    def test_questions_rotate_but_keys_fixed(self):
        a = C.questions_for(date(2026, 11, 2))
        b = C.questions_for(date(2026, 11, 3))
        self.assertEqual([k for k, _ in a], ["win", "blocked", "next"])
        self.assertNotEqual([q for _, q in a], [q for _, q in b])
        texts = {q for d in range(3) for _, q in C.questions_for(date(2026, 11, 2 + d))}
        self.assertIn("오늘 제일 잘한 거?", texts)
        self.assertIn("막힌 거?", texts)
        self.assertIn("내일 첫 할 일?", texts)

    def test_interactive_checkout_and_morning(self):
        j = self.mk()
        at(j, 2026, 11, 2, 18)
        answers = iter(["API 서버 붙임", "", "로그인 버그부터 잡기"])
        asked = []

        def ask(q):
            asked.append(q)
            return next(answers)

        saved = C.run_checkout(j, ask, out=lambda s: None)
        self.assertEqual(len(saved), 2)
        self.assertEqual(len(asked), 3)
        self.assertEqual(j.checkout_answers("2026-11-02"), {"win": "API 서버 붙임", "next": "로그인 버그부터 잡기"})

        at(j, 2026, 11, 3, 9)
        m = C.morning(j)
        self.assertEqual(m["next"], "로그인 버그부터 잡기")
        self.assertTrue(m["is_yesterday"])
        txt = C.render_morning(m)
        self.assertIn("로그인 버그부터 잡기", txt)

    def test_morning_flagged_and_due_and_fallback_day(self):
        j = self.mk()
        at(j, 2026, 11, 2, 18)
        C.save_answers(j, {"next": "PR 리뷰", "blocked": "CI 느림"})
        j.add_entry("세금 서류 챙기기 #중요")
        j.add_entry("내일까지 보고서 끝낸다")
        at(j, 2026, 11, 5, 9)   # 이틀 뒤: 어제 기록 없음 -> 예전 기록 + 표시
        m = C.morning(j)
        self.assertFalse(m["is_yesterday"])
        self.assertEqual(m["source_day"], "2026-11-02")
        self.assertEqual(len(m["flagged"]), 1)
        at(j, 2026, 11, 3, 9)
        m = C.morning(j)
        self.assertEqual([g["title"] for g in m["due_goals"]], ["보고서 끝낸다"])
        out = C.render_morning(m)
        self.assertIn("세금 서류", out)
        self.assertIn("CI 느림", out)

    def test_morning_without_data(self):
        j = self.mk()
        self.assertIn("기록이 없어요", C.render_morning(C.morning(j)))

    def test_unknown_key_rejected(self):
        j = self.mk()
        with self.assertRaises(ValueError):
            C.save_answers(j, {"foo": "x"})

    def test_cli_checkout_with_scripted_input(self):
        import os, tempfile
        d = tempfile.mkdtemp()
        db = os.path.join(d, "j.db")
        now = lambda: datetime(2026, 11, 2, 18, 0)
        answers = iter(["잘함", "막힘", "내일 할 일"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--db", db, "checkout"], now=now, ask=lambda q: next(answers))
            rc2 = main(["--db", db, "morning"], now=lambda: datetime(2026, 11, 3, 9, 0))
        self.assertEqual((rc, rc2), (0, 0))
        self.assertIn("내일 할 일", buf.getvalue())

    def test_cli_add_stdin(self):
        import os, tempfile
        db = os.path.join(tempfile.mkdtemp(), "j.db")
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["--db", db, "add", "-", "--mood", "5"], stdin=io.StringIO("표준입력 일기 #테스트\n둘째 줄"))
            main(["--db", db, "list"])
        self.assertIn("표준입력 일기", buf.getvalue())
        self.assertIn("#테스트", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
