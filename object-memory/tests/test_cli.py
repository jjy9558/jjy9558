import contextlib
import io
import json
import os
import unittest

import helpers
from helpers import jpeg
from objmem import cli


def run(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(args)
    return rc, buf.getvalue()


class CliTest(unittest.TestCase, helpers.TmpCase):
    def test_init_status_ingest_no_describe_ask_purge(self):
        tmp = self.make_tmp()
        conf = os.path.join(tmp, "c.json")
        self.assertEqual(run(["--config", conf, "config", "--init"])[0], 0)
        cfg = json.load(open(conf))
        self.assertGreaterEqual(len(cfg["token"]), 20)
        self.assertEqual(cfg["host"], "127.0.0.1")
        self.assertEqual(cfg["base_url"], "http://127.0.0.1:8080/v1")
        cfg["data_dir"] = os.path.join(tmp, "d")
        json.dump(cfg, open(conf, "w"))
        self.assertEqual(run(["--config", conf, "config", "--init"])[0], 1)    # 덮어쓰지 않음
        rc, out = run(["--config", conf, "config"])
        self.assertNotIn(cfg["token"], out)                                    # 토큰은 가려서 출력
        src = os.path.join(tmp, "s")
        os.makedirs(src)
        open(os.path.join(src, "IMG_20261101_100000.jpg"), "wb").write(jpeg())
        rc, out = run(["--config", conf, "ingest", src, "--camera", "거실", "--no-describe"])
        self.assertEqual(json.loads(out)["stored"], 1)
        self.assertEqual(json.loads(run(["--config", conf, "status"])[1])["pending"], 1)
        rc, out = run(["--config", conf, "ask", "리모컨", "어디", "뒀지?"])
        self.assertIn("본 기록이 없어요", out)
        self.assertEqual(run(["--config", conf, "purge"])[0], 0)


if __name__ == "__main__":
    unittest.main()
