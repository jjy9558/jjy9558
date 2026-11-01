import unittest

from subpipe.segment import SegConfig, build_cues, wrap_lines


def words(text, start, step=0.35):
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append({"word": (" " if i else " ") + w, "start": t, "end": t + step - 0.05})
        t += step
    return out


def seg(text, start):
    w = words(text, start)
    return {"start": w[0]["start"], "end": w[-1]["end"], "text": text, "words": w}


class SegmentTests(unittest.TestCase):
    def check_rules(self, cues, cfg):
        for i, c in enumerate(cues):
            lines = c.text.split("\n")
            self.assertLessEqual(len(lines), cfg.max_lines, c.text)
            for l in lines:
                self.assertLessEqual(len(l), cfg.max_chars, c.text)
            self.assertLessEqual(c.duration, cfg.max_dur * 1000 + 1)
            if i + 1 < len(cues):
                self.assertGreaterEqual(cues[i + 1].start - c.end, int(cfg.min_gap * 1000) - 1)
                self.assertGreater(c.end, c.start)

    def test_long_segment_is_split(self):
        text = " ".join(["word%d" % i for i in range(40)])  # 40 words ~ 14s
        cfg = SegConfig()
        cues = build_cues([seg(text, 0.0)], cfg)
        self.assertGreater(len(cues), 2)
        self.check_rules(cues, cfg)
        joined = " ".join(c.text.replace("\n", " ") for c in cues)
        self.assertEqual(joined, text)  # nothing lost or reordered

    def test_sentence_and_silence_breaks(self):
        segs = [seg("This is the first sentence here.", 0.0), seg("And now a second one follows it.", 2.8),
                seg("After a long pause we speak again", 9.0)]
        cues = build_cues(segs)
        self.assertGreaterEqual(len(cues), 3)
        self.assertTrue(cues[0].text.endswith("here."))
        self.assertGreaterEqual(cues[-1].start, 9000)

    def test_short_cues_merged_and_min_duration(self):
        segs = [seg("Hi", 0.0), seg("there friend", 0.5)]
        cues = build_cues(segs)
        self.assertEqual(len(cues), 1)
        self.assertGreaterEqual(cues[0].duration, 1000)
        self.assertEqual(cues[0].text, "Hi there friend")

    def test_min_duration_extension_respects_gap(self):
        segs = [seg("Okay", 0.0), seg("this is a very long following sentence indeed.", 0.7)]
        cfg = SegConfig(min_dur=1.0, min_gap=0.1, min_chars=3, gap_break=0.3)
        cues = build_cues(segs, cfg)
        self.assertLessEqual(cues[0].end, cues[1].start - 100)

    def test_two_lines_balanced(self):
        lines = wrap_lines("one two three four five six seven eight nine ten eleven", 42, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(l) <= 42 for l in lines))
        self.assertIsNone(wrap_lines("x" * 90, 42, 2))

    def test_no_word_timestamps_fallback(self):
        segs = [{"start": 0.0, "end": 6.0, "text": "a b c d e f g h i j k l m n o p q r s t u v w x y z a b c d e f g h i j"}]
        cues = build_cues(segs)
        self.check_rules(cues, SegConfig())
        self.assertGreaterEqual(len(cues), 1)

    def test_configurable(self):
        text = " ".join(["word%d" % i for i in range(12)])
        cfg = SegConfig(max_chars=20, max_lines=2)
        cues = build_cues([seg(text, 0.0)], cfg)
        self.check_rules(cues, cfg)
        self.assertGreater(len(cues), 1)

    def test_cjk_no_spaces(self):
        ws = [{"word": ch, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, ch in enumerate("これはテストです。" * 6)]
        cues = build_cues([{"start": 0, "end": ws[-1]["end"], "text": "", "words": ws}], SegConfig(max_chars=20))
        self.check_rules(cues, SegConfig(max_chars=20))
        self.assertEqual("".join(c.text.replace("\n", "") for c in cues), "これはテストです。" * 6)


if __name__ == "__main__":
    unittest.main()
