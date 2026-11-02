import json
import os
import subprocess
import sys
import tempfile
import unittest

import helpers as H

from devguard import config, snippet


class SnippetTests(unittest.TestCase):
    def test_structure(self):
        data = snippet.build("python", "/opt/dg/devguard_run.py")
        hooks = data["hooks"]
        self.assertEqual(set(hooks), {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"})
        pre = hooks["PreToolUse"][0]
        self.assertIn("Bash", pre["matcher"])
        self.assertIn("Edit", pre["matcher"])
        cmd = pre["hooks"][0]
        self.assertEqual(cmd["type"], "command")
        self.assertEqual(cmd["command"], "python /opt/dg/devguard_run.py strike")
        self.assertEqual(hooks["Stop"][0]["hooks"][0]["command"], "python /opt/dg/devguard_run.py wallet stop-hook")
        self.assertNotIn("matcher", hooks["Stop"][0])

    def test_only_and_no_stop(self):
        self.assertEqual(set(snippet.build("p", "l", only=["strike"])["hooks"]),
                         {"PreToolUse", "PostToolUse", "PostToolUseFailure"})
        self.assertEqual(set(snippet.build("p", "l", only=["wallet"])["hooks"]), {"Stop"})
        self.assertNotIn("Stop", snippet.build("p", "l", stop=False)["hooks"])

    def test_windows_paths_and_spaces(self):
        data = snippet.build("C:\\Program Files\\Python\\python.exe", "C:\\Users\\me\\dev guard\\devguard_run.py")
        cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, '"C:/Program Files/Python/python.exe" "C:/Users/me/dev guard/devguard_run.py" strike')

    def test_render_is_valid_json(self):
        json.loads(snippet.render())

    def test_cli_prints_json_only_on_stdout_and_edits_nothing(self):
        home = tempfile.mkdtemp()
        try:
            env = H.git_env({"DEVGUARD_HOME": home, "HOME": home, "USERPROFILE": home})
            p = subprocess.run([sys.executable, "-m", "devguard", "settings-snippet"], cwd=H.ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 0, p.stderr)
            data = json.loads(p.stdout.decode("utf-8"))
            self.assertIn("hooks", data)
            self.assertIn("settings.json", p.stderr.decode("utf-8"))
            self.assertEqual(os.listdir(home), [])  # 아무 파일도 만들지 않음
        finally:
            H.rmtree(home)


class ConfigTests(unittest.TestCase):
    def test_merge_order(self):
        home = tempfile.mkdtemp()
        repo = tempfile.mkdtemp()
        try:
            H.write(os.path.join(home, "config.json"), json.dumps({"friday": {"after": "10:00"}, "wallet": {"daily_limit_usd": 5}}))
            H.write(os.path.join(repo, ".devguard.json"), json.dumps({"friday": {"after": "12:00", "branches": ["prod"]}}))
            os.makedirs(os.path.join(repo, "sub", "dir"))
            cfg = config.load_config(os.path.join(repo, "sub", "dir"), home=home)
            self.assertEqual(cfg["friday"]["after"], "12:00")
            self.assertEqual(cfg["friday"]["branches"], ["prod"])
            self.assertEqual(cfg["friday"]["days"], ["fri"])
            self.assertEqual(cfg["wallet"]["daily_limit_usd"], 5)
        finally:
            H.rmtree(home)
            H.rmtree(repo)

    def test_broken_json_ignored(self):
        home = tempfile.mkdtemp()
        try:
            H.write(os.path.join(home, "config.json"), "{oops")
            self.assertEqual(config.load_config(None, home=home)["friday"]["after"], "15:00")
        finally:
            H.rmtree(home)

    def test_defaults_not_mutated(self):
        cfg = config.load_config(None, home=tempfile.gettempdir() + "/nonexistent-dg-home")
        cfg["friday"]["branches"].append("zzz")
        self.assertNotIn("zzz", config.DEFAULTS["friday"]["branches"])


if __name__ == "__main__":
    unittest.main()
