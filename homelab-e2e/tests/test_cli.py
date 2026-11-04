import io
import unittest

from homelabe2e.cli import SCENARIOS, main
from homelabe2e.harness import FakeLLM, child_env, free_port, http_json
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_projects(self):
        buf = io.StringIO()
        self.assertEqual(main(["projects"], out=buf), 0)
        text = buf.getvalue()
        for sid, _, _ in SCENARIOS:
            self.assertIn("시나리오 %s" % sid, text)

    def test_requires_command(self):
        with self.assertRaises(SystemExit):
            main([], out=io.StringIO())


class HarnessTests(unittest.TestCase):
    def test_child_env_drops_proxies_and_project_vars(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:1", "VJOURNAL_DB": "/x",
                                          "SIDEKICK_TOKEN": "t", "KEEP_ME": "1"}):
            env = child_env(Path("/tmp/h"), {"EXTRA": "2"})
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("VJOURNAL_DB", env)
        self.assertNotIn("SIDEKICK_TOKEN", env)
        self.assertEqual((env["KEEP_ME"], env["EXTRA"], env["HOME"]), ("1", "2", str(Path("/tmp/h"))))
        self.assertIn("127.0.0.1", env["NO_PROXY"])

    def test_fake_llm_roundtrip(self):
        llm = FakeLLM(lambda body: "pong:" + FakeLLM.text_of(body))
        self.addCleanup(llm.close)
        code, r = http_json("POST", llm.url + "/chat/completions",
                            {"messages": [{"role": "user", "content": "ping"}]})
        self.assertEqual(code, 200)
        self.assertEqual(r["choices"][0]["message"]["content"], "pong:ping")
        self.assertEqual(len(llm.requests), 1)

    def test_free_port_is_bindable(self):
        import socket
        p = free_port()
        s = socket.socket()
        self.addCleanup(s.close)
        s.bind(("127.0.0.1", p))


if __name__ == "__main__":
    unittest.main()
