import unittest

from hpatch.masking import renpy_masker, rpgmaker_masker


class RPGMakerMaskTests(unittest.TestCase):
    def setUp(self):
        self.m = rpgmaker_masker()

    def test_all_control_codes_are_masked_and_restored(self):
        src = "\\C[2]Hello\\C[0] \\N[1] \\V[3] \\I[5] \\{big\\} \\. \\| \\! \\> \\< \\^ \\\\ %1 \\G \\$ \\FS[24]"
        mr = self.m.mask(src)
        for frag in ("\\C[2]", "\\N[1]", "\\V[3]", "\\I[5]", "\\{", "\\}", "\\.", "\\|", "\\!", "\\>", "\\<", "\\^", "\\\\", "%1", "\\G", "\\$", "\\FS[24]"):
            self.assertNotIn(frag, mr.masked, frag)
        self.assertEqual(len(mr.tokens), 18)
        self.assertEqual(self.m.restore(mr.masked, mr), src)

    def test_reordered_placeholders_are_accepted(self):
        mr = self.m.mask("\\C[2]Name\\C[0] says hi")
        self.assertEqual(mr.masked, "<0>Name<1> says hi")
        out = self.m.restore("<1>안녕<0> 이름", mr)
        self.assertEqual(out, "\\C[0]안녕\\C[2] 이름")

    def test_tolerates_spaces_inside_placeholder(self):
        mr = self.m.mask("\\V[1] gold")
        self.assertEqual(self.m.restore("< 0 > 골드", mr), "\\V[1] 골드")

    def test_missing_extra_duplicate_placeholder_fails(self):
        mr = self.m.mask("\\C[2]A\\C[0]")
        self.assertIsNone(self.m.restore("<0>A", mr))  # 누락
        self.assertIsNone(self.m.restore("<0>A<1><2>", mr))  # 초과
        self.assertIsNone(self.m.restore("<0>A<0><1>", mr))  # 중복
        self.assertIsNone(self.m.restore("   ", mr))  # 빈 응답

    def test_fallback_to_original(self):
        mr = self.m.mask("\\C[2]A\\C[0] b")
        text, ok = self.m.restore_or_fallback("에이 b", mr)
        self.assertFalse(ok)
        self.assertEqual(text, "\\C[2]A\\C[0] b")
        text, ok = self.m.restore_or_fallback("<0>에이<1> 비", mr)
        self.assertTrue(ok)
        self.assertEqual(text, "\\C[2]에이\\C[0] 비")

    def test_whitespace_preserved(self):
        mr = self.m.mask("  Hello \n")
        self.assertEqual(mr.lead, "  ")
        self.assertEqual(mr.trail, " \n")
        self.assertEqual(self.m.restore("안녕\n", mr), "  안녕 \n")

    def test_literal_placeholder_lookalike_in_source_is_protected(self):
        mr = self.m.mask("see <1> and \\V[2]")
        self.assertEqual(len(mr.tokens), 2)
        self.assertEqual(self.m.restore(mr.masked, mr), "see <1> and \\V[2]")

    def test_needs_translation(self):
        n = lambda s: self.m.needs_translation(self.m.mask(s))
        self.assertTrue(n("Potion"))
        self.assertTrue(n("ポーション"))
        self.assertFalse(n("100"))
        self.assertFalse(n("\\V[1]"))
        self.assertFalse(n("???"))
        self.assertFalse(n("이미 한글입니다"))

    def test_check_finished_translation(self):
        self.assertTrue(self.m.check("\\C[2]A\\C[0]", "\\C[2]에이\\C[0]"))
        self.assertFalse(self.m.check("\\C[2]A\\C[0]", "에이"))

    def test_extra_patterns(self):
        m = self.m.with_extra([r"\bNAME_\d+\b"])
        mr = m.mask("NAME_12 appears")
        self.assertEqual(mr.masked, "<0> appears")


class RenPyMaskTests(unittest.TestCase):
    def setUp(self):
        self.m = renpy_masker()

    def test_renpy_tokens(self):
        src = r'Hi [player_name]! {i}Wow{/i} %(name)s 100%% \"q\" [[literal] {{x} [var!t] {color=#f00}red{/color}'
        mr = self.m.mask(src)
        for frag in ("[player_name]", "{i}", "{/i}", "%(name)s", "%%", '\\"', "[[", "{{", "[var!t]", "{color=#f00}"):
            self.assertNotIn(frag, mr.masked, frag)
        self.assertEqual(self.m.restore(mr.masked, mr), src)

    def test_stray_brackets_or_backslash_in_translation_rejected(self):
        mr = self.m.mask("Hello [name]")
        self.assertIsNone(self.m.restore("안녕 <0> [", mr))
        self.assertIsNone(self.m.restore("안녕 <0> {b}", mr))
        self.assertIsNone(self.m.restore("안녕 <0> \\n", mr))
        self.assertEqual(self.m.restore("안녕 <0>", mr), "안녕 [name]")


if __name__ == "__main__":
    unittest.main()
