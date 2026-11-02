"""sqlite 저장소 + 검색 색인 (FTS5 가 있으면 사용, 없으면 자체 bigram 테이블)."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import synonyms

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames(
  id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, camera TEXT NOT NULL DEFAULT '',
  ts REAL NOT NULL, path TEXT, phash TEXT, status TEXT NOT NULL DEFAULT 'pending',
  scene TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS frames_cam_ts ON frames(camera, ts);
CREATE TABLE IF NOT EXISTS sightings(
  id INTEGER PRIMARY KEY, frame_id INTEGER NOT NULL, name TEXT NOT NULL, where_text TEXT NOT NULL DEFAULT '',
  conf REAL NOT NULL DEFAULT 0.5, ts REAL NOT NULL, camera TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS sight_frame ON sightings(frame_id);
CREATE INDEX IF NOT EXISTS sight_ts ON sightings(ts);
CREATE TABLE IF NOT EXISTS desc_cache(sha256 TEXT PRIMARY KEY, json TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sgram(gram TEXT NOT NULL, sid INTEGER NOT NULL, col TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sgram_g ON sgram(col, gram);
CREATE INDEX IF NOT EXISTS sgram_s ON sgram(sid);
"""
BOUNDARY = "zzb"


def fts5_available():
    """런타임에 FTS5 가 컴파일되어 있는지 확인."""
    try:
        c = sqlite3.connect(":memory:")
        try:
            opts = {r[0] for r in c.execute("PRAGMA compile_options")}
            if "ENABLE_FTS5" in opts:
                return True
            c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
            return True
        finally:
            c.close()
    except sqlite3.Error:
        return False


class Store:
    def __init__(self, db_path, use_fts=None):
        self.db_path = db_path
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.lock = threading.RLock()
        self.use_fts = fts5_available() if use_fts is None else bool(use_fts and fts5_available())
        with self.conn() as c:
            c.executescript(SCHEMA)
            if self.use_fts:
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS sfts USING fts5(nm, wh)")

    @contextmanager
    def conn(self):
        with self.lock:
            c = sqlite3.connect(self.db_path, timeout=30)
            c.row_factory = sqlite3.Row
            try:
                yield c
                c.commit()
            finally:
                c.close()

    # ------------------------------------------------------------ frames
    def frame_by_sha(self, sha):
        with self.conn() as c:
            return c.execute("SELECT * FROM frames WHERE sha256=?", (sha,)).fetchone()

    def frame(self, fid):
        with self.conn() as c:
            return c.execute("SELECT * FROM frames WHERE id=?", (fid,)).fetchone()

    def last_phash(self, camera):
        with self.conn() as c:
            r = c.execute("SELECT phash FROM frames WHERE camera=? AND phash IS NOT NULL ORDER BY ts DESC, id DESC LIMIT 1",
                          (camera,)).fetchone()
        return r["phash"] if r else None

    def add_frame(self, sha, camera, ts, path, phash, size):
        with self.conn() as c:
            cur = c.execute("INSERT INTO frames(sha256,camera,ts,path,phash,status,size) VALUES(?,?,?,?,?,'pending',?)",
                            (sha, camera, ts, path, phash, size))
            return cur.lastrowid

    def pending_frames(self, limit=100):
        with self.conn() as c:
            return c.execute("SELECT * FROM frames WHERE status!='described' AND path IS NOT NULL ORDER BY ts LIMIT ?",
                             (limit,)).fetchall()

    def cache_get(self, sha):
        with self.conn() as c:
            r = c.execute("SELECT json FROM desc_cache WHERE sha256=?", (sha,)).fetchone()
        return json.loads(r["json"]) if r else None

    def cache_put(self, sha, desc, now):
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO desc_cache(sha256,json,ts) VALUES(?,?,?)",
                      (sha, json.dumps(desc, ensure_ascii=False), now))

    # ------------------------------------------------------------ 색인
    def _index_text(self, name):
        parts = [synonyms.grams(t) for t in synonyms.expand(name)]
        out = []
        for p in parts:
            if p:
                out.extend(p)
                out.append(BOUNDARY)
        return " ".join(out)

    def _index_add(self, c, sid, name, where):
        nm = self._index_text(name)
        wh = " ".join(synonyms.grams(where))
        if self.use_fts:
            c.execute("INSERT INTO sfts(rowid,nm,wh) VALUES(?,?,?)", (sid, nm, wh))
        else:
            rows = [(g, sid, "nm") for g in set(nm.split()) if g != BOUNDARY]
            rows += [(g, sid, "wh") for g in set(wh.split())]
            c.executemany("INSERT INTO sgram(gram,sid,col) VALUES(?,?,?)", rows)

    def _index_delete(self, c, sids):
        sids = list(sids)
        for i in range(0, len(sids), 500):
            chunk = sids[i:i + 500]
            q = ",".join("?" * len(chunk))
            if self.use_fts:
                c.execute("DELETE FROM sfts WHERE rowid IN (%s)" % q, chunk)
            else:
                c.execute("DELETE FROM sgram WHERE sid IN (%s)" % q, chunk)

    def set_description(self, fid, desc):
        with self.conn() as c:
            f = c.execute("SELECT * FROM frames WHERE id=?", (fid,)).fetchone()
            if f is None:
                return 0
            old = [r[0] for r in c.execute("SELECT id FROM sightings WHERE frame_id=?", (fid,))]
            self._index_delete(c, old)
            c.execute("DELETE FROM sightings WHERE frame_id=?", (fid,))
            for o in desc.get("objects", []):
                cur = c.execute("INSERT INTO sightings(frame_id,name,where_text,conf,ts,camera) VALUES(?,?,?,?,?,?)",
                                (fid, o["name"], o.get("where", ""), o.get("confidence", 0.5), f["ts"], f["camera"]))
                self._index_add(c, cur.lastrowid, o["name"], o.get("where", ""))
            c.execute("UPDATE frames SET status='described', scene=? WHERE id=?", (desc.get("scene", ""), fid))
            return len(desc.get("objects", []))

    def search(self, terms, col):
        """terms 중 하나라도(각 term 의 bigram 이 모두) col('nm'|'wh')에 있는 sighting id 집합."""
        found = set()
        with self.conn() as c:
            for t in terms:
                g = synonyms.grams(t)
                if not g:
                    continue
                if self.use_fts:
                    q = '%s : "%s"' % (col, " ".join(g))
                    try:
                        found.update(r[0] for r in c.execute("SELECT rowid FROM sfts WHERE sfts MATCH ?", (q,)))
                    except sqlite3.OperationalError:
                        pass
                else:
                    ug = sorted(set(g))
                    q = ",".join("?" * len(ug))
                    found.update(r[0] for r in c.execute(
                        "SELECT sid FROM sgram WHERE col=? AND gram IN (%s) GROUP BY sid HAVING COUNT(DISTINCT gram)=?" % q,
                        [col] + ug + [len(ug)]))
        return found

    def sightings(self, ids):
        ids = list(ids)
        out = []
        with self.conn() as c:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                out += c.execute(
                    "SELECT s.*, f.path AS path, f.id AS fid, f.scene AS scene FROM sightings s "
                    "JOIN frames f ON f.id=s.frame_id WHERE s.id IN (%s)" % ",".join("?" * len(chunk)), chunk).fetchall()
        return out

    # ------------------------------------------------------------ 보관 기간
    def photos_older_than(self, cutoff):
        with self.conn() as c:
            return c.execute("SELECT id, path FROM frames WHERE ts<? AND path IS NOT NULL", (cutoff,)).fetchall()

    def clear_photo(self, fid):
        with self.conn() as c:
            c.execute("UPDATE frames SET path=NULL WHERE id=?", (fid,))

    def delete_descriptions_older_than(self, cutoff):
        """cutoff 이전 프레임 행·sighting·색인·캐시를 지우고 (프레임 수, 남은 파일 경로 목록) 반환."""
        with self.conn() as c:
            frames = c.execute("SELECT id, sha256, path FROM frames WHERE ts<?", (cutoff,)).fetchall()
            ids = [f["id"] for f in frames]
            sids = [r[0] for r in c.execute("SELECT id FROM sightings WHERE ts<?", (cutoff,))]
            self._index_delete(c, sids)
            c.execute("DELETE FROM sightings WHERE ts<?", (cutoff,))
            c.execute("DELETE FROM frames WHERE ts<?", (cutoff,))
            c.execute("DELETE FROM desc_cache WHERE ts<?", (cutoff,))
            return len(ids), [f["path"] for f in frames if f["path"]]

    def stats(self):
        with self.conn() as c:
            return {
                "frames": c.execute("SELECT COUNT(*) FROM frames").fetchone()[0],
                "pending": c.execute("SELECT COUNT(*) FROM frames WHERE status!='described'").fetchone()[0],
                "sightings": c.execute("SELECT COUNT(*) FROM sightings").fetchone()[0],
                "fts5": self.use_fts,
            }
