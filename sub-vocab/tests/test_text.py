import html
import unittest

from subvocab.text import WORD_RE, group_key, highlight, lemmatize, token_base


class LemmatizeTests(unittest.TestCase):
    def test_plural_suffixes(self):
        cases = {
            "cats": "cat",
            "boxes": "box",
            "classes": "class",
            "watches": "watch",
            "houses": "house",
            "dishes": "dish",
            "stories": "story",
            "dances": "dance",
        }
        for surface, lemma in cases.items():
            with self.subTest(surface=surface):
                self.assertEqual(lemmatize(surface), lemma)

    def test_plural_exceptions_kept(self):
        for word in ["class", "bus", "news", "this", "analysis", "always"]:
            with self.subTest(word=word):
                self.assertEqual(lemmatize(word), word)

    def test_ed_strips_suffix_and_undoes_doubling(self):
        cases = {
            "walked": "walk",
            "stopped": "stop",
            "tried": "try",
            "wanted": "want",
            "visited": "visit",
            "agreed": "agreed",
            "opened": "open",
        }
        for surface, stem in cases.items():
            with self.subTest(surface=surface):
                self.assertEqual(lemmatize(surface), stem)

    def test_ing_strips_suffix_and_undoes_doubling(self):
        cases = {
            "running": "run",
            "stopping": "stop",
            "playing": "play",
            "looking": "look",
            "thing": "thing",
            "bring": "bring",
        }
        for surface, stem in cases.items():
            with self.subTest(surface=surface):
                self.assertEqual(lemmatize(surface), stem)

    def test_lemmatizer_does_not_guess_silent_e(self):
        # The silent "e" is decided later from the forms that really appear.
        self.assertEqual(lemmatize("decided"), "decid")
        self.assertEqual(lemmatize("making"), "mak")

    def test_irregular_table(self):
        cases = {
            "went": "go",
            "was": "be",
            "were": "be",
            "bought": "buy",
            "ate": "eat",
            "children": "child",
            "people": "person",
            "made": "make",
            "did": "do",
            "wolves": "wolf",
        }
        for surface, lemma in cases.items():
            with self.subTest(surface=surface):
                self.assertEqual(lemmatize(surface), lemma)

    def test_lowercases_input(self):
        self.assertEqual(lemmatize("Went"), "go")

    def test_short_words_untouched(self):
        self.assertEqual(lemmatize("yes"), "yes")
        self.assertEqual(lemmatize("cat"), "cat")


class GroupKeyTests(unittest.TestCase):
    def test_forms_of_same_word_share_a_key(self):
        groups = [
            ["decide", "decided", "deciding", "decides"],
            ["make", "making", "made", "makes"],
            ["dance", "dancing", "danced", "dances"],
            ["close", "closed", "closes"],
            ["open", "opened", "opening"],
            ["stop", "stopped", "stopping"],
        ]
        for group in groups:
            keys = {group_key(w) for w in group}
            with self.subTest(group=group):
                self.assertEqual(len(keys), 1, keys)

    def test_different_words_keep_different_keys(self):
        self.assertNotEqual(group_key("run"), group_key("walk"))
        self.assertNotEqual(group_key("go"), group_key("stop"))

    def test_irregular_forms_group_with_base(self):
        self.assertEqual(group_key("went"), group_key("go"))
        self.assertEqual(group_key("bought"), group_key("buy"))


class TokenTests(unittest.TestCase):
    def test_possessive_is_base_word(self):
        self.assertEqual(token_base("John's"), "John")

    def test_contractions_dropped(self):
        self.assertIsNone(token_base("don't"))
        self.assertIsNone(token_base("I’m"))

    def test_numbers_are_not_words(self):
        self.assertEqual(WORD_RE.findall("3rd and 2020 'quoted' word"), ["and", "quoted", "word"])

    def test_curly_apostrophe_words_are_found(self):
        self.assertEqual(WORD_RE.findall("don’t stop"), ["don’t", "stop"])


class HighlightTests(unittest.TestCase):
    def test_wraps_inflected_forms_of_lemma(self):
        out = highlight(
            "We went home and ate apples.",
            lambda base: lemmatize(base) == "go",
            "<b>", "</b>",
        )
        self.assertEqual(out, "We <b>went</b> home and ate apples.")

    def test_escape_applies_outside_and_inside_marks(self):
        out = highlight(
            "a < b & cats",
            lambda base: lemmatize(base) == "cat",
            "<b>", "</b>",
            escape=lambda s: html.escape(s, quote=False),
        )
        self.assertEqual(out, "a &lt; b &amp; <b>cats</b>")

    def test_no_match_returns_text(self):
        self.assertEqual(highlight("nothing here", lambda b: False, "<b>", "</b>"), "nothing here")


class StopwordSanityTests(unittest.TestCase):
    def test_common_function_words_present(self):
        from subvocab.text import STOPWORDS

        for word in ["the", "and", "is", "of", "i", "you", "don", "um"]:
            self.assertIn(word, STOPWORDS)


if __name__ == "__main__":
    unittest.main()
