import unittest

import helpers
from helpers import NOW, at, jpeg, obj
from objmem import ask as askmod
from objmem import synonyms


class SynonymTest(unittest.TestCase):
    def test_groups(self):
        for a, b in (("리모콘", "리모컨"), ("remote", "리모컨"), ("Wallet", "지갑"), ("키", "열쇠"), ("key", "열쇠"),
                     ("박스", "택배"), ("택배 상자", "택배"), ("핸드폰", "휴대폰"), ("거실 리모컨", "리모컨")):
            self.assertEqual(synonyms.canonical(a), b, a)

    def test_unknown_and_short(self):
        self.assertIsNone(synonyms.canonical("모니터"))
        self.assertIsNone(synonyms.canonical("키링"))      # '키' 부분 포함으로 열쇠가 되면 안 됨
        self.assertEqual(synonyms.canonical("키보드"), "키보드")
        self.assertEqual(synonyms.expand("모니터"), ["모니터"])
        self.assertIn("wallet", synonyms.expand("지갑"))

    def test_grams(self):
        self.assertEqual(synonyms.grams("리모컨"), ["리모", "모컨"])
        self.assertEqual(synonyms.grams("키"), ["키"])
        self.assertEqual(synonyms.grams(" 리 모-컨 "), ["리모", "모컨"])


class ParseQueryTest(unittest.TestCase):
    CASES = [
        ("리모컨 어디 뒀지?", "리모컨", ""),
        ("리모컨 어디 있지", "리모컨", ""),
        ("내 리모컨 어디다 놨더라", "리모컨", ""),
        ("어제 택배 상자 어디 놨더라", "택배 상자", "어제"),
        ("내 지갑 어디있어", "지갑", ""),
        ("지갑이 어디 있더라?", "지갑", ""),
        ("열쇠 어디에 두었더라", "열쇠", ""),
        ("열쇠를 어디에 뒀는지 모르겠어", "열쇠", ""),
        ("핸드폰 마지막으로 본 게 언제야", "핸드폰", ""),
        ("3일 전에 우산 어디 있었어?", "우산", "3일 전"),
        ("where is my remote?", "remote", ""),
        ("Where's my wallet", "wallet", ""),
        ("리모콘이 어디갔지", "리모콘", ""),
        ("지난주 안경 어디 있었는지 알려줘", "안경", "지난주"),
        ("어젯밤에 충전기 어디다 놨지", "충전기", "어젯밤"),
        ("키 어디 뒀더라", "키", ""),
        ("오늘 아침 택배 박스 어디에 있어?", "택배 박스", "오늘 아침"),
        ("리모컨 좀 찾아줘", "리모컨", ""),
        ("마스크는 어디에 있을까", "마스크", ""),
        ("그저께 가방 어디에 놨었지", "가방", "그저께"),
        ("아까 폰 어디 뒀어", "폰", "아까"),
        ("우산 어딨어?", "우산", ""),
        ("에어팟 마지막으로 본 거 어디야", "에어팟", ""),
        ("택배 어제 어디다 놓았지", "택배", "어제"),
        ("혹시 내 노트북 어디 있는지 기억나?", "노트북", ""),
        ("", "", ""),
        ("어디 뒀지?", "", ""),
    ]

    def test_many_phrasings(self):
        for q, target, label in self.CASES:
            p = askmod.parse_query(q, NOW)
            self.assertEqual((p.target, p.window_label), (target, label), q)

    def test_kind_when(self):
        self.assertEqual(askmod.parse_query("핸드폰 언제 봤지", NOW).kind, "when")
        self.assertEqual(askmod.parse_query("핸드폰 어디 뒀지", NOW).kind, "where")

    def test_windows(self):
        p = askmod.parse_query("어제 택배 어디", NOW)
        self.assertEqual(p.window, (at(2026, 11, 1), at(2026, 11, 2)))
        p = askmod.parse_query("어제 저녁 택배 어디", NOW)
        self.assertEqual(p.window, (at(2026, 11, 1, 17), at(2026, 11, 1, 22)))
        p = askmod.parse_query("3일 전 우산", NOW)
        self.assertEqual(p.window, (at(2026, 10, 30), at(2026, 10, 31)))
        p = askmod.parse_query("오늘 아침 지갑", NOW)
        self.assertEqual(p.window, (at(2026, 11, 2, 5), at(2026, 11, 2, 11)))


class FormatTest(unittest.TestCase):
    def test_time(self):
        self.assertEqual(askmod.fmt_time(at(2026, 11, 2, 21, 40), NOW), "오늘 21:40")
        self.assertEqual(askmod.fmt_time(at(2026, 11, 1, 9, 5), NOW), "어제 09:05")
        self.assertEqual(askmod.fmt_time(at(2026, 10, 20, 9, 5), NOW), "10월 20일 09:05")
        self.assertEqual(askmod.fmt_age(3 * 86400 + 5), "3일 전")
        self.assertEqual(askmod.fmt_age(7200), "2시간 전")


class RankingTest(unittest.TestCase, helpers.TmpCase):
    def app(self, **kw):
        self.clock = helpers.Clock()
        vlm = helpers.FakeVlm()
        a = helpers.make_app(self.make_tmp(), vlm=vlm, clock=self.clock, **kw)
        return a, vlm

    def put(self, a, vlm, ts, camera, objs, scene=""):
        vlm.results.append({"objects": objs, "scene": scene})
        r = a.ingest_bytes(jpeg(), camera, None, ts)
        self.assertEqual(r["status"], "stored")
        return r

    def test_most_recent_wins_and_text(self):
        for fts in (True, False):
            a, vlm = self.app(use_fts=fts)
            self.put(a, vlm, at(2026, 11, 2, 18, 0), "거실", [obj("리모컨", "TV 앞 바닥", 0.9)])
            self.put(a, vlm, at(2026, 11, 2, 21, 40), "거실", [obj("리모컨", "소파 왼쪽 쿠션 위", 0.9)])
            r = askmod.answer(a, "리모컨 어디 뒀지?")
            self.assertTrue(r["found"])
            top = r["sightings"][0]
            self.assertEqual(top["where"], "소파 왼쪽 쿠션 위")
            self.assertEqual(top["when"], "오늘 21:40")
            self.assertIn("오늘 21:40 거실 카메라에서 소파 왼쪽 쿠션 위에 있었어요", r["answer"])
            self.assertIn(".jpg", r["answer"])
            self.assertEqual(len(r["sightings"]), 2)

    def test_synonym_search_both_directions(self):
        for fts in (True, False):
            a, vlm = self.app(use_fts=fts)
            self.put(a, vlm, at(2026, 11, 2, 20, 0), "책상", [obj("택배 상자", "현관 앞 바닥", 0.8), obj("Wallet", "책상 위", 0.8)])
            self.assertTrue(askmod.answer(a, "박스 어디 있어")["found"])
            self.assertTrue(askmod.answer(a, "택배 어디 놨지")["found"])
            self.assertTrue(askmod.answer(a, "지갑 어디 뒀지")["found"])
            self.assertFalse(askmod.answer(a, "열쇠 어디 뒀지")["found"])

    def test_confidence_beats_slightly_older(self):
        a, vlm = self.app()
        self.put(a, vlm, at(2026, 11, 2, 21, 0), "거실", [obj("안경", "식탁 위", 0.15)])
        self.put(a, vlm, at(2026, 11, 2, 20, 0), "책상", [obj("안경", "책상 모니터 옆", 0.95)])
        r = askmod.answer(a, "안경 어디 있어")
        self.assertEqual(r["sightings"][0]["where"], "책상 모니터 옆")
        self.assertEqual(r["sightings"][1]["where"], "식탁 위")

    def test_recency_beats_confidence_when_far_apart(self):
        a, vlm = self.app()
        self.put(a, vlm, at(2026, 10, 25, 20, 0), "책상", [obj("지갑", "서랍 위", 1.0)])
        self.put(a, vlm, at(2026, 11, 2, 20, 0), "거실", [obj("지갑", "소파 위", 0.6)])
        self.assertEqual(askmod.answer(a, "지갑 어디")["sightings"][0]["where"], "소파 위")

    def test_exact_name_beats_where_mention(self):
        a, vlm = self.app()
        self.put(a, vlm, at(2026, 11, 2, 21, 0), "거실", [obj("컵", "리모컨 옆 탁자", 0.9)])
        self.put(a, vlm, at(2026, 11, 2, 20, 0), "거실", [obj("리모컨", "소파 위", 0.9)])
        r = askmod.answer(a, "리모컨 어디 뒀지")
        self.assertEqual(r["sightings"][0]["name"], "리모컨")
        self.assertEqual(r["sightings"][1]["name"], "컵")

    def test_stale_message(self):
        a, vlm = self.app()
        self.put(a, vlm, at(2026, 10, 30, 20, 0), "거실", [obj("리모컨", "소파 위", 0.9)])
        r = askmod.answer(a, "리모컨 어디 뒀지")
        self.assertIn("마지막으로 본 게 3일 전", r["answer"])
        self.put(a, vlm, at(2026, 11, 2, 21, 50), "거실", [obj("우산", "현관", 0.9)])
        self.assertNotIn("마지막으로 본 게", askmod.answer(a, "우산 어디")["answer"])

    def test_time_window_filter_and_fallback(self):
        a, vlm = self.app()
        self.put(a, vlm, at(2026, 11, 1, 15, 0), "현관", [obj("택배", "현관 신발장 앞", 0.9)])
        self.put(a, vlm, at(2026, 11, 2, 21, 0), "거실", [obj("택배", "거실 테이블 위", 0.9)])
        r = askmod.answer(a, "어제 택배 상자 어디 놨더라")
        self.assertEqual(r["sightings"][0]["where"], "현관 신발장 앞")
        self.assertNotIn("못 봤고", r["answer"])
        r = askmod.answer(a, "그저께 택배 어디 있었지")
        self.assertIn("그저께에는 못 봤고", r["answer"])
        self.assertEqual(r["sightings"][0]["where"], "거실 테이블 위")

    def test_not_found_and_empty_target(self):
        a, vlm = self.app()
        r = askmod.answer(a, "리모컨 어디 뒀지?")
        self.assertFalse(r["found"])
        self.assertIn("본 기록이 없어요", r["answer"])
        self.assertIn("모르겠어요", askmod.answer(a, "어디 뒀지?")["answer"])

    def test_dedupes_same_place(self):
        a, vlm = self.app()
        for h in (18, 19, 20):
            self.put(a, vlm, at(2026, 11, 2, h, 0), "거실", [obj("리모컨", "소파 위", 0.9)])
        self.assertEqual(len(askmod.answer(a, "리모컨 어디")["sightings"]), 1)

    def test_llm_phrase_and_fallback(self):
        a, vlm = self.app()
        self.put(a, vlm, at(2026, 11, 2, 21, 0), "거실", [obj("리모컨", "소파 위", 0.9)])
        vlm.phrased = "소파 위에 있어요!"
        self.assertEqual(askmod.answer(a, "리모컨 어디", True)["answer"], "소파 위에 있어요!")
        vlm.phrased = None
        self.assertIn("소파 위", askmod.answer(a, "리모컨 어디", True)["answer"])
        self.assertIn("소파 위", askmod.answer(a, "리모컨 어디")["answer"])


if __name__ == "__main__":
    unittest.main()
