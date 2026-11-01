import os
import tempfile
import unittest

from subvocab.subtitles import (
    Caption,
    SubtitleError,
    clean_line,
    decode_bytes,
    load_captions,
    parse_subtitle_text,
    parse_timestamp,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    return os.path.join(FIXTURES, name)


class TimestampTests(unittest.TestCase):
    def test_srt_comma_and_vtt_dot(self):
        self.assertAlmostEqual(parse_timestamp("00:01:02,500"), 62.5)
        self.assertAlmostEqual(parse_timestamp("00:01:02.250"), 62.25)

    def test_hours_optional_in_vtt(self):
        self.assertAlmostEqual(parse_timestamp("01:02.250"), 62.25)
        self.assertAlmostEqual(parse_timestamp("1:00:00.000"), 3600.0)

    def test_invalid_timestamp_raises(self):
        with self.assertRaises(SubtitleError):
            parse_timestamp("soon")


class SrtParsingTests(unittest.TestCase):
    def test_srt_fixture_lines_and_times(self):
        caps = load_captions(fixture("sample.srt"))
        self.assertEqual(
            [(c.start, c.text) for c in caps],
            [
                (1.0, "I went to the store yesterday with Sarah."),
                (3.6, "Sarah bought three apples"),
                (3.6, "and two oranges."),
                (6.5, "They were running fast, didn't they?"),
                (65.0, "Let's go home now."),
            ],
        )
        self.assertTrue(all(c.source == "sample.srt" for c in caps))

    def test_index_lines_are_not_captions(self):
        caps = parse_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\n2\n")
        # "2" sits inside the cue text here; a real index comes after a blank line.
        self.assertEqual([c.text for c in caps], ["2"])
        caps = parse_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n")
        self.assertEqual([c.text for c in caps], ["Hi", "Bye"])


class VttParsingTests(unittest.TestCase):
    def test_vtt_fixture_skips_header_notes_styles(self):
        caps = load_captions(fixture("sample.vtt"))
        self.assertEqual(
            [(c.start, c.text) for c in caps],
            [
                (1.0, "Welcome to the channel"),
                (3.5, "Today we talk about cats & dogs"),
            ],
        )

    def test_cue_settings_and_identifiers_ignored(self):
        text = "WEBVTT\n\nintro\n00:00:01.000 --> 00:00:02.000 align:start position:10%\nHello there\n"
        caps = parse_subtitle_text(text)
        self.assertEqual([(c.start, c.text) for c in caps], [(1.0, "Hello there")])

    def test_rejects_text_without_timings(self):
        with self.assertRaises(SubtitleError):
            parse_subtitle_text("just some text\nno timing here\n", source="x.txt")


class YouTubeRollingTests(unittest.TestCase):
    def test_rolling_duplicates_and_tags_removed(self):
        caps = load_captions(fixture("youtube_auto.vtt"))
        self.assertEqual(
            [(round(c.start, 3), c.text) for c in caps],
            [
                (0.0, "hello everyone welcome back"),
                (2.51, "today we are going to learn"),
                (4.81, "about the Python language"),
            ],
        )

    def test_youtube_single_space_separator_lines(self):
        text = (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:02.500 align:start position:0%\n"
            " \n"
            "hello<00:00:00.480><c> there</c>\n\n"
            "00:00:02.500 --> 00:00:02.510 align:start position:0%\n"
            "hello there\n \n\n"
            "00:00:02.510 --> 00:00:04.000 align:start position:0%\n"
            "hello there\n"
            "friend<00:00:03.000><c> now</c>\n \n"
        )
        caps = parse_subtitle_text(text)
        self.assertEqual([(c.start, c.text) for c in caps], [(0.0, "hello there"), (2.51, "friend now")])

    def test_growing_line_replaces_previous_and_keeps_first_time(self):
        text = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\nI want\n\n"
            "00:00:02.000 --> 00:00:03.000\nI want to go home\n\n"
        )
        caps = parse_subtitle_text(text)
        self.assertEqual([(c.start, c.text) for c in caps], [(1.0, "I want to go home")])

    def test_different_lines_are_not_merged(self):
        text = (
            "1\n00:00:01,000 --> 00:00:02,000\nYes\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\nYes, I know\n"
        )
        caps = parse_subtitle_text(text)
        self.assertEqual([c.text for c in caps], ["Yes", "Yes, I know"])


class CleaningTests(unittest.TestCase):
    def test_clean_line_strips_tags_entities_and_sounds(self):
        self.assertEqual(clean_line("<c>Hi</c> &amp; [Applause] ♪ there"), "Hi & there")

    def test_clean_line_strips_dialogue_dash(self):
        self.assertEqual(clean_line("- Where are you?"), "Where are you?")

    def test_sound_only_line_is_dropped(self):
        text = "1\n00:00:01,000 --> 00:00:02,000\n[Music]\n\n2\n00:00:03,000 --> 00:00:04,000\nOk\n"
        caps = parse_subtitle_text(text)
        self.assertEqual([c.text for c in caps], ["Ok"])

    def test_decode_utf8_bom(self):
        self.assertEqual(decode_bytes("﻿hello".encode("utf-8")), "hello")

    def test_decode_cp949(self):
        self.assertEqual(decode_bytes("자막".encode("cp949")), "자막")

    def test_missing_file_raises(self):
        with self.assertRaises(SubtitleError):
            load_captions(os.path.join(tempfile.gettempdir(), "no-such-file-subvocab.srt"))


class CaptionTests(unittest.TestCase):
    def test_caption_is_immutable_value(self):
        cap = Caption(1.0, "hi", "a.srt")
        self.assertEqual(cap, Caption(1.0, "hi", "a.srt"))


if __name__ == "__main__":
    unittest.main()
