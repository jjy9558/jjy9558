import unittest

from demomaker.ansi import Screen, Style, apply_sgr, char_width, line_runs, strip_ansi, text_width, wrap_text


def rows(scr):
    return ["".join(c.ch for c in line) for line in scr.finish()]


class WidthTests(unittest.TestCase):
    def test_wide_and_narrow(self):
        self.assertEqual(char_width("a"), 1)
        self.assertEqual(char_width("한"), 2)
        self.assertEqual(char_width("ｗ"), 2)       # 전각 라틴 (F)
        self.assertEqual(char_width("🎮"), 2)
        self.assertEqual(char_width("́"), 0)   # 결합 문자
        self.assertEqual(char_width("‍"), 0)
        self.assertEqual(text_width("가나다abc"), 9)

    def test_wrap_text_never_splits_wide_char(self):
        lines = wrap_text("한글한글한", 5)
        self.assertEqual(lines, ["한글", "한글", "한"])
        for l in lines:
            self.assertLessEqual(text_width(l), 5)

    def test_wrap_text_mixed(self):
        lines = wrap_text("ab한cd", 3)
        self.assertEqual(lines, ["ab", "한c", "d"])


class SgrTests(unittest.TestCase):
    def test_basic_colors_and_bold(self):
        st = apply_sgr(Style(), [1, 31])
        self.assertTrue(st.bold)
        self.assertEqual(st.fg, 1)
        st = apply_sgr(st, [0])
        self.assertEqual(st, Style())

    def test_bright_and_background(self):
        st = apply_sgr(Style(), [92, 44])
        self.assertEqual((st.fg, st.bg), (10, 4))
        st = apply_sgr(st, [39, 49])
        self.assertEqual((st.fg, st.bg), (None, None))

    def test_256_and_truecolor(self):
        self.assertEqual(apply_sgr(Style(), [38, 5, 196]).fg, "#ff0000")
        self.assertEqual(apply_sgr(Style(), [38, 5, 3]).fg, 3)
        self.assertEqual(apply_sgr(Style(), [48, 2, 1, 2, 3]).bg, "#010203")

    def test_empty_params_is_reset(self):
        self.assertEqual(apply_sgr(Style(fg=1, bold=True), []), Style())


class ScreenTests(unittest.TestCase):
    def test_colors_in_runs(self):
        s = Screen(40)
        s.feed("\x1b[1;32m성공\x1b[0m done\n")
        runs = line_runs(s.finish()[0])
        self.assertEqual(runs[0][1], "성공")
        self.assertTrue(runs[0][2].bold)
        self.assertEqual(runs[0][2].fg, 2)
        self.assertEqual(runs[1][0], 4)             # 전각 2글자 = 4칸 뒤에서 시작
        self.assertEqual(runs[1][1], " done")

    def test_wrapping_counts_columns(self):
        s = Screen(10)
        s.feed("한글한글한글한\n")
        self.assertEqual(rows(s), ["한글한글한", "글한"])
        self.assertEqual(s.wrapped[:2], [False, True])

    def test_wide_char_does_not_overflow_last_column(self):
        s = Screen(5)
        s.feed("abcd한")
        self.assertEqual(rows(s), ["abcd", "한"])

    def test_carriage_return_overwrites(self):
        s = Screen(20)
        s.feed("진행 10%\r진행 90%\n")
        self.assertEqual(rows(s), ["진행 90%"])

    def test_crlf_and_trailing_blank_lines_trimmed(self):
        s = Screen(20)
        s.feed("a\r\nb\r\n\r\n")
        self.assertEqual(rows(s), ["a", "b"])

    def test_tab_stops(self):
        s = Screen(30)
        s.feed("a\tb\n")
        self.assertEqual(rows(s), ["a       b"])

    def test_non_sgr_escapes_are_dropped(self):
        s = Screen(30)
        s.feed("\x1b[2J\x1b[?25lhi\x1b]0;title\x07 there\n")
        self.assertEqual(rows(s), ["hi there"])

    def test_erase_to_end_of_line(self):
        s = Screen(30)
        s.feed("abcdef\r\x1b[Kxy\n")
        self.assertEqual(rows(s), ["xy"])

    def test_overwrite_half_of_wide_char(self):
        s = Screen(10)
        s.feed("한글\rX\n")
        self.assertEqual(text_width(rows(s)[0]), 4)

    def test_strip_ansi(self):
        self.assertEqual(strip_ansi("\x1b[31m빨강\x1b[0m"), "빨강")

    def test_control_chars_removed(self):
        s = Screen(20)
        s.feed("a\x07\x00b\n")
        self.assertEqual(rows(s), ["ab"])


if __name__ == "__main__":
    unittest.main()
