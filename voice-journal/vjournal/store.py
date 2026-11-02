# -*- coding: utf-8 -*-
"""sqlite 저장소 + 입력 시 자동 목표 처리."""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta

from . import goals as G
from .llm import llm_extract_goals
from .parse import FLAG_TAGS, parse_tags, validate_mood

TS_FMT = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, day TEXT NOT NULL, text TEXT NOT NULL,
  mood INTEGER, kind TEXT NOT NULL DEFAULT 'diary', qkey TEXT,
  flagged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_day ON entries(day);
CREATE TABLE IF NOT EXISTS entry_tags(entry_id INTEGER NOT NULL, tag TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON entry_tags(tag);
CREATE TABLE IF NOT EXISTS goals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts TEXT NOT NULL, created_day TEXT NOT NULL, entry_id INTEGER,
  title TEXT NOT NULL, keywords TEXT NOT NULL, period TEXT NOT NULL,
  target REAL, unit TEXT, start_day TEXT, deadline TEXT,
  status TEXT NOT NULL DEFAULT 'open', done_day TEXT, dropped_day TEXT, raw TEXT
);
CREATE TABLE IF NOT EXISTS goal_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL,
  ts TEXT NOT NULL, day TEXT NOT NULL, amount REAL NOT NULL, entry_id INTEGER, note TEXT
);
"""


def default_db_path():
    p = os.environ.get("VJOURNAL_DB")
    if p:
        return p
    return os.path.join(os.path.expanduser("~"), ".vjournal", "journal.db")


class Journal(object):
    def __init__(self, path=":memory:", now=None, llm=None):
        self.path = path
        if path != ":memory:":
            d = os.path.dirname(os.path.abspath(path))
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._now = now or datetime.now
        self.llm = llm

    def close(self):
        self.conn.close()

    def now(self):
        return self._now()

    def today(self):
        return self.now().date()

    # ---- entries ----
    def add_entry(self, text, mood=None, kind="diary", qkey=None, flag=False, ts=None,
                  auto_goals=True):
        text = (text or "").strip()
        if not text:
            raise ValueError("빈 일기는 저장하지 않습니다")
        mood = validate_mood(mood)
        ts = ts or self.now()
        if isinstance(ts, str):
            ts = datetime.strptime(ts, TS_FMT)
        tags = parse_tags(text)
        flagged = 1 if (flag or FLAG_TAGS.intersection(tags)) else 0
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO entries(ts,day,text,mood,kind,qkey,flagged) VALUES(?,?,?,?,?,?,?)",
                (ts.strftime(TS_FMT), ts.date().isoformat(), text, mood, kind, qkey, flagged))
            eid = cur.lastrowid
            self.conn.executemany("INSERT INTO entry_tags(entry_id,tag) VALUES(?,?)",
                                  [(eid, t) for t in tags])
        result = {"id": eid, "ts": ts.strftime(TS_FMT), "tags": tags, "mood": mood,
                  "kind": kind, "flagged": bool(flagged), "goals_created": [], "progress": []}
        if auto_goals and kind in ("diary", "checkout"):
            created, prog = self.process_goals(eid, text, ts)
            result["goals_created"] = created
            result["progress"] = prog
        return result

    def _row_entry(self, r):
        d = dict(r)
        d["tags"] = [x["tag"] for x in self.conn.execute(
            "SELECT tag FROM entry_tags WHERE entry_id=? ORDER BY rowid", (r["id"],))]
        d["flagged"] = bool(d["flagged"])
        return d

    def get_entry(self, eid):
        r = self.conn.execute("SELECT * FROM entries WHERE id=?", (eid,)).fetchone()
        return self._row_entry(r) if r else None

    def entries(self, start_day=None, end_day=None, kind=None, tag=None, limit=None, newest_first=False):
        q, a = "SELECT * FROM entries WHERE 1=1", []
        if start_day:
            q += " AND day>=?"
            a.append(_iso(start_day))
        if end_day:
            q += " AND day<=?"
            a.append(_iso(end_day))
        if kind:
            q += " AND kind=?"
            a.append(kind)
        if tag:
            q += " AND id IN (SELECT entry_id FROM entry_tags WHERE tag=?)"
            a.append(tag.lstrip("#").lower())
        q += " ORDER BY ts %s, id %s" % (("DESC", "DESC") if newest_first else ("ASC", "ASC"))
        if limit:
            q += " LIMIT %d" % int(limit)
        return [self._row_entry(r) for r in self.conn.execute(q, a)]

    def checkout_answers(self, day):
        out = {}
        for r in self.conn.execute(
                "SELECT qkey,text FROM entries WHERE kind='checkout' AND day=? ORDER BY ts,id", (_iso(day),)):
            out[r["qkey"]] = r["text"]
        return out

    def flagged(self, since_day=None):
        q, a = "SELECT * FROM entries WHERE flagged=1", []
        if since_day:
            q += " AND day>=?"
            a.append(_iso(since_day))
        q += " ORDER BY ts"
        return [self._row_entry(r) for r in self.conn.execute(q, a)]

    def set_flag(self, eid, value):
        with self.conn:
            cur = self.conn.execute("UPDATE entries SET flagged=? WHERE id=?", (1 if value else 0, eid))
        return cur.rowcount > 0

    # ---- goals ----
    def goals(self, status=None):
        q, a = "SELECT * FROM goals", []
        if status:
            q += " WHERE status=?"
            a.append(status)
        q += " ORDER BY id"
        return [self._row_goal(r) for r in self.conn.execute(q, a)]

    def get_goal(self, gid):
        r = self.conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
        return self._row_goal(r) if r else None

    @staticmethod
    def _row_goal(r):
        d = dict(r)
        d["keywords"] = json.loads(d["keywords"])
        if d["target"] is not None and d["target"] == int(d["target"]):
            d["target"] = int(d["target"])
        return d

    def goal_events(self, gid):
        return [(r["day"], r["amount"]) for r in self.conn.execute(
            "SELECT day,amount FROM goal_events WHERE goal_id=? ORDER BY ts,id", (gid,))]

    def add_goal(self, decl, entry_id=None, ts=None):
        ts = ts or self.now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO goals(created_ts,created_day,entry_id,title,keywords,period,target,unit,"
                "start_day,deadline,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ts.strftime(TS_FMT), ts.date().isoformat(), entry_id, decl.title,
                 json.dumps(decl.keywords, ensure_ascii=False), decl.period, decl.target, decl.unit,
                 decl.start.isoformat() if decl.start else None,
                 decl.deadline.isoformat() if decl.deadline else None, decl.raw))
        return cur.lastrowid

    def add_progress(self, gid, amount, ts=None, entry_id=None, note=None):
        g = self.get_goal(gid)
        if g is None:
            raise KeyError("목표 %s 없음" % gid)
        ts = ts or self.now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO goal_events(goal_id,ts,day,amount,entry_id,note) VALUES(?,?,?,?,?,?)",
                (gid, ts.strftime(TS_FMT), ts.date().isoformat(), amount, entry_id, note))
        if g["period"] == "once" and g["status"] == "open":
            need = g["target"] or 1
            total = 0
            for day, amt in self.goal_events(gid):
                total += amt
                if total >= need:
                    with self.conn:
                        self.conn.execute("UPDATE goals SET status='done', done_day=? WHERE id=?",
                                          (day, gid))
                    break

    def drop_goal(self, gid, ts=None):
        ts = ts or self.now()
        with self.conn:
            cur = self.conn.execute(
                "UPDATE goals SET status='dropped', dropped_day=? WHERE id=? AND status!='dropped'",
                (ts.date().isoformat(), gid))
        return cur.rowcount > 0

    def complete_goal(self, gid, ts=None):
        g = self.get_goal(gid)
        if g is None:
            raise KeyError("목표 %s 없음" % gid)
        ts = ts or self.now()
        if g["period"] == "once":
            with self.conn:
                self.conn.execute("UPDATE goals SET status='done', done_day=? WHERE id=?",
                                  (ts.date().isoformat(), gid))
        else:
            self.add_progress(gid, g["target"] or 1, ts)

    def process_goals(self, entry_id, text, ts):
        """선언 생성 + 기존 목표 진행/포기 갱신. (생성된 목표 리스트, 진행 리스트) 반환."""
        today = ts.date()
        decls = G.detect_declarations(text, today)
        if self.llm is not None:
            known = {tuple(d.keywords) for d in decls}
            for d in llm_extract_goals(self.llm, text, today):
                if tuple(d.keywords) not in known and not (set(d.keywords) & {k for d0 in decls for k in d0.keywords}):
                    decls.append(d)
        open_goals = self.goals("open")
        created = []
        for d in decls:
            dup = [g for g in open_goals if g["keywords"] == d.keywords and g["period"] == d.period
                   and g["deadline"] == (d.deadline.isoformat() if d.deadline else None)]
            if dup:
                continue
            gid = self.add_goal(d, entry_id, ts)
            created.append({"id": gid, "title": d.title, "period": d.period, "target": d.target,
                            "unit": d.unit, "deadline": d.deadline.isoformat() if d.deadline else None})
        exclude = G.declaration_sentences(text, today)
        progress = []
        for p in G.detect_progress(text, open_goals, today, exclude):
            if p.kind == "drop":
                if self.drop_goal(p.goal_id, ts):
                    progress.append({"goal_id": p.goal_id, "kind": "drop", "note": p.note})
            else:
                self.add_progress(p.goal_id, p.amount, ts, entry_id, p.note)
                progress.append({"goal_id": p.goal_id, "kind": "progress", "amount": p.amount,
                                 "note": p.note})
        return created, progress


def _iso(d):
    return d.isoformat() if isinstance(d, (date, datetime)) else str(d)
