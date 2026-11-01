import re
import tempfile
import unittest
from pathlib import Path

from hpatch.backup import BackupStore
from hpatch.engines import ApplyOptions, detect_engine
from hpatch.engines.renpy import (RenPyEngine, decode_literal, encode_say, logical_lines, parse_choice, parse_rpy,
                                  parse_say, sanitize_literal, to_double_quoted)
from hpatch.tm import TranslationMemory
from hpatch.translate import Translator
from helpers import FakeLLM, RPY_SCRIPT, make_renpy, snapshot


def parse(text):
    return parse_rpy(text, "game/t.rpy", set())[0]


class ParserTests(unittest.TestCase):
    def test_extracts_only_dialogue_choices_and_character_names(self):
        us = parse(RPY_SCRIPT)
        by_kind = {}
        for u in us:
            by_kind.setdefault(u.kind, []).append(u.source)
        self.assertEqual(by_kind["name"], ["Eileen", 'Mystery \\"Man\\"'])
        self.assertEqual(by_kind["choice"], ["Go left", 'Go \\"right\\"', "Stay"])
        dialogue = by_kind["dialogue"]
        for expected in ["Narration with [player_name] and {i}tags{/i}.", "Attributes line", "String speaker line.",
                         "Narrator via variable", "Left it is.", "Duplicate line.", "Local label line."]:
            self.assertIn(expected, dialogue)
        # 파이썬/screen/init/$ 문장은 제외
        joined = "\n".join(dialogue + by_kind["choice"])
        for bad in ("not dialogue", "python string", "Screen text", "Skip button", "also not dialogue", "bg room"):
            self.assertNotIn(bad, joined)

    def test_say_forms(self):
        p = parse_say('e happy "Hi" with dissolve')
        self.assertEqual((p["who"], p["attrs"], p["raw"], p["mods"]), ("e", ["happy"], "Hi", ["with dissolve"]))
        p = parse_say('"Hi" nointeract')
        self.assertEqual((p["who"], p["mods"]), (None, ["nointeract"]))
        p = parse_say('"Eileen" "Hi"')
        self.assertEqual((p["who"], p["raw"]), ('"Eileen"', "Hi"))
        self.assertEqual(parse_say("extend \" more\"")["who"], "extend")
        for not_say in ['$ x = "a"', 'show eileen "x"', 'play music "a.ogg"', 'x = "abc"', 'foo("abc")',
                        'jump somewhere', 'if a == "b":', 'e "Open', 'return']:
            self.assertIsNone(parse_say(not_say), not_say)

    def test_choice_forms(self):
        self.assertEqual(parse_choice('"Go" if x > 1:'), "Go")
        self.assertEqual(parse_choice("'It\\'s' :"), "It\\'s")
        self.assertEqual(parse_choice("'He said \"hi\"':"), 'He said \\"hi\\"')
        self.assertIsNone(parse_choice('"Not a choice"'))
        self.assertIsNone(parse_choice("jump x:"))

    def test_escapes_and_quote_conversion(self):
        self.assertEqual(decode_literal(r'a \"b\" \\ c \n d'), 'a "b" \\ c \n d')
        self.assertEqual(encode_say('a "b" \\ c\nd  e'), 'a \\"b\\" \\\\ c\\nd \\ e')
        self.assertEqual(to_double_quoted("say \"x\" it\\'s", "'"), "say \\\"x\\\" it\\'s")
        self.assertEqual(to_double_quoted("plain", '"'), "plain")

    def test_sanitize_translation(self):
        self.assertEqual(sanitize_literal('그는 "안녕" 이라고'), '그는 \\"안녕\\" 이라고')
        self.assertEqual(sanitize_literal('이미 \\"escaped\\"'), '이미 \\"escaped\\"')
        self.assertEqual(sanitize_literal("줄\n바꿈"), "줄 바꿈")
        self.assertEqual(sanitize_literal("끝 역슬래시\\"), "끝 역슬래시")
        self.assertEqual(sanitize_literal("짝수\\\\"), "짝수\\\\")

    def test_logical_lines_comments_and_continuations(self):
        text = 'label a:\n    # comment only\n    e "has # hash"  # trailing\n    $ x = [1,\n        2]\n    e """triple\nline"""\n'
        got = [(n, ind, c) for n, ind, c in logical_lines(text)]
        self.assertEqual(got[0], (1, 0, "label a:"))
        self.assertEqual(got[1], (3, 4, 'e "has # hash"'))
        self.assertEqual(got[2][0], 4)
        self.assertEqual(got[3][0], 6)

    def test_multiline_string_dialogue_is_skipped_not_corrupted(self):
        us = parse('label a:\n    e """two\nlines"""\n    e "ok"\n')
        self.assertEqual([u.source for u in us], ["ok"])

    def test_existing_translate_blocks_are_ignored(self):
        text = 'label a:\n    e "real"\ntranslate korean a_12345678:\n    e "이미 번역"\n'
        self.assertEqual([u.source for u in parse(text)], ["real"])

    def test_identifier_format_and_uniqueness(self):
        us = parse('label chapter.one:\n    e "A"\n    e "A"\n    e "B"\n')
        ids = [u.extra["tid"] for u in us]
        self.assertEqual(len(set(ids)), 3)
        for i in ids:
            self.assertRegex(i, r"^chapter_one_[0-9a-f]{8}(_\d+)?$")
        self.assertEqual(ids[1], ids[0] + "_1")
        pre = parse('e "before any label"\n')  # label 밖은 대사로 보지 않음
        self.assertEqual(pre, [])


class TlGenerationTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name) / "game_root"
        make_renpy(self.root)

    def pipeline(self, **opts):
        eng = detect_engine(self.root)
        store = BackupStore(self.root)
        res = eng.extract(store)
        tm = TranslationMemory(":memory:")
        rep_t = Translator(FakeLLM(), tm, eng.masker(), finalize=eng.finalize, log=lambda s: None, batch_size=50).run(res.units)
        rep = eng.apply(store, tm.as_dict(), ApplyOptions(**opts))
        return eng, tm, rep_t, rep

    def test_detect(self):
        self.assertIsInstance(detect_engine(self.root), RenPyEngine)

    def test_variables_tags_and_escapes_survive(self):
        _, tm, rep_t, _ = self.pipeline()
        self.assertEqual(rep_t.failed, 0)
        self.assertEqual(tm.get('Hello, [player_name]! You owe me 100%% of \\"honor\\".'),
                         'KO:Hello, [player_name]! You owe me 100%% of \\"honor\\".')

    def test_tl_file_format(self):
        _, _, _, rep = self.pipeline()
        self.assertIn("game/tl/korean/script.rpy", rep.written)
        self.assertIn("game/tl/korean/chapter2.rpy", rep.written)
        text = (self.root / "game/tl/korean/script.rpy").read_text("utf-8")
        blocks = re.findall(r"^translate korean (?!strings:)(\w+):\n\n    # (.*)\n    (.*)\n", text, re.M)
        self.assertEqual(len(blocks), 10)
        for ident, comment, line in blocks:
            self.assertRegex(ident, r"^(start|left|left_local)_[0-9a-f]{8}(_\d+)?$")
            self.assertTrue(line.startswith(comment.split('"')[0]))  # 같은 화자/속성 접두
        self.assertIn('    # e happy "Attributes line" with dissolve\n    e happy "KO:Attributes line" with dissolve\n', text)
        self.assertIn('    # "Narration with [player_name] and {i}tags{/i}."\n    "KO:Narration with [player_name] and {i}tags{/i}."\n', text)
        self.assertIn('    e "KO:Hello, [player_name]! You owe me 100%% of \\"honor\\"."\n', text)
        # 작은따옴표 소스는 큰따옴표로 변환되며 원문 주석에는 디코드된 형태
        self.assertIn('    # e "Right it is, it\'s fine."\n', text)
        self.assertIn("translate korean strings:\n\n", text)
        self.assertIn('    old "Go \\"right\\""\n    new "KO:Go \\"right\\""\n', text)
        self.assertIn('    old "Eileen"\n    new "KO:Eileen"\n', text)
        # 모든 따옴표가 균형 잡힌 문자열
        for line in text.splitlines():
            if line.strip().startswith(("old ", "new ", "e ", '"')):
                self.assertEqual(len(re.findall(r'(?<!\\)"', line)) % 2, 0, line)

    def test_strings_are_unique_across_all_tl_files(self):
        _, _, _, rep = self.pipeline()
        olds = []
        for rel in rep.written:
            if rel.endswith("_lang.rpy"):
                continue
            olds += re.findall(r'^\s+old "(.*)"$', (self.root / rel).read_text("utf-8"), re.M)
        self.assertEqual(len(olds), len(set(olds)))
        self.assertIn("Go left", olds)  # script.rpy / chapter2.rpy 에 모두 있었지만 한 번만

    def test_lang_snippet(self):
        _, _, _, rep = self.pipeline()
        lang = (self.root / "game/tl/korean/_lang.rpy").read_text("utf-8")
        self.assertIn('config.default_language = "korean"', lang)
        self.assertNotIn("translate korean python:\n    _hpatch_font", lang)
        self.restore()
        _, _, _, rep = self.pipeline(default_language=False)
        lang = (self.root / "game/tl/korean/_lang.rpy").read_text("utf-8")
        self.assertNotIn('config.default_language = "korean"', lang)

    def restore(self):
        BackupStore(self.root).restore()

    def test_font_for_renpy(self):
        font = Path(self._td.name) / "Nanum.ttf"
        font.write_bytes(b"fake")
        self.pipeline(font=str(font))
        self.assertEqual((self.root / "game/tl/korean/Nanum.ttf").read_bytes(), b"fake")
        lang = (self.root / "game/tl/korean/_lang.rpy").read_text("utf-8")
        self.assertIn('_hpatch_font = "tl/korean/Nanum.ttf"', lang)

    def test_apply_restore_leaves_tree_identical(self):
        before = snapshot(self.root)
        self.pipeline()
        self.assertNotEqual(snapshot(self.root), before)
        # 원본 .rpy 는 건드리지 않는다
        self.assertEqual((self.root / "game/script.rpy").read_bytes(), before["game/script.rpy"])
        BackupStore(self.root).restore()
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / "game/tl").exists())

    def test_existing_tl_file_is_backed_up_and_restored(self):
        tl = self.root / "game/tl/korean"
        tl.mkdir(parents=True)
        (tl / "script.rpy").write_text("# user's own translation\n", encoding="utf-8")
        before = snapshot(self.root)
        eng, _, _, rep = self.pipeline()
        self.assertTrue(any("tl/korean" in n for n in rep.notes))
        BackupStore(self.root).restore()
        self.assertEqual(snapshot(self.root), before)

    def test_llm_bare_quote_and_bracket_handling(self):
        eng = detect_engine(self.root)
        self.assertEqual(eng.finalize("x", '그가 "안녕" 했다'), '그가 \\"안녕\\" 했다')
        m = eng.masker()
        mr = m.mask("Hi [name]")
        self.assertIsNone(m.restore("안녕 <0> [", mr))  # 맨몸의 [ 는 거부 -> 원문 유지

    def test_only_compiled_files_reports_how_to_extract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            make_renpy(root, with_rpy=False)
            eng = detect_engine(root)
            res = eng.extract(BackupStore(root))
            self.assertFalse(res.ok)
            self.assertEqual(res.units, [])
            msg = " ".join(res.notes)
            self.assertIn(".rpyc 1개", msg)
            self.assertIn(".rpa 1개", msg)
            self.assertIn("unrpa", msg)
            rep = eng.apply(BackupStore(root), {}, ApplyOptions())
            self.assertEqual(rep.written, [])
            self.assertFalse((root / "game/tl").exists())


if __name__ == "__main__":
    unittest.main()
