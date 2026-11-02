import os
import tempfile
import unittest

from demomaker.readme import (END, START, build_section, find_modules, insert_section, make_section,
                              parse_pyproject, parse_subcommands, write_readme)

PYPROJECT = '''
[build-system]
requires = ["setuptools>=61"]

[project]
name = "steam-buddy"
description = "스팀 구매 결정 비서"
requires-python = ">=3.9"

[project.scripts]
steam-buddy = "steam_buddy.cli:main"
'''

HELP = """usage: tool [-h]
            {a,b} ...

설명

positional arguments:
  {a,b}
    a                   첫째 명령
                        이어지는 설명
    b                   둘째

options:
  -h, --help            show this help message and exit
"""


class ParseTests(unittest.TestCase):
    def test_pyproject(self):
        info = parse_pyproject(PYPROJECT)
        self.assertEqual(info["name"], "steam-buddy")
        self.assertEqual(info["description"], "스팀 구매 결정 비서")
        self.assertEqual(info["scripts"], {"steam-buddy": "steam_buddy.cli:main"})

    def test_subcommands_skip_usage_line_and_join_continuations(self):
        self.assertEqual(parse_subcommands(HELP), [("a", "첫째 명령 이어지는 설명"), ("b", "둘째")])

    def test_no_subcommands(self):
        self.assertEqual(parse_subcommands("usage: x [-h]\n\noptions:\n  -h  help\n"), [])

    def test_find_modules(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "pkg"))
            open(os.path.join(d, "pkg", "__main__.py"), "w").close()
            os.makedirs(os.path.join(d, "tests"))
            open(os.path.join(d, "tests", "__main__.py"), "w").close()
            self.assertEqual(find_modules(d, {}), ["pkg"])
            self.assertEqual(find_modules(d, {"x": "pkg.cli:main"}), ["pkg"])


class InsertTests(unittest.TestCase):
    SECTION = START + "\n## 사용법\n내용\n" + END + "\n"

    def test_append_when_no_markers(self):
        out = insert_section("# 제목\n\n설명\n", self.SECTION)
        self.assertTrue(out.startswith("# 제목\n\n설명\n\n" + START))
        self.assertTrue(out.endswith(END + "\n"))

    def test_replace_between_markers_keeps_rest(self):
        text = "# t\n\n" + START + "\n옛날 내용\n" + END + "\n\n## 라이선스\nMIT\n"
        out = insert_section(text, self.SECTION)
        self.assertNotIn("옛날", out)
        self.assertIn("## 라이선스\nMIT\n", out)
        self.assertEqual(out.count(START), 1)

    def test_idempotent(self):
        once = insert_section("# 제목\n", self.SECTION)
        twice = insert_section(once, self.SECTION)
        self.assertEqual(once, twice)
        replaced = insert_section("a\n" + START + "x" + END + "\nb\n", self.SECTION)
        self.assertEqual(replaced, insert_section(replaced, self.SECTION))

    def test_empty_readme(self):
        self.assertEqual(insert_section("", self.SECTION), self.SECTION.strip("\n") + "\n")

    def test_crlf_preserved(self):
        out = insert_section("# t\r\n", self.SECTION)
        self.assertNotIn("\n\n", out.replace("\r\n", ""))
        self.assertEqual(out.count("\r\n"), out.count("\n"))

    def test_backslashes_in_section_survive(self):
        sec = START + "\n| a \\| b |\n" + END + "\n"
        out = insert_section("x\n" + START + "y" + END + "\n", sec)
        self.assertIn("a \\| b", out)

    def test_write_readme_no_overwrite_semantics(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "README.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# 내 프로젝트\n\n소개글\n")
            self.assertTrue(write_readme(p, self.SECTION))
            first = open(p, encoding="utf-8").read()
            self.assertIn("소개글", first)
            self.assertFalse(write_readme(p, self.SECTION))   # 두 번째는 변경 없음
            self.assertEqual(first, open(p, encoding="utf-8").read())


class MakeSectionTests(unittest.TestCase):
    def make_project(self, d):
        os.makedirs(os.path.join(d, "demo_cli"))
        open(os.path.join(d, "demo_cli", "__init__.py"), "w").close()
        with open(os.path.join(d, "demo_cli", "__main__.py"), "w", encoding="utf-8") as f:
            f.write(
                "import argparse\n"
                "p = argparse.ArgumentParser(prog='demo-cli', description='데모 도구')\n"
                "s = p.add_subparsers(dest='c')\n"
                "s.add_parser('hello', help='인사하기')\n"
                "s.add_parser('bye', help='작별')\n"
                "p.parse_args()\n")
        with open(os.path.join(d, "pyproject.toml"), "w", encoding="utf-8") as f:
            f.write('[project]\nname = "demo-cli"\ndescription = "데모 도구"\n[project.scripts]\ndemo-cli = "demo_cli.__main__:main"\n')

    def test_section_from_real_argparse_project(self):
        with tempfile.TemporaryDirectory() as d:
            self.make_project(d)
            before = sorted(os.listdir(d))
            section, info = make_section(d, "docs/demo.svg")
            self.assertEqual(sorted(os.listdir(d)), before)       # 프로젝트에 파일을 남기지 않음
            self.assertTrue(section.startswith(START))
            self.assertTrue(section.rstrip().endswith(END))
            self.assertIn("![demo-cli 데모](docs/demo.svg)", section)
            self.assertIn("| `hello` | 인사하기 |", section)
            self.assertIn("demo-cli --help", section)
            self.assertIn("python -m demo_cli --help", section)
            self.assertIn("hello --help", section)

    def test_build_section_without_svg(self):
        info = {"name": "x", "description": None, "scripts": {}, "modules": ["x"]}
        self.assertNotIn("![", build_section(info, None, "usage: x", []))


if __name__ == "__main__":
    unittest.main()
