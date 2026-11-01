import unittest

import fixtures as fx
from briefing.models import Item
from briefing.score import Scorer


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.scorer = Scorer(fx.INTERESTS)

    def test_title_and_summary_weights(self):
        a = Item("llama.cpp release", "u1", "s")
        b = Item("Some release", "u2", "s", summary="built on llama.cpp")
        self.assertTrue(self.scorer.score(a))
        self.assertTrue(self.scorer.score(b))
        self.assertEqual(a.score, 4.0)
        self.assertEqual(b.score, 2.0)
        self.assertEqual(a.topic, "로컬 LLM")

    def test_korean_substring_and_english_boundary(self):
        k = Item("새 그래픽카드를 샀다", "u", "s")
        self.assertTrue(self.scorer.score(k))
        self.assertEqual(k.topic, "GPU")
        said = Item("He said it was fine", "u", "s")  # "ai" must not match inside "said"
        self.assertFalse(self.scorer.score(said))

    def test_mute_words(self):
        muted = Item("LLM 기반 코인 추천", "u", "s")
        self.assertFalse(self.scorer.score(muted))
        muted2 = Item("LLM news", "u", "s", summary="코인 이야기")
        self.assertFalse(self.scorer.score(muted2))

    def test_comment_boost_only_for_relevant(self):
        quiet = Item("GPU news", "u", "s")
        busy = Item("GPU news", "u", "s", comments=999)
        self.scorer.score(quiet)
        self.scorer.score(busy)
        self.assertAlmostEqual(busy.score - quiet.score, 0.5 * 3, places=3)
        self.assertFalse(self.scorer.score(Item("요리 레시피", "u", "s", comments=5000)))

    def test_topic_is_strongest(self):
        it = Item("RTX GPU로 llama.cpp 돌리기", "u", "s")
        self.scorer.score(it)
        self.assertEqual(it.topic, "로컬 LLM")  # 4 vs 2+2
        self.assertEqual(set(it.matched), {"llama.cpp", "gpu", "rtx"})

    def test_min_score_and_flat_keywords(self):
        s = Scorer({"keywords": {"rust": 0.5}, "min_score": 1.0})
        self.assertFalse(s.score(Item("Rust 2026", "u", "s")))
        s2 = Scorer({"keywords": {"rust": 2}})
        it = Item("Rust 2026", "u", "s")
        self.assertTrue(s2.score(it))
        self.assertEqual(it.topic, "관심사")


if __name__ == "__main__":
    unittest.main()
