# -*- coding: utf-8 -*-
import unittest

from vjournal.parse import find_number, parse_tags, split_sentences, validate_mood
from tests.helpers import Base


class EntryTests(Base):
    def test_tags(self):
        self.assertEqual(parse_tags("오늘은 #블로그 와 #Python_3 그리고 #블로그 #123 #운동!"),
                         ["블로그", "python_3", "운동"])
        self.assertEqual(parse_tags("주소 http://x.com/#top 은 태그 아님"), [])
        self.assertEqual(parse_tags("태그 없음"), [])

    def test_mood(self):
        self.assertIsNone(validate_mood(None))
        self.assertEqual(validate_mood("4"), 4)
        for bad in (0, 6, "abc", 2.5):
            with self.assertRaises(ValueError):
                validate_mood(bad)

    def test_numbers(self):
        self.assertEqual(find_number("블로그 2개"), (2, "개"))
        self.assertEqual(find_number("책 한 권"), (1, "권"))
        self.assertEqual(find_number("두 번 뛴다"), (2, "번"))
        self.assertEqual(find_number("3.5km"), (3.5, "km"))
        self.assertIsNone(find_number("숫자 없음"))

    def test_sentences(self):
        self.assertEqual(split_sentences("하나. 둘!\n셋"), ["하나.", "둘!", "셋"])

    def test_add_and_read(self):
        j = self.mk()
        r = j.add_entry("오늘 날씨가 좋았다 #일상 #산책", mood=4)
        self.assertEqual(r["tags"], ["일상", "산책"])
        e = j.get_entry(r["id"])
        self.assertEqual(e["mood"], 4)
        self.assertEqual(e["ts"], "2026-11-02 09:00:00")
        self.assertEqual(e["tags"], ["일상", "산책"])
        self.assertEqual([x["id"] for x in j.entries(tag="#산책")], [r["id"]])

    def test_empty_and_bad_mood_rejected(self):
        j = self.mk()
        with self.assertRaises(ValueError):
            j.add_entry("   ")
        with self.assertRaises(ValueError):
            j.add_entry("글", mood=9)

    def test_flag_by_tag(self):
        j = self.mk()
        r = j.add_entry("내일 병원 예약 #중요")
        self.assertTrue(r["flagged"])
        self.assertEqual(len(j.flagged()), 1)
        j.set_flag(r["id"], False)
        self.assertEqual(j.flagged(), [])


if __name__ == "__main__":
    unittest.main()
