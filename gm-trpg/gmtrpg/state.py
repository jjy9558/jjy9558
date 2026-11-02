"""sqlite 기반 지속 세계 상태."""
import json
import os
import re
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS locations(name TEXT PRIMARY KEY, description TEXT DEFAULT '', links TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS npcs(name TEXT PRIMARY KEY, traits TEXT DEFAULT '', relation TEXT DEFAULT '', location TEXT DEFAULT '', last_seen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS items(name TEXT PRIMARY KEY, qty INTEGER DEFAULT 1, description TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS quests(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, detail TEXT DEFAULT '', status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, turn INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS summaries(id INTEGER PRIMARY KEY AUTOINCREMENT, upto_turn INTEGER, text TEXT, kind TEXT DEFAULT 'rolling');
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, turn INTEGER, role TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS snapshots(turn INTEGER PRIMARY KEY, data TEXT);
"""
SNAP_TABLES = ["meta", "locations", "npcs", "items", "quests", "facts", "summaries"]
MAX_SNAPSHOTS = 50


def safe_name(name):
    n = re.sub(r"[\\/:*?\"<>|\s]+", "_", name.strip())
    n = n.strip("._")
    if not n:
        raise ValueError("세계 이름이 비어 있습니다.")
    return n


def world_path(directory, name):
    return os.path.join(directory, safe_name(name) + ".db")


class World:
    def __init__(self, path=":memory:"):
        self.path = path
        if path != ":memory:":
            d = os.path.dirname(os.path.abspath(path))
            os.makedirs(d, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        if self.get("hp") is None:
            self.init_defaults()

    # ---- meta ----
    def get(self, key, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if r is None else r["value"]

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))

    def init_defaults(self):
        for k, v in {"name": "이름 없는 세계", "setting": "", "tone": "모험적이고 가족과 함께 즐길 수 있는 분위기",
                     "rules": "", "player_name": "모험가", "hp": 20, "max_hp": 20,
                     "location": "시작 지점", "turn": 0, "summarized_upto": 0,
                     "stats": "{}"}.items():
            self.set(k, v)
        self.add_location("시작 지점")
        self.db.commit()

    @property
    def turn(self):
        return int(self.get("turn", 0))

    @property
    def hp(self):
        return int(self.get("hp", 0))

    @property
    def max_hp(self):
        return int(self.get("max_hp", 0))

    @property
    def location(self):
        return self.get("location", "")

    @property
    def stats(self):
        try:
            d = json.loads(self.get("stats", "{}"))
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}

    def set_bible(self, name=None, setting=None, tone=None, rules=None, player_name=None):
        for k, v in (("name", name), ("setting", setting), ("tone", tone),
                     ("rules", rules), ("player_name", player_name)):
            if v is not None:
                self.set(k, v)
        self.db.commit()

    # ---- locations ----
    def locations(self):
        return [{"name": r["name"], "description": r["description"],
                 "links": json.loads(r["links"] or "[]")}
                for r in self.db.execute("SELECT * FROM locations ORDER BY rowid")]

    def has_location(self, name):
        return self.db.execute("SELECT 1 FROM locations WHERE name=?", (name,)).fetchone() is not None

    def add_location(self, name, description="", links=()):
        if not self.has_location(name):
            self.db.execute("INSERT INTO locations(name,description,links) VALUES(?,?,'[]')", (name, description))
        elif description:
            self.db.execute("UPDATE locations SET description=? WHERE name=?", (description, name))
        for other in links:
            if other and other != name:
                self.link(name, other)

    def link(self, a, b):
        for x, y in ((a, b), (b, a)):
            if not self.has_location(x):
                self.db.execute("INSERT INTO locations(name) VALUES(?)", (x,))
            r = self.db.execute("SELECT links FROM locations WHERE name=?", (x,)).fetchone()
            ls = json.loads(r["links"] or "[]")
            if y not in ls:
                ls.append(y)
                self.db.execute("UPDATE locations SET links=? WHERE name=?", (json.dumps(ls, ensure_ascii=False), x))

    # ---- npcs ----
    def npcs(self, location=None):
        q, a = "SELECT * FROM npcs", ()
        if location is not None:
            q, a = q + " WHERE location=?", (location,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY rowid", a)]

    def get_npc(self, name):
        r = self.db.execute("SELECT * FROM npcs WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None

    def upsert_npc(self, name, traits=None, relation=None, location=None):
        cur = self.get_npc(name)
        turn = self.turn
        if cur is None:
            self.db.execute("INSERT INTO npcs(name,traits,relation,location,last_seen) VALUES(?,?,?,?,?)",
                            (name, traits or "", relation or "", location or "", turn))
        else:
            self.db.execute("UPDATE npcs SET traits=?,relation=?,location=?,last_seen=? WHERE name=?",
                            (cur["traits"] if traits is None else traits,
                             cur["relation"] if relation is None else relation,
                             cur["location"] if location is None else location, turn, name))

    # ---- items ----
    def items(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM items ORDER BY rowid")]

    def add_item(self, name, qty=1, description=""):
        r = self.db.execute("SELECT qty FROM items WHERE name=?", (name,)).fetchone()
        if r:
            self.db.execute("UPDATE items SET qty=qty+? WHERE name=?", (qty, name))
        else:
            self.db.execute("INSERT INTO items(name,qty,description) VALUES(?,?,?)", (name, qty, description))

    def remove_item(self, name, qty=1):
        """보유량 한도 내에서 제거. 제거했으면 True."""
        r = self.db.execute("SELECT qty FROM items WHERE name=?", (name,)).fetchone()
        if not r:
            return False
        if r["qty"] <= qty:
            self.db.execute("DELETE FROM items WHERE name=?", (name,))
        else:
            self.db.execute("UPDATE items SET qty=qty-? WHERE name=?", (qty, name))
        return True

    # ---- quests ----
    def quests(self, status=None):
        q, a = "SELECT * FROM quests", ()
        if status:
            q, a = q + " WHERE status=?", (status,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY id", a)]

    def add_quest(self, title, detail=""):
        if any(q["title"] == title and q["status"] == "open" for q in self.quests()):
            return False
        self.db.execute("INSERT INTO quests(title,detail,status) VALUES(?,?,'open')", (title, detail))
        return True

    def finish_quest(self, title):
        for q in self.quests("open"):
            if q["title"] == title or title in q["title"] or q["title"] in title:
                self.db.execute("UPDATE quests SET status='done' WHERE id=?", (q["id"],))
                return q["title"]
        return None

    # ---- facts / summaries / history ----
    def facts(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM facts ORDER BY id")]

    def add_fact(self, text):
        if any(f["text"] == text for f in self.facts()):
            return False
        self.db.execute("INSERT INTO facts(text,turn) VALUES(?,?)", (text, self.turn))
        return True

    def latest_summary(self):
        r = self.db.execute("SELECT * FROM summaries ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def add_summary(self, upto_turn, text, kind="rolling"):
        self.db.execute("INSERT INTO summaries(upto_turn,text,kind) VALUES(?,?,?)", (upto_turn, text, kind))

    def add_history(self, turn, role, content):
        self.db.execute("INSERT INTO history(turn,role,content) VALUES(?,?,?)", (turn, role, content))

    def history(self, after_turn=0, upto_turn=None):
        q, a = "SELECT * FROM history WHERE turn>?", [after_turn]
        if upto_turn is not None:
            q += " AND turn<=?"
            a.append(upto_turn)
        return [dict(r) for r in self.db.execute(q + " ORDER BY id", a)]

    # ---- snapshots / undo ----
    def _dump(self):
        out = {}
        for t in SNAP_TABLES:
            cur = self.db.execute("SELECT * FROM %s" % t)
            cols = [d[0] for d in cur.description]
            out[t] = {"cols": cols, "rows": [list(r) for r in cur.fetchall()]}
        return out

    def snapshot(self):
        """현재 상태를 '현재 turn' 키로 저장(턴 시작 직전에 호출)."""
        t = self.turn
        self.db.execute("INSERT OR REPLACE INTO snapshots(turn,data) VALUES(?,?)",
                        (t, json.dumps(self._dump(), ensure_ascii=False)))
        self.db.execute("DELETE FROM snapshots WHERE turn<=?", (t - MAX_SNAPSHOTS,))

    def can_undo(self):
        return self.turn > 0 and self.db.execute(
            "SELECT 1 FROM snapshots WHERE turn=?", (self.turn - 1,)).fetchone() is not None

    def undo(self):
        """마지막 턴을 되돌린다. 되돌린 턴 번호 또는 None."""
        cur_turn = self.turn
        if cur_turn <= 0:
            return None
        r = self.db.execute("SELECT data FROM snapshots WHERE turn=?", (cur_turn - 1,)).fetchone()
        if not r:
            return None
        data = json.loads(r["data"])
        for t, d in data.items():
            self.db.execute("DELETE FROM %s" % t)
            cols = d["cols"]
            ph = ",".join("?" * len(cols))
            self.db.executemany("INSERT INTO %s(%s) VALUES(%s)" % (t, ",".join(cols), ph), d["rows"])
        self.db.execute("DELETE FROM history WHERE turn>?", (cur_turn - 1,))
        self.db.execute("DELETE FROM snapshots WHERE turn>=?", (cur_turn - 1,))
        self.db.commit()
        return cur_turn

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()
