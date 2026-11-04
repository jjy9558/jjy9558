import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from lifecourt.cli import main

# sound-watch/soundwatch/store.py 의 SCHEMA 를 그대로 옮긴 텍스트(원본은 건드리지 않는다)
SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  start REAL NOT NULL, end REAL NOT NULL, duration REAL NOT NULL,
  peak_dbfs REAL NOT NULL, leq_dbfs REAL NOT NULL,
  kind TEXT NOT NULL DEFAULT '', n_segments INTEGER NOT NULL DEFAULT 1,
  clip TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_start ON events(start);
CREATE TABLE IF NOT EXISTS minutes(
  minute INTEGER PRIMARY KEY,
  sum_power REAL NOT NULL,
  n INTEGER NOT NULL,
  max_dbfs REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS detections(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_det_ts ON detections(ts);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
NOW = datetime(2026, 11, 4, 8, 0)


def ts(day, hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, day, h, m).timestamp()


class WakeupBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db = Path(self._td.name) / "soundwatch.db"
        c = sqlite3.connect(self.db)
        c.executescript(SCHEMA)
        c.commit()
        c.close()

    def add_event(self, start, end, peak, leq=None, kind="impact"):
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO events(start,end,duration,peak_dbfs,leq_dbfs,kind) VALUES(?,?,?,?,?,?)",
                  (start, end, end - start, peak, peak - 8 if leq is None else leq, kind))
        c.commit()
        c.close()

    def add_det(self, t, kind, name):
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO detections(ts,kind,name,score,message) VALUES(?,?,?,?,?)", (t, kind, name, 0.9, "m"))
        c.commit()
        c.close()

    def run_cli(self, extra, now=NOW):
        out, err = io.StringIO(), io.StringIO()
        rc = main(["wakeup", "--db", str(self.db), *extra], out, err=err, now=now, env={})
        return rc, out.getvalue(), err.getvalue()


class Culprit(WakeupBase):
    def test_loudest_longest_wins(self):
        self.add_event(ts(4, "01:00"), ts(4, "01:00") + 2, -40.0)
        self.add_event(ts(4, "03:10"), ts(4, "03:10") + 20, -18.0)
        self.add_det(ts(4, "03:10") + 3, "template", "세탁기 종료음")
        rc, out, _ = self.run_cli(["--seed", "x"])
        self.assertEqual(rc, 0)
        self.assertIn("세탁기 종료음", out)
        self.assertIn("03:10", out)
        self.assertIn("23시 전", out)
        self.assertIn("상대값", out)

    def test_woke_time_picks_closest(self):
        self.add_event(ts(4, "01:00"), ts(4, "01:00") + 30, -10.0)
        self.add_event(ts(4, "05:40"), ts(4, "05:40") + 1, -50.0)
        self.add_det(ts(4, "05:40"), "template", "초인종")
        rc, out, _ = self.run_cli(["--woke", "05:42", "--seed", "x"])
        self.assertEqual(rc, 0)
        self.assertIn("피고 초인종", out)
        self.assertIn("05:42", out)
        rc, out2, _ = self.run_cli(["--seed", "x"])
        self.assertIn("피고 정체불명의 소음", out2)

    def test_woke_outside_window_rejected(self):
        rc, _, err = self.run_cli(["--woke", "09:00"])
        self.assertEqual(rc, 2)
        self.assertIn("밖", err)

    def test_deterministic(self):
        self.add_event(ts(4, "02:00"), ts(4, "02:00") + 5, -20.0)
        a = self.run_cli(["--seed", "s1"])[1]
        self.assertEqual(a, self.run_cli(["--seed", "s1"])[1])


class EmptyNight(WakeupBase):
    def test_empty_is_acquittal(self):
        rc, out, _ = self.run_cli([])
        self.assertEqual(rc, 0)
        self.assertIn("없음", out)
        self.assertIn("sound-watch 실행 여부", out)

    def test_events_outside_window_ignored(self):
        self.add_event(ts(4, "08:30"), ts(4, "08:31"), -5.0)
        rc, out, _ = self.run_cli([])
        self.assertIn("피고", out)
        self.assertIn("없음", out)


class AcrossMidnight(WakeupBase):
    def test_window_across_midnight(self):
        self.add_event(ts(3, "23:30"), ts(3, "23:30") + 4, -22.0)
        self.add_event(ts(4, "00:20"), ts(4, "00:20") + 1, -45.0)
        self.add_event(ts(3, "20:00"), ts(3, "20:00") + 9, -5.0)    # 창 밖
        rc, out, _ = self.run_cli(["--from", "23:00", "--to", "06:00", "--seed", "x"])
        self.assertEqual(rc, 0)
        self.assertIn("23:30:00", out)
        self.assertIn("11-03 23:00", out)
        self.assertNotIn("20:00:00", out)

    def test_woke_after_midnight_in_cross_window(self):
        self.add_event(ts(3, "23:30"), ts(3, "23:31"), -40.0)
        self.add_event(ts(4, "00:20"), ts(4, "00:21"), -40.0)
        self.add_det(ts(4, "00:20"), "template", "세탁기")
        rc, out, _ = self.run_cli(["--from", "23:00", "--to", "06:00", "--woke", "00:25", "--seed", "x"])
        self.assertIn("피고 세탁기", out)


class NoPeople(WakeupBase):
    def test_people_named_template_becomes_unknown(self):
        self.add_event(ts(4, "02:00"), ts(4, "02:00") + 6, -15.0)
        self.add_det(ts(4, "02:00") + 1, "template", "윗집 아저씨 쿵쿵")
        h = Path(self._td.name) / "o.html"
        rc, out, _ = self.run_cli(["--seed", "x", "--html", str(h)])
        self.assertEqual(rc, 0)
        for w in ("윗집", "아저씨"):
            self.assertNotIn(w, out)
            self.assertNotIn(w, h.read_text(encoding="utf-8"))
        self.assertIn("정체불명의 소음", out)
        self.assertIn("찾아가지 마세요", out)

    def test_generic_noise_defendant(self):
        self.add_event(ts(4, "04:00"), ts(4, "04:00") + 3, -25.0)
        rc, out, _ = self.run_cli(["--seed", "x"])
        self.assertIn("피고 정체불명의 소음", out)
        self.assertIn("찾아가지 마세요", out)

    def test_safe_name(self):
        from lifecourt.wakeup import UNKNOWN, safe_name
        self.assertEqual(safe_name("세탁기 종료음")[0], "세탁기 종료음")
        self.assertEqual(safe_name("옆집 청소기")[0], UNKNOWN)
        self.assertEqual(safe_name("")[0], UNKNOWN)


class ReadOnly(WakeupBase):
    def test_db_bytes_unchanged(self):
        self.add_event(ts(4, "02:00"), ts(4, "02:00") + 6, -15.0)
        self.add_det(ts(4, "02:01"), "knock", "")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        files_before = sorted(p.name for p in self.db.parent.iterdir())
        rc, _, _ = self.run_cli(["--seed", "x"])
        self.assertEqual(rc, 0)
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.assertEqual(files_before, sorted(p.name for p in self.db.parent.iterdir()))

    def test_missing_db_not_created(self):
        missing = Path(self._td.name) / "nope.db"
        out, err = io.StringIO(), io.StringIO()
        rc = main(["wakeup", "--db", str(missing)], out, err=err, now=NOW, env={})
        self.assertEqual(rc, 2)
        self.assertFalse(missing.exists())


class Speak(WakeupBase):
    def test_speak_uses_notify(self):
        calls = []

        class Resp:
            status = 200
            def read(self, *a): return b'{"ok": true}'
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class Opener:
            def open(self, req, timeout=None):
                calls.append(req.data)
                return Resp()

        self.add_event(ts(4, "02:00"), ts(4, "02:00") + 6, -15.0)
        out, err = io.StringIO(), io.StringIO()
        env = {"LIFECOURT_SIDEKICK_TOKEN": "t" + "k" * 6, "LIFECOURT_SIDEKICK_URL": "http://127.0.0.1:1"}
        rc = main(["wakeup", "--db", str(self.db), "--speak"], out, err=err, now=NOW, env=env, opener=Opener())
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("피고", json.loads(calls[0])["text"])


if __name__ == "__main__":
    unittest.main()


class DefaultDbTests(unittest.TestCase):
    def test_default_db_location(self):
        from pathlib import Path
        from lifecourt.wakeup import default_db
        self.assertEqual(default_db({"SOUNDWATCH_HOME": "/x"}), Path("/x/soundwatch.db"))
        import os, tempfile
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                self.assertEqual(default_db({}), Path.home() / ".soundwatch" / "soundwatch.db")
            finally:
                os.chdir(cwd)
