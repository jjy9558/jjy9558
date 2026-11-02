# -*- coding: utf-8 -*-
import unittest

from vjournal.search import find_markers, search_entries, tokenize, why
from tests.helpers import Base, at


class SearchTests(Base):
    def test_tokenize_bigrams(self):
        self.assertEqual(tokenize("블로그를"), ["블로", "로그", "그를"])
        self.assertEqual(tokenize("API를 호출"), ["api", "호출"])   # 영단어 뒤 조사 한 글자는 버림
        self.assertEqual(tokenize("책"), ["책"])

    def test_ranking(self):
        j = self.mk()
        ids = {}
        for k, t in [("a", "오늘 블로그를 썼다. 블로그 글감이 많다"),
                     ("b", "블로그 이야기를 잠깐 했다 그리고 점심은 김치찌개를 먹었다 오래오래 긴 하루였다 정말"),
                     ("c", "로그인 화면 버그를 잡았다"),
                     ("d", "산책을 했다")]:
            ids[k] = j.add_entry(t, auto_goals=False)["id"]
        hits = search_entries(j.entries(), "블로그")
        order = [e["id"] for _, e in hits]
        self.assertEqual(order[0], ids["a"])
        self.assertIn(ids["b"], order)
        self.assertNotIn(ids["c"], order)   # '로그' 한 bigram 만 겹치는 건 제외
        self.assertNotIn(ids["d"], order)
        self.assertEqual(search_entries(j.entries(), ""), [])

    def test_particles_and_multiword(self):
        j = self.mk()
        a = j.add_entry("스터디카페에서 공부했다", auto_goals=False)["id"]
        j.add_entry("카페에서 커피만 마셨다", auto_goals=False)
        hits = search_entries(j.entries(), "스터디카페 공부")
        self.assertEqual(hits[0][1]["id"], a)

    def test_tag_search(self):
        j = self.mk()
        a = j.add_entry("그냥 기록 #리팩토링", auto_goals=False)["id"]
        self.assertEqual([e["id"] for _, e in search_entries(j.entries(), "리팩토링")], [a])

    def test_markers(self):
        self.assertIn("접었", find_markers("블로그는 접었다"))
        self.assertIn("미루", find_markers("내일로 미뤘다"))
        self.assertIn("안 하기로", find_markers("이제 안 하기로 했다"))
        self.assertIn("때문에", find_markers("야근 때문에 못 썼다"))
        self.assertEqual(find_markers("오늘은 맑음"), [])

    def test_why_timeline(self):
        j = self.mk()
        at(j, 2026, 11, 2)
        j.add_entry("블로그 쓰는 게 즐겁다. 오늘 한 편 썼다", auto_goals=False)
        at(j, 2026, 11, 9)
        j.add_entry("야근 때문에 블로그를 못 썼다. 점심은 맛있었다", auto_goals=False)
        at(j, 2026, 11, 5)
        j.add_entry("블로그 소재가 없어서 일단 미루기로 했다", auto_goals=False)
        at(j, 2026, 11, 12)
        j.add_entry("결국 블로그는 접었다. 그래서 일기에 집중한다", auto_goals=False)
        j.add_entry("무관한 이야기 때문에 피곤", auto_goals=False)
        items = why(j.entries(), "블로그")
        self.assertEqual([i["ts"][:10] for i in items], ["2026-11-05", "2026-11-09", "2026-11-12", "2026-11-12"])
        self.assertTrue(items[0]["sentence"].endswith("미루기로 했다"))
        self.assertIn("접었", items[2]["markers"])
        self.assertTrue(all("피곤" not in i["sentence"] for i in items))

    def test_why_includes_dropped_goal_and_fallback(self):
        j = self.mk()
        j.add_entry("이번 주에 블로그 2개 쓴다")
        at(j, 2026, 11, 4)
        j.add_entry("블로그 포기")
        items = why(j.entries(), "블로그", j.goals())
        self.assertTrue(any("[목표 접음]" in i["sentence"] for i in items))
        # 부분 일치 폴백: 정확한 부분 문자열이 없어도 bigram 검색으로 찾는다
        j.add_entry("스터디 모임은 시간이 없어서 그만뒀다")
        self.assertTrue(why(j.entries(), "모임 스터디"))


if __name__ == "__main__":
    unittest.main()
