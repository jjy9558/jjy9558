"""sqlite 저장소: files(경로별 메타) + captions(파일 해시별 캡션 캐시)."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY, hash TEXT, bytes INTEGER, mtime REAL,
  game TEXT, appid TEXT, ts TEXT, width INTEGER, height INTEGER,
  thumb TEXT, skip_reason TEXT);
CREATE TABLE IF NOT EXISTS captions(
  hash TEXT PRIMARY KEY, caption TEXT, mood TEXT, tags TEXT,
  notable INTEGER, model TEXT, created TEXT);
"""


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Store:
    def __init__(self, path):
        if path != ":memory:":
            d = os.path.dirname(os.path.abspath(path))
            os.makedirs(d, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def upsert_shots(self, shots, max_bytes):
        """shots 를 반영. (size, mtime)이 같으면 재해시 안 함. 통계 반환."""
        stats = {"new": 0, "unchanged": 0, "skipped": 0}
        for s in shots:
            row = self.db.execute("SELECT * FROM files WHERE path=?", (s["path"],)).fetchone()
            reason = None
            h = None
            if max_bytes and s["bytes"] > max_bytes:
                reason = "파일이 너무 큼 (%.1fMB > %.1fMB)" % (s["bytes"] / 1048576.0, max_bytes / 1048576.0)
                stats["skipped"] += 1
            elif row and row["bytes"] == s["bytes"] and row["mtime"] == s["mtime"] and row["hash"]:
                h = row["hash"]
                stats["unchanged"] += 1
            else:
                h = file_hash(s["path"])
                stats["new"] += 1
            self.db.execute(
                "INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (s["path"], h, s["bytes"], s["mtime"], s["game"], s["appid"],
                 s["ts"].isoformat(), s["width"], s["height"], s["thumb"], reason))
        self.db.commit()
        return stats

    def prune_missing(self):
        n = 0
        for r in self.db.execute("SELECT path FROM files").fetchall():
            if not os.path.exists(r["path"]):
                self.db.execute("DELETE FROM files WHERE path=?", (r["path"],))
                n += 1
        self.db.commit()
        return n

    def pending(self, limit=None):
        """캡션이 없는 (해시 기준 중복 제거) 파일들, 오래된 순."""
        q = ("SELECT f.* FROM files f LEFT JOIN captions c ON c.hash=f.hash "
             "WHERE f.skip_reason IS NULL AND f.hash IS NOT NULL AND c.hash IS NULL "
             "GROUP BY f.hash ORDER BY f.ts")
        if limit:
            q += " LIMIT %d" % int(limit)
        return [dict(r) for r in self.db.execute(q).fetchall()]

    def save_caption(self, h, data, model=""):
        self.db.execute(
            "INSERT OR REPLACE INTO captions VALUES(?,?,?,?,?,?,?)",
            (h, data.get("caption", ""), data.get("mood", ""),
             json.dumps(data.get("tags", []), ensure_ascii=False),
             1 if data.get("notable") else 0, model, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def skipped(self):
        return [dict(r) for r in self.db.execute(
            "SELECT path, skip_reason FROM files WHERE skip_reason IS NOT NULL")]

    def all_shots(self):
        rows = self.db.execute(
            "SELECT f.*, c.caption, c.mood, c.tags, c.notable FROM files f "
            "LEFT JOIN captions c ON c.hash=f.hash ORDER BY f.ts, f.path").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["ts"] = datetime.fromisoformat(d["ts"])
            try:
                d["tags"] = json.loads(d["tags"]) if d["tags"] else []
            except ValueError:
                d["tags"] = []
            d["notable"] = bool(d["notable"])
            d["caption"] = d["caption"] or ""
            d["mood"] = d["mood"] or ""
            out.append(d)
        return out
