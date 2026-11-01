import contextlib
import io
import json
import os
import shutil
import socket
import tempfile
import unittest

from subvocab.cli import main

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def data_rows(tsv_text):
    return [ln for ln in tsv_text.splitlines() if ln and not ln.startswith("#")]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.known = os.path.join(self.tmp, "known.txt")
        with open(self.known, "w", encoding="utf-8") as fh:
            fh.write("go\n")
        self.out = os.path.join(self.tmp, "out")

    def test_end_to_end_without_llm(self):
        code, stdout, _ = run([
            os.path.join(FIXTURES, "youtube_auto.vtt"),
            os.path.join(FIXTURES, "sample.srt"),
            "--known", self.known, "--out", self.out, "--no-llm",
        ])
        self.assertEqual(code, 0)
        self.assertIn("뜻: 생성 안 함", stdout)
        md = read(os.path.join(self.out, "vocab.md"))
        tsv = read(os.path.join(self.out, "anki.tsv"))
        self.assertIn("| 1 |", md)
        self.assertIn("youtube_auto.vtt", md)
        self.assertTrue(tsv.startswith("#separator:tab\n#html:true\n"))
        self.assertFalse(any(row.startswith("go\t") for row in tsv.splitlines()))  # "go" is known

    def test_top_limits_rows(self):
        run([os.path.join(FIXTURES, "sample.srt"), "--known", self.known, "--out", self.out,
             "--no-llm", "--top", "2"])
        md = read(os.path.join(self.out, "vocab.md"))
        self.assertIn("| 2 |", md)
        self.assertNotIn("| 3 |", md)
        self.assertEqual(len(data_rows(read(os.path.join(self.out, "anki.tsv")))), 2)

    def test_add_known_only(self):
        code, _, _ = run(["--add-known", "Hello", "hello", "--known", self.known])
        self.assertEqual(code, 0)
        words = read(self.known).split()
        self.assertEqual(words.count("hello"), 1)

    def test_add_known_then_process(self):
        code, _, _ = run([os.path.join(FIXTURES, "sample.srt"), "--add-known", "store",
                          "--known", self.known, "--out", self.out, "--no-llm"])
        self.assertEqual(code, 0)
        self.assertIn("store", read(self.known).split())
        self.assertNotIn("| store |", read(os.path.join(self.out, "vocab.md")))

    def test_missing_input_file_fails_cleanly(self):
        code, _, stderr = run([os.path.join(self.tmp, "missing.srt"), "--no-llm", "--out", self.out])
        self.assertEqual(code, 1)
        self.assertIn("파일이 없습니다", stderr)

    def test_unreachable_llm_still_writes_blank_meanings(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = os.path.join(self.tmp, "llm.json")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"base_url": "http://127.0.0.1:%d/v1" % port, "timeout": 2}, fh)
        code, stdout, stderr = run([
            os.path.join(FIXTURES, "sample.srt"), "--known", self.known, "--out", self.out,
            "--config", config,
        ])
        self.assertEqual(code, 0)
        self.assertIn("뜻: 0/", stdout)
        self.assertIn("LLM 사용 불가", stderr)
        rows = data_rows(read(os.path.join(self.out, "anki.tsv")))
        self.assertTrue(rows)
        self.assertTrue(all(len(row.split("\t")) == 4 for row in rows))
        self.assertTrue(all(row.split("\t")[1] == "" for row in rows))


if __name__ == "__main__":
    unittest.main()
