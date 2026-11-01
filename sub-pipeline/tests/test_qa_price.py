import os
import tempfile
import unittest

from subpipe import price, srt
from subpipe.qa import Limits, lint
from subpipe.srt import Cue


def codes(issues):
    return sorted(i.code for i in issues)


class QATests(unittest.TestCase):
    def test_clean(self):
        cues = [Cue(1, 0, 2000, "안녕하세요"), Cue(2, 2100, 4000, "반갑습니다")]
        self.assertEqual(lint(cues), [])

    def test_overlap_numbering_empty_duration(self):
        cues = [Cue(1, 0, 2000, "가"), Cue(3, 1500, 3500, "나"), Cue(3, 4000, 4000, "  ")]
        c = codes(lint(cues))
        self.assertIn("overlap", c)
        self.assertIn("numbering", c)
        self.assertIn("empty", c)
        self.assertIn("duration", c)

    def test_short_cps_length_lines(self):
        cues = [Cue(1, 0, 300, "가"),
                Cue(2, 1000, 2000, "가" * 30),  # 30 cps, 30 chars/line
                Cue(3, 3000, 11000, "a\nb\nc"),
                Cue(4, 10000, 12000, "가\n\n나")]
        c = codes(lint(cues, Limits(cps=14, max_chars=22)))
        for want in ("too_short", "cps", "line_length", "lines", "too_long_dur", "empty_line"):
            self.assertIn(want, c)

    def test_min_gap(self):
        cues = [Cue(1, 0, 2000, "가"), Cue(2, 2010, 4000, "나")]
        self.assertIn("gap", codes(lint(cues, Limits(min_gap=0.08))))
        self.assertEqual(lint(cues), [])

    def test_cps_counts_without_newline(self):
        cues = [Cue(1, 0, 1000, "가" * 7 + "\n" + "나" * 7)]
        self.assertEqual(lint(cues, Limits(cps=14)), [])


class PriceTests(unittest.TestCase):
    def test_quote_rounding(self):
        self.assertEqual(price.quote(600, 5000)["amount"], 50000)
        self.assertEqual(price.quote(601, 5000)["amount"], 55000)  # ceil to 11 min
        self.assertEqual(price.quote(630, 5000, "half")["amount"], 52500)
        self.assertEqual(price.quote(630, 5000, "none")["amount"], 52500)
        self.assertEqual(price.quote(90, 7000, "none")["amount"], 10500)

    def test_default_and_custom_tiers(self):
        t = price.parse_tiers(None)
        self.assertEqual([v[1] for v in t.values()], [5000, 7000, 10000])
        t = price.parse_tiers("basic=4500")
        self.assertEqual(t["basic"][1], 4500)
        with self.assertRaises(price.PriceError):
            price.parse_tiers("nope=1")
        self.assertEqual(price.won(1234567), "1,234,567원")

    def test_duration_from_srt(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.srt")
            srt.save_srt(p, [Cue(1, 0, 1000, "a"), Cue(2, 2000, 125000, "b")])
            self.assertAlmostEqual(price.media_duration_seconds(p), 125.0)

    def test_duration_from_wav(self):
        import wave
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.wav")
            w = wave.open(p, "wb")
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"\0\0" * 8000 * 3)
            w.close()
            self.assertAlmostEqual(price.media_duration_seconds(p), 3.0)


if __name__ == "__main__":
    unittest.main()
