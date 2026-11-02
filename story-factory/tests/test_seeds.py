import tempfile
import unittest
from pathlib import Path

from storyfactory.seeds import Sources, collect, won_ko, ko_hour

from tests.helpers import NOW, REAL_NAME, make_all, make_kakao, make_voice_journal, tree_state


class SeedTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def test_cards_from_five_sources_and_read_only(self):
        src = make_all(self.dir)
        before = tree_state(self.dir)
        cards, warnings = collect(src, NOW)
        self.assertEqual(tree_state(self.dir), before)           # 원본 바이트/mtime/파일 목록 불변
        self.assertEqual(warnings, [])
        self.assertLessEqual(len(cards), 10)
        self.assertGreaterEqual(len(cards), 8)
        sources = {c.source for c in cards}
        self.assertEqual(sources, {"nagger", "pet", "cooloff", "kakao", "journal"})
        kinds = {c.kind for c in cards}
        self.assertIn("dish_streak", kinds)
        self.assertIn("graveyard_tomb", kinds)
        self.assertIn("declared_quit", kinds)            # voice-journal 에서 가져온 묘지 항목
        self.assertIn("regret_item", kinds)
        dish = next(c for c in cards if c.kind == "dish_streak")
        self.assertEqual(dish.facts["days"], 9)
        self.assertEqual([c.priority for c in cards], sorted((c.priority for c in cards), reverse=True))

    def test_kakao_only_anonymous_numbers(self):
        src = Sources(kakao=make_kakao(self.dir))
        cards, _ = collect(src, NOW)
        blob = " ".join(str(c.to_dict()) for c in cards)
        for secret in ("XYZZY", "멤버1", "멤버2", REAL_NAME):
            self.assertNotIn(secret, blob)
        self.assertEqual({c.kind for c in cards}, {"streak", "peak_hour", "busiest_day", "volume"})

    def test_kakao_not_anonymized_is_refused(self):
        src = Sources(kakao=make_kakao(self.dir, anonymized=False))
        cards, warnings = collect(src, NOW)
        self.assertEqual(cards, [])
        self.assertTrue(any("익명화" in w for w in warnings))

    def test_journal_only_user_specified_entries(self):
        db = make_voice_journal(self.dir)
        cards, _ = collect(Sources(voice_journal=db), NOW)      # 지정 없음 -> 아무것도 읽지 않음
        self.assertEqual(cards, [])
        cards, _ = collect(Sources(voice_journal=db, journal_entries=[1]), NOW)
        self.assertEqual([c.id for c in cards], ["journal-entry-1"])
        self.assertEqual(cards[0].consent, "user-specified")
        self.assertNotIn("PRIVATEZZ", cards[0].subject)
        cards, warnings = collect(Sources(voice_journal=db, journal_entries=[99]), NOW)
        self.assertEqual(cards, [])
        self.assertTrue(any("#99" in w for w in warnings))

    def test_missing_and_broken_sources_are_skipped(self):
        bad = self.dir / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        src = Sources(life_nagger=self.dir / "nope.jsonl", decision_pet=bad, cool_off=self.dir / "none.json",
                      kakao=bad, voice_journal=self.dir / "x.db", journal_entries=[1])
        cards, warnings = collect(src, NOW)
        self.assertEqual(cards, [])
        self.assertGreaterEqual(len(warnings), 4)

    def test_helpers(self):
        self.assertEqual(won_ko(189000), "18만 9천원")
        self.assertEqual(won_ko(250000), "25만원")
        self.assertEqual(won_ko(1500), "1천 500원")
        self.assertEqual(ko_hour(0), "새벽 12시")
        self.assertEqual(ko_hour(15), "오후 3시")
        self.assertEqual(ko_hour(22), "밤 10시")


if __name__ == "__main__":
    unittest.main()
