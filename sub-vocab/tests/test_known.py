import os
import shutil
import tempfile
import unittest

from subvocab.cli import DEFAULT_KNOWN
from subvocab.known import add_known_words, load_known, normalize_word


class KnownFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "known.txt")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("# header comment\n\ntime\n  Make  \n")

    def test_load_skips_comments_and_blanks_and_lowercases(self):
        self.assertEqual(load_known(self.path), {"time", "make"})

    def test_missing_file_is_empty(self):
        self.assertEqual(load_known(os.path.join(self.tmp, "nope.txt")), set())

    def test_add_appends_new_words_only(self):
        added = add_known_words(self.path, ["Time", "go", "go", "won't!", "bad word", "Hello,"])
        self.assertEqual(added, ["go", "won't", "hello"])
        self.assertEqual(load_known(self.path), {"time", "make", "go", "won't", "hello"})

    def test_add_keeps_file_valid_when_missing_final_newline(self):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("end")
        add_known_words(self.path, ["stop"])
        with open(self.path, encoding="utf-8") as fh:
            self.assertIn("end\nstop", fh.read())

    def test_add_creates_missing_file(self):
        path = os.path.join(self.tmp, "new.txt")
        self.assertEqual(add_known_words(path, ["Hi"]), ["hi"])
        self.assertEqual(load_known(path), {"hi"})

    def test_normalize_word(self):
        self.assertEqual(normalize_word(" (Hello!) "), "hello")
        self.assertEqual(normalize_word("Don’t"), "don't")


class ShippedStarterListTests(unittest.TestCase):
    def test_starter_has_about_300_unique_words(self):
        with open(DEFAULT_KNOWN, encoding="utf-8") as fh:
            entries = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        self.assertGreaterEqual(len(entries), 280)
        self.assertEqual(len(entries), len(set(entries)), "starter list has duplicates")
        self.assertEqual(len(load_known(DEFAULT_KNOWN)), len(entries))


if __name__ == "__main__":
    unittest.main()
