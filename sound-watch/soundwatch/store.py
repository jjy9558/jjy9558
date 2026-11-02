"""SQLite 저장소: 소음 이벤트, 분 단위 집계, 인식 기록."""
from __future__ import annotations

import sqlite3
import threading
from typing import Any, Dict, List, Optional

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
  minute INTEGER PRIMARY KEY,         -- epoch // 60
  sum_power REAL NOT NULL,            -- 100 ms 프레임 파워(선형) 합
  n INTEGER NOT NULL,                 -- 프레임 수 (1프레임 = 0.1초)
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


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.commit()
            self._db.close()

    def commit(self):
        with self._lock:
            self._db.commit()

    # ------------------------------------------------------------------ 이벤트
    def add_event(self, start: float, end: float, peak_dbfs: float, leq_dbfs: float, kind: str = "",
                  n_segments: int = 1, clip: Optional[str] = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(start,end,duration,peak_dbfs,leq_dbfs,kind,n_segments,clip) VALUES(?,?,?,?,?,?,?,?)",
                (start, end, max(0.0, end - start), peak_dbfs, leq_dbfs, kind, n_segments, clip))
            self._db.commit()
            return int(cur.lastrowid)

    def set_clip(self, event_id: int, clip: Optional[str]):
        with self._lock:
            self._db.execute("UPDATE events SET clip=? WHERE id=?", (clip, event_id))
            self._db.commit()

    def events_between(self, a: float, b: float) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM events WHERE start>=? AND start<? ORDER BY start", (a, b)).fetchall()
        return [dict(r) for r in rows]

    def clips_older_than(self, cutoff_epoch: float) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT id, clip FROM events WHERE clip IS NOT NULL AND start<?", (cutoff_epoch,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ 분 집계
    def add_minute(self, minute: int, sum_power: float, n: int, max_dbfs: float):
        with self._lock:
            self._db.execute(
                "INSERT INTO minutes(minute,sum_power,n,max_dbfs) VALUES(?,?,?,?) "
                "ON CONFLICT(minute) DO UPDATE SET sum_power=sum_power+excluded.sum_power, n=n+excluded.n, "
                "max_dbfs=MAX(max_dbfs, excluded.max_dbfs)",
                (minute, sum_power, n, max_dbfs))

    def minutes_between(self, a: float, b: float) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM minutes WHERE minute>=? AND minute<? ORDER BY minute",
                                    (int(a // 60), int(b // 60) + 1)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ 인식 기록
    def add_detection(self, ts: float, kind: str, name: str, score: float, message: str) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO detections(ts,kind,name,score,message) VALUES(?,?,?,?,?)",
                                   (ts, kind, name, score, message))
            self._db.commit()
            return int(cur.lastrowid)

    def detections_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM detections ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def detections_between(self, a: float, b: float) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM detections WHERE ts>=? AND ts<? ORDER BY ts", (a, b)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            r = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value: str):
        with self._lock:
            self._db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, value))
            self._db.commit()
