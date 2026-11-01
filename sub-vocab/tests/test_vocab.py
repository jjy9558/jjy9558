import unittest

from subvocab.output import highlight_entry
from subvocab.subtitles import Caption
from subvocab.text import group_key
from subvocab.vocab import analyze, headword, is_sentence_initial


def caps(*pairs, source="a.srt"):
    return [Caption(start, text, source) for start, text in pairs]


def entry_for(result, word):
    key = group_key(word)
    return next(e for e in result.words if e.key == key)


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.captions = caps(
            (1.0, "I went to the store with Sarah."),
            (3.0, "Sarah bought three apples."),
            (5.0, "We went home and ate apples."),
        )

    def test_frequency_and_order(self):
        result = analyze(self.captions, known={"home"})
        self.assertEqual([e.key for e in result.words], ["go", "appl", "stor", "buy", "eat"])
        self.assertEqual([e.count for e in result.words], [2, 2, 1, 1, 1])
        go = result.words[0]
        self.assertEqual(go.forms["went"], 2)

    def test_headword_is_a_form_seen_in_subtitles(self):
        result = analyze(self.captions, known={"home"})
        self.assertEqual([e.word for e in result.words], ["went", "apples", "store", "bought", "ate"])
        for entry in result.words:
            self.assertIn(entry.word, entry.forms)

    def test_first_timestamp_and_example_sentence(self):
        result = analyze(self.captions, known={"home"})
        store = entry_for(result, "store")
        self.assertEqual(store.first_start, 1.0)
        self.assertEqual(store.example, "I went to the store with Sarah.")
        self.assertEqual(store.first_source, "a.srt")

    def test_known_words_are_excluded(self):
        result = analyze(self.captions, known={"home", "apples"})
        keys = [e.key for e in result.words]
        self.assertNotIn(group_key("apples"), keys)
        self.assertNotIn(group_key("home"), keys)
        self.assertIn("go", keys)

    def test_known_check_uses_surface_form_too(self):
        result = analyze(self.captions, known={"went"})
        self.assertNotIn("go", [e.key for e in result.words])

    def test_stopwords_and_single_letters_skipped(self):
        result = analyze(caps((1.0, "I am the one who is it")))
        self.assertEqual(result.words, [])

    def test_names_are_separate(self):
        result = analyze(self.captions, known={"home"})
        self.assertNotIn("sarah", [e.key for e in result.words])
        self.assertEqual(len(result.names), 1)
        name = result.names[0]
        self.assertEqual(name.word, "Sarah")
        self.assertEqual(name.count, 2)
        self.assertEqual(name.first_start, 1.0)
        self.assertTrue(name.is_name)

    def test_word_seen_in_lowercase_is_not_a_name(self):
        result = analyze(caps(
            (1.0, "Apple is nice."),
            (2.0, "I like the apple pie."),
            (3.0, "I bought an Apple store."),
        ))
        self.assertEqual(result.names, [])
        apple = entry_for(result, "apple")
        self.assertEqual(apple.count, 3)

    def test_numbers_are_not_words(self):
        result = analyze(caps((1.0, "There were 3rd 2020 twenty-five things.")))
        self.assertEqual([e.word for e in result.words], ["things"])

    def test_caption_count(self):
        self.assertEqual(analyze(self.captions).caption_count, 3)


class HeadwordGroupingTests(unittest.TestCase):
    def test_decided_and_decide_both_present_gives_decide(self):
        result = analyze(caps(
            (1.0, "We decided to leave."),
            (2.0, "You can decide later."),
        ))
        entry = entry_for(result, "decide")
        self.assertEqual(entry.word, "decide")
        self.assertEqual(entry.count, 2)
        self.assertEqual(sum(1 for e in result.words if e.key == entry.key), 1)

    def test_only_decided_gives_decided(self):
        result = analyze(caps((1.0, "We decided to leave.")))
        self.assertEqual(entry_for(result, "decided").word, "decided")

    def test_stopped_without_stop_gives_stopped(self):
        result = analyze(caps((1.0, "He stopped."), (2.0, "They stopped again.")))
        entry = entry_for(result, "stopped")
        self.assertEqual(entry.word, "stopped")
        self.assertEqual(entry.count, 2)

    def test_stopped_with_stop_seen_gives_stop(self):
        result = analyze(caps((1.0, "He stopped."), (2.0, "Please stop."), (3.0, "Stop now.")))
        entry = entry_for(result, "stop")
        self.assertEqual(entry.word, "stop")
        self.assertEqual(entry.count, 3)

    def test_running_and_run_gives_run(self):
        result = analyze(caps((1.0, "Kids were running fast."), (2.0, "Then we run home.")))
        entry = entry_for(result, "run")
        self.assertEqual(entry.word, "run")
        self.assertEqual(entry.count, 2)

    def test_only_running_gives_running(self):
        result = analyze(caps((1.0, "Kids were running fast.")))
        self.assertEqual(entry_for(result, "running").word, "running")

    def test_headword_never_invents_unseen_form(self):
        result = analyze(caps((1.0, "We decided to leave.")))
        self.assertNotIn("decide", [e.word for e in result.words])

    def test_example_bolds_the_surface_form_used(self):
        result = analyze(caps(
            (1.0, "We decided to leave."),
            (2.0, "You can decide later."),
        ))
        entry = entry_for(result, "decide")
        self.assertEqual(
            highlight_entry(entry, "We decided to leave.", "**", "**"),
            "We **decided** to leave.",
        )
        self.assertEqual(
            highlight_entry(entry, "You can decide later.", "**", "**"),
            "You can **decide** later.",
        )

    def test_headword_function_priority(self):
        from collections import Counter

        self.assertEqual(headword("stop", Counter({"stop": 1, "stopped": 5})), "stop")
        self.assertEqual(headword("decid", Counter({"decide": 1, "decided": 3})), "decide")
        self.assertEqual(headword("decid", Counter({"decided": 3, "decides": 1})), "decided")


class SentenceInitialTests(unittest.TestCase):
    def test_line_start_is_initial(self):
        self.assertTrue(is_sentence_initial("John came", 0))

    def test_after_sentence_end_is_initial(self):
        self.assertTrue(is_sentence_initial("Yes. John came", 5))

    def test_mid_sentence_is_not_initial(self):
        self.assertFalse(is_sentence_initial("a John", 2))


if __name__ == "__main__":
    unittest.main()
