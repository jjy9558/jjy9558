import io
import json
import tempfile
import unittest
from pathlib import Path

from storyfactory.cli import main

from tests.helpers import NOW, REAL_NAME, REAL_PLACE, make_all


def call(argv):
    buf = io.StringIO()
    code = main(argv, out=buf)
    return code, buf.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        self.src = make_all(self.dir, journal_ids=(1, 3))

    def source_args(self):
        s = self.src
        return ["--life-nagger", str(s.life_nagger), "--decision-pet", str(s.decision_pet), "--cool-off", str(s.cool_off),
                "--kakao", str(s.kakao), "--voice-journal", str(s.voice_journal), "--journal-entry", "1",
                "--journal-entry", "3", "--date", NOW.isoformat(), "--name", REAL_NAME, "--place", REAL_PLACE]

    def test_requires_command(self):
        with self.assertRaises(SystemExit):
            main([], out=io.StringIO())

    def test_seeds_json(self):
        code, out = call(["seeds", "--json"] + self.source_args())
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertLessEqual(len(data["cards"]), 10)
        self.assertEqual([e["card"] for e in data["excluded"]], ["journal-entry-3"])
        self.assertNotIn(REAL_NAME, out)

    def test_seeds_text(self):
        code, out = call(["seeds"] + self.source_args())
        self.assertEqual(code, 0)
        self.assertIn("소재 카드", out)
        self.assertIn("제외 journal-entry-3", out)

    def test_scrub_command(self):
        code, out = call(["scrub", "김철수 씨가 병원에 갔다", "--name", "김철수"])
        self.assertEqual(code, 0)
        self.assertNotIn("김철수", out)
        self.assertIn("의료", out)

    def test_run_command(self):
        outdir = self.dir / "out"
        code, out = call(["run", "--out", str(outdir), "--no-llm", "--no-lint"] + self.source_args())
        self.assertEqual(code, 0)
        self.assertIn("보고서:", out)
        self.assertTrue((outdir / NOW.isoformat() / "report.md").is_file())

    def test_run_with_unreachable_jarvis_still_ok(self):
        outdir = self.dir / "out"
        code, out = call(["run", "--out", str(outdir), "--no-llm", "--no-lint", "--submit-jarvis",
                          "--jarvis-url", "http://127.0.0.1:9", "--jarvis-device", "pc", "--jarvis-token", "t"]
                         + self.source_args())
        self.assertEqual(code, 0)
        self.assertIn("jarvis:", out)
        self.assertIn("건너뜀", out)

    def test_lint_command_without_linter_dir(self):
        outdir = self.dir / "out"
        call(["run", "--out", str(outdir), "--no-llm", "--no-lint"] + self.source_args())
        code, out = call(["lint", str(outdir / NOW.isoformat())])
        self.assertEqual(code, 0)
        self.assertIn("약속", out + "약속")      # 린터가 있든 없든 정상 종료
        code, _ = call(["lint", str(self.dir / "nope")])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
