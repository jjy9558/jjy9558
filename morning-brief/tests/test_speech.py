import datetime as dt
import unittest

from helpers import NOW  # noqa: F401  (sys.path 설정)
from mbrief import speech as S


class SpeechTests(unittest.TestCase):
    def test_dates(self):
        today = dt.date(2026, 11, 2)
        self.assertEqual(S.ko_date(today), "11월 2일 월요일")
        self.assertEqual(S.ko_date(today, today), "오늘")
        self.assertEqual(S.when_phrase(dt.date(2026, 11, 3), today), "내일")
        self.assertEqual(S.when_phrase(dt.date(2026, 11, 4), today), "모레")
        self.assertEqual(S.when_phrase(dt.date(2026, 11, 5), today), "3일 뒤")
        self.assertEqual(S.when_phrase(dt.date(2026, 11, 12), today), "11월 12일 목요일")
        self.assertEqual(S.when_phrase(dt.date(2026, 10, 30), today), "3일 지남")
        self.assertEqual(S.ko_date(dt.date(2027, 1, 5), today), "2027년 1월 5일 화요일")

    def test_time_and_money(self):
        self.assertEqual(S.ko_time(10, 0), "오전 10시")
        self.assertEqual(S.ko_time(15, 30), "오후 3시 30분")
        self.assertEqual(S.ko_time(2, 10), "새벽 2시 10분")
        self.assertEqual(S.ko_time(0, 0), "자정")
        self.assertEqual(S.ko_time(12, 0), "정오")
        self.assertEqual(S.ko_time(23, 5), "밤 11시 5분")
        self.assertEqual(S.ko_won(45200), "4만 5천 2백원")
        self.assertEqual(S.ko_won(324000), "32만 4천원")
        self.assertEqual(S.ko_won(100_000_000), "1억원")
        self.assertEqual(S.won_text(45200), "45,200원")

    def test_speechify(self):
        s = S.speechify("가스비 D-2, 넷플릭스 D-DAY, 2026-11-05 10:30 점검, 12:00에 보자, 45,200원, 50%")
        self.assertNotIn("D-", s)
        self.assertNotRegex(s, r"\d{4}-\d{2}")
        self.assertNotRegex(s, r"\d:\d{2}")
        self.assertIn("2일 뒤", s)
        self.assertIn("오늘", s)
        self.assertIn("11월 5일 오전 10시 30분", s)
        self.assertIn("정오", s)
        self.assertIn("4만 5천 2백원", s)
        self.assertIn("50 퍼센트", s)
        self.assertEqual(S.speechify(s), s)   # 멱등

    def test_speechify_strips_markup_and_urls(self):
        s = S.speechify("**굵게** [링크](https://example.com/x?a=1) 와 `코드` #제목")
        self.assertNotIn("http", s)
        self.assertNotIn("*", s)
        self.assertNotIn("`", s)

    def test_shorten(self):
        self.assertEqual(S.shorten("짧다", 10), "짧다")
        out = S.shorten("가나다 라마바 사아자 차카타", 9)
        self.assertLessEqual(len(out), 9)
        self.assertTrue(out.endswith("…"))


if __name__ == "__main__":
    unittest.main()
