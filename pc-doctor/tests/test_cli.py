import contextlib
import io
import json
import os
import unittest

from fakes import FakeSystem, TempHome

from pc_doctor import cli


def run(argv, system):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(argv, system=system)
    return code, buf.getvalue()


class CliTests(unittest.TestCase):
    def test_end_to_end_with_fake(self):
        with TempHome() as home:
            s = FakeSystem()
            code, out = run(["snapshot"], s)
            self.assertEqual(code, 0)
            self.assertIn("RTX 5080", out)
            run(["snapshot"], s)
            code, out = run(["diff"], s)
            self.assertIn("변경 없음", out)
            code, out = run(["check", "--json"], s)
            self.assertIsInstance(json.loads(out), list)
            code, out = run(["upgrade"], s)
            self.assertIn("가격", out)
            code, out = run(["tweaks", "list"], s)
            self.assertIn("classic_context_menu", out)
            code, out = run(["tweaks", "apply", "--dry-run"], s)
            self.assertIn("[dry-run]", out)
            code, out = run(["tweaks", "apply", "show_extensions", "--restart-explorer"], s)
            self.assertIn(("restart_explorer",), s.calls)
            code, out = run(["tweaks", "restore"], s)
            self.assertEqual(code, 0)
            code, out = run(["updates", "--no-pending"], s)
            self.assertIn("신호등", out)
            code, out = run(["gamemode", "status"], s)
            self.assertIn("OFF", out)
            code, out = run(["init"], s)
            self.assertTrue(os.path.exists(os.path.join(home, "config.json")))
            self.assertTrue(os.path.exists(os.path.join(home, "known_issues.json")))

    def test_unknown_tweak_id_is_error(self):
        with TempHome():
            code, _ = run(["tweaks", "apply", "nope"], FakeSystem())
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
