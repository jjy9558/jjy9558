# -*- coding: utf-8 -*-
import unittest
from datetime import date, datetime

from vjournal.goals import detect_declarations
from vjournal.grading import grade_goal, overall_grade, ratio_to_grade
from vjournal.report import build_scorecard, render_scorecard_html, render_scorecard_md
from tests.helpers import Base, at

MON = date(2026, 11, 2)


class DeclarationTests(unittest.TestCase):
    def one(self, text, today=MON):
        ds = detect_declarations(text, today)
        self.assertEqual(len(ds), 1, (text, ds))
        return ds[0]

    def test_this_week_count(self):
        d = self.one("이번 주에 블로그 2개 쓴다")
        self.assertEqual((d.period, d.target, d.unit, d.deadline), ("once", 2, "개", date(2026, 11, 8)))
        self.assertEqual(d.keywords, ["블로그"])

    def test_daily(self):
        d = self.one("매일 푸시업 30개")
        self.assertEqual((d.period, d.target, d.unit), ("day", 30, "개"))
        d = self.one("매일 아침 스트레칭 10분 할 거야")
        self.assertEqual(d.period, "day")

    def test_weekday_deadline(self):
        d = self.one("금요일까지 보고서 끝낸다")
        self.assertEqual(d.deadline, date(2026, 11, 6))
        self.assertEqual(d.keywords, ["보고서"])
        d = self.one("월요일까지 보고서 끝낸다")   # 오늘이 월요일 -> 오늘
        self.assertEqual(d.deadline, MON)

    def test_phrasings(self):
        cases = {
            "내일까지 리팩토링 끝내자": date(2026, 11, 3),
            "다음 주 수요일까지 기획서 낼 거야": date(2026, 11, 11),
            "이번 주말까지 방 청소 할게": date(2026, 11, 8),
            "11월 20일까지 책 한 권 읽겠다": date(2026, 11, 20),
            "3일 안에 포트폴리오 완성한다": date(2026, 11, 5),
            "이번 달 안에 5kg 뺀다": date(2026, 11, 30),
            "모레까지 발표자료 만들겠습니다": date(2026, 11, 4),
            "15일까지 세금 신고 해야지": date(2026, 11, 15),
            "이번 주 안에 코드리뷰 마친다": date(2026, 11, 8),
            "다음 주까지 이사 준비 하기로 했다": date(2026, 11, 15),
        }
        for text, dl in cases.items():
            self.assertEqual(self.one(text).deadline, dl, text)

    def test_weekly_and_start(self):
        d = self.one("주 3회 운동할게")
        self.assertEqual((d.period, d.target, d.unit), ("week", 3, "회"))
        self.assertEqual(d.keywords, ["운동"])
        d = self.one("내일부터 매일 스트레칭 10분")
        self.assertEqual((d.period, d.start), ("day", date(2026, 11, 3)))
        d = self.one("매주 책 한 권 읽는다")
        self.assertEqual((d.period, d.target), ("week", 1))

    def test_goal_word(self):
        d = self.one("목표: 영어 공부")
        self.assertEqual(d.keywords, ["영어", "공부"])

    def test_multiple_in_one_entry(self):
        ds = detect_declarations("오늘 피곤했다. 이번 주에 블로그 2개 쓴다. 매일 푸시업 30개\n금요일까지 X 끝낸다", MON)
        self.assertEqual(len(ds), 3)

    def test_not_declarations(self):
        for text in ["오늘 푸시업 30개 했다", "이번 주에 블로그 2개 썼다", "이번 주에 비 올까?",
                     "이번 주에 운동 안 한다", "그냥 평범한 하루였다", "블로그 쓴다",
                     "매일 커피 3잔 마셨다", "금요일까지 못 끝낸다", "이번 주 블로그 포기한다"]:
            self.assertEqual(detect_declarations(text, MON), [], text)

    def test_year_rollover(self):
        d = self.one("1월 5일까지 계약서 쓸 거야", today=date(2026, 12, 20))
        self.assertEqual(d.deadline, date(2027, 1, 5))


class ProgressAndGradeTests(Base):
    def test_progress_marks_done_and_grade_A(self):
        j = self.mk()
        r = j.add_entry("이번 주에 블로그 2개 쓴다")
        self.assertEqual(len(r["goals_created"]), 1)
        at(j, 2026, 11, 3)
        r = j.add_entry("블로그 1개 썼다")
        self.assertEqual(r["progress"][0]["amount"], 1)
        at(j, 2026, 11, 4)
        j.add_entry("블로그 하나 더 썼다 #블로그")
        g = j.goals()[0]
        self.assertEqual(g["status"], "done")
        at(j, 2026, 11, 9, 9)
        sc = build_scorecard(j, date(2026, 11, 2))
        self.assertEqual(sc["results"][0]["grade"], "A")
        self.assertEqual(sc["grade"], "A")
        self.assertIn("A", render_scorecard_md(sc))

    def test_no_progress_after_deadline_is_F(self):
        j = self.mk()
        j.add_entry("금요일까지 보고서 끝낸다")
        at(j, 2026, 11, 8)
        sc = build_scorecard(j)
        self.assertEqual(sc["results"][0]["grade"], "F")
        self.assertTrue(sc["results"][0]["final"])

    def test_partial_and_negated(self):
        j = self.mk()
        j.add_entry("이번 주에 블로그 2개 쓴다")
        at(j, 2026, 11, 3)
        r = j.add_entry("블로그 못 썼다")
        self.assertEqual(r["progress"], [])
        r = j.add_entry("블로그 쓰려고 했는데 피곤했다")
        self.assertEqual(r["progress"], [])
        r = j.add_entry("블로그 1개 썼다")
        self.assertEqual(len(r["progress"]), 1)
        at(j, 2026, 11, 9)
        sc = build_scorecard(j, date(2026, 11, 2))
        res = sc["results"][0]
        self.assertAlmostEqual(res["ratio"], 0.4)   # 1/2 * 0.8
        self.assertEqual(res["grade"], "D")

    def test_late_completion(self):
        j = self.mk()
        j.add_entry("내일까지 리팩토링 끝낸다")
        at(j, 2026, 11, 5)
        j.add_entry("리팩토링 끝냈다")
        g = j.goals()[0]
        self.assertEqual(g["status"], "done")
        res = grade_goal(g, j.goal_events(1), "2026-11-02", "2026-11-08", date(2026, 11, 9))
        self.assertEqual(res["grade"], "C")   # 2일 늦음 -> 0.6

    def test_daily_goal_grading(self):
        j = self.mk()
        j.add_entry("매일 푸시업 30개")
        for day, txt in [(2, "푸시업 30개 했다"), (3, "푸시업 30개 했다"), (4, "푸시업 10개 했다"),
                         (5, "푸시업 했다"), (6, "푸시업 30개 했다")]:
            at(j, 2026, 11, day)
            j.add_entry(txt)
        at(j, 2026, 11, 9)
        sc = build_scorecard(j, date(2026, 11, 2))
        res = sc["results"][0]
        # 월~일 7일 중 월,화,목(amount=target),금 = 4일
        self.assertEqual(res["progress"], 4)
        self.assertAlmostEqual(res["ratio"], 4 / 7.0)
        self.assertEqual(res["grade"], "C")

    def test_drop_detection(self):
        j = self.mk()
        j.add_entry("이번 주에 블로그 2개 쓴다")
        at(j, 2026, 11, 4)
        r = j.add_entry("블로그는 시간이 없어서 포기했다")
        self.assertEqual(r["progress"][0]["kind"], "drop")
        self.assertEqual(j.goals()[0]["status"], "dropped")
        sc = build_scorecard(j)
        self.assertEqual(sc["results"][0]["grade"], "F")
        self.assertIn("접", sc["results"][0]["comment"])

    def test_dedupe_declaration(self):
        j = self.mk()
        j.add_entry("이번 주에 블로그 2개 쓴다")
        r = j.add_entry("이번 주에 블로그 2개 쓴다 진짜로")
        self.assertEqual(r["goals_created"], [])
        self.assertEqual(len(j.goals()), 1)

    def test_manual_updates(self):
        j = self.mk()
        j.add_entry("금요일까지 보고서 끝낸다")
        j.complete_goal(1)
        self.assertEqual(j.goals()[0]["status"], "done")

    def test_grade_thresholds_and_overall(self):
        self.assertEqual([ratio_to_grade(x) for x in (1, .9, .8, .6, .4, .1)], list("AABCDF"))
        self.assertEqual(overall_grade([]), (None, None))
        self.assertEqual(overall_grade([{"ratio": 1.0}, {"ratio": 0.5}])[0], "B")

    def test_empty_week_and_html(self):
        j = self.mk()
        sc = build_scorecard(j)
        self.assertIsNone(sc["grade"])
        self.assertIn("선언이 없었다", sc["comment"])
        j.add_entry("이번 주에 <script>alert(1)</script> 2개 쓴다")
        html = render_scorecard_html(build_scorecard(j))
        self.assertNotIn("<script>alert", html)
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertNotIn("http://", html)

    def test_comment_deterministic(self):
        j = self.mk()
        j.add_entry("금요일까지 보고서 끝낸다")
        at(j, 2026, 11, 9)
        a = build_scorecard(j, date(2026, 11, 2))["results"][0]["comment"]
        b = build_scorecard(j, date(2026, 11, 2))["results"][0]["comment"]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
