import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest

import helpers  # noqa: F401
import synth
from soundwatch import audio, config
from soundwatch.cli import main
from soundwatch.store import Store


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = os.path.join(self.td.name, "data")
        self.args = ["--data-dir", self.d]

    def wav(self, name, x):
        p = os.path.join(self.td.name, name)
        audio.write_wav(p, x)
        return p

    def test_calibrate_flow(self):
        code, out, _ = run("calibrate", *self.args)
        self.assertEqual(code, 0)
        self.assertIn("미보정", out)
        code, out, _ = run("calibrate", "--offset", "3.5", "--note", "소음계 테스트", *self.args)
        self.assertEqual(code, 0)
        cfg = config.load_config(os.path.join(self.d, "config.json"))
        self.assertEqual(cfg["calibration"]["offset_db"], 3.5)
        self.assertTrue(cfg["calibration"]["calibrated"])
        self.assertEqual(cfg["calibration"]["note"], "소음계 테스트")
        self.assertIn("사용자 보정", run("calibrate", *self.args)[1])
        run("calibrate", "--meter", "62.0", "--shown", "58.5", *self.args)
        self.assertAlmostEqual(config.load_config(os.path.join(self.d, "config.json"))["calibration"]["offset_db"], 3.5)
        self.assertEqual(run("calibrate", "--offset", "99", *self.args)[0], 2)
        run("calibrate", "--clear", *self.args)
        self.assertFalse(config.load_config(os.path.join(self.d, "config.json"))["calibration"]["calibrated"])

    def test_enroll_templates_analyze(self):
        w1 = self.wav("b1.wav", synth.add_noise(synth.pad(synth.doorbell(), 1.5, 1.5), "white", 15))
        w2 = self.wav("b2.wav", synth.add_noise(synth.pad(synth.doorbell(), 1.2, 1.8), "pink", 12, seed=8))
        code, out, _ = run("enroll", "초인종", w1, "--say", "초인종이 울렸어요", *self.args)
        self.assertEqual(code, 0, out)
        self.assertIn("감지됨", out)
        code, out, _ = run("enroll", "초인종", w2, *self.args)
        self.assertEqual(code, 0, out)
        self.assertIn("샘플 2개", out)
        code, out, _ = run("templates", *self.args)
        self.assertIn("초인종", out)
        self.assertIn("초인종이 울렸어요", out)
        test = self.wav("t.wav", synth.concat(synth.white(4, 0.001, 3), synth.doorbell(), synth.white(4, 0.001, 4)))
        code, out, _ = run("analyze", test, *self.args)
        self.assertEqual(code, 0)
        self.assertIn("[template] 초인종", out)
        code, out, _ = run("analyze", test, "--json", *self.args)
        j = json.loads(out)
        self.assertEqual([d["name"] for d in j["detections"]], ["초인종"])
        self.assertIn("미보정", j["level_basis"])
        code, out, _ = run("analyze", test, "--no-templates", *self.args)
        self.assertNotIn("[template]", out)
        code, out, _ = run("templates", "--remove", "초인종", *self.args)
        self.assertEqual(code, 0)
        self.assertEqual(run("templates", "--remove", "없음", *self.args)[0], 1)

    def test_enroll_failure_message(self):
        w = self.wav("silent.wav", synth.silence(3))
        code, out, err = run("enroll", "x", w, *self.args)
        self.assertEqual(code, 2)
        self.assertIn("등록 실패", err)

    def test_log_then_report_week(self):
        tz = config.tz_of(config.load_config("/nonexistent"))
        yesterday = dt.datetime.now(tz).date() - dt.timedelta(days=1)
        start = "%s 23:10:00" % yesterday.isoformat()
        x = synth.concat(synth.white(3, 0.0003, 1), synth.white(2, 0.05, 2), synth.white(3, 0.0003, 3))
        w = self.wav("n.wav", x)
        os.makedirs(self.d, exist_ok=True)
        cfg = config.load_config(os.path.join(self.d, "config.json"))
        cfg["noise"]["threshold_db"] = 70.0
        config.save_config(os.path.join(self.d, "config.json"), cfg)
        code, out, _ = run("analyze", w, "--log", "--start", start, *self.args)
        self.assertEqual(code, 0, out)
        self.assertIn("소음 이벤트", out)
        outhtml = os.path.join(self.td.name, "rep.html")
        code, out, _ = run("report", "--week", "--out", outhtml, *self.args)
        self.assertEqual(code, 0, out)
        self.assertIn("이벤트 1건, 야간 1건", out)
        self.assertIn("미보정", out + open(outhtml, encoding="utf-8").read())
        with open(outhtml, encoding="utf-8") as f:
            html = f.read()
        self.assertIn(yesterday.isoformat(), html)
        base = os.path.splitext(outhtml)[0]
        with open(base + "_events.csv", "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.decode("utf-8-sig").count("\n"), 2)
        self.assertTrue(os.path.exists(base + "_daily.csv"))
        code, out, _ = run("report", "--week", "--out", outhtml, "--no-csv", *self.args)
        self.assertEqual(code, 0)
        self.assertEqual(run("report", "--from", "2026-11-05", "--to", "2026-11-01", *self.args)[0], 2)

    def test_report_without_db_is_friendly(self):
        code, out, err = run("report", "--week", *self.args)
        self.assertEqual(code, 1)
        self.assertIn("기록 DB", err)

    def test_purge(self):
        os.makedirs(os.path.join(self.d, "clips"))
        p = os.path.join(self.d, "clips", "old.wav")
        with open(p, "wb") as f:
            f.write(b"x")
        os.utime(p, (1, 1))
        code, out, _ = run("purge", "--days", "1", *self.args)
        self.assertIn("1개 삭제", out)
        self.assertFalse(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()


class DefaultDataDirTests(unittest.TestCase):
    def test_default_and_legacy(self):
        from soundwatch.cli import default_data_dir
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(default_data_dir(env={"SOUNDWATCH_HOME": "/x"}, cwd=d), "/x")
            self.assertEqual(default_data_dir(env={}, cwd=d),
                             os.path.join(os.path.expanduser("~"), ".soundwatch"))
            os.makedirs(os.path.join(d, "data"))
            open(os.path.join(d, "data", "soundwatch.db"), "w").close()
            err = io.StringIO()
            self.assertEqual(default_data_dir(env={}, cwd=d, err=err), os.path.join(d, "data"))
            self.assertIn("안내", err.getvalue())
