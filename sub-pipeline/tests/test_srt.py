import unittest

from subpipe import srt
from tests.helpers import SAMPLE_SRT


class SrtTests(unittest.TestCase):
    def test_timecode(self):
        self.assertEqual(srt.parse_timecode("01:02:03,456"), 3723456)
        self.assertEqual(srt.parse_timecode("02:03.5"), 123500)  # VTT short form, ".5" = 500ms
        self.assertEqual(srt.format_timecode(3723456), "01:02:03,456")
        self.assertEqual(srt.format_timecode(3723456, "."), "01:02:03.456")
        with self.assertRaises(ValueError):
            srt.parse_timecode("abc")

    def test_roundtrip(self):
        cues = srt.parse_cues(SAMPLE_SRT)
        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[1].text, "Today we test the NVIDIA RTX 5080\nwith 3,000 samples.")
        self.assertEqual(srt.parse_cues(srt.write_srt(cues)), cues)
        self.assertEqual(srt.write_srt(cues), SAMPLE_SRT)

    def test_lenient_bom_crlf_no_index(self):
        raw = "﻿00:00:01,000 --> 00:00:02,000\r\nhi\r\n\r\n7\r\n00:00:03,000 --> 00:00:04,000\r\nyo\r\n"
        cues = srt.parse_cues(raw)
        self.assertEqual([(c.index, c.text) for c in cues], [(1, "hi"), (7, "yo")])

    def test_vtt(self):
        vtt = "WEBVTT\n\nNOTE x\n\nintro\n00:00:01.000 --> 00:00:02.500 align:start\nHi\n"
        cues = srt.parse_cues(vtt)
        self.assertEqual((cues[0].start, cues[0].end, cues[0].text), (1000, 2500, "Hi"))
        out = srt.write_vtt(cues)
        self.assertTrue(out.startswith("WEBVTT"))
        self.assertIn("00:00:01.000 --> 00:00:02.500", out)


if __name__ == "__main__":
    unittest.main()
