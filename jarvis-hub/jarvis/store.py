"""sqlite 작업 큐 + 기기 레지스트리 + 이벤트 로그.

연결 하나를 잠금(Condition) 하나로 보호한다. 허브 한 대(미니PC)에서 도는 v0.1에는 충분하고,
롱폴링(lease 대기, 이벤트 대기)은 같은 Condition 위에서 기다린다.
"""
import json
import sqlite3
import threading
import time

STATUSES = ("waiting_approval", "queued", "leased", "done", "failed", "cancelled")
ACTIVE = ("waiting_approval", "queued", "leased")
FINAL = ("done", "failed", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    caps TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_timeout REAL NOT NULL DEFAULT 300,
    lease_owner TEXT,
    lease_expires REAL,
    available_at REAL NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    created_by TEXT NOT NULL,
    notify INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_pick ON jobs(status, priority DESC, id);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    caps TEXT NOT NULL DEFAULT '[]',
    types TEXT NOT NULL DEFAULT '[]',
    info TEXT NOT NULL DEFAULT '{}',
    last_seen REAL,
    online INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


class StoreError(Exception):
    code = 400


class NotFound(StoreError):
    code = 404


class Conflict(StoreError):
    code = 409


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _job_row(r):
    if r is None:
        return None
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    d["caps"] = json.loads(d["caps"])
    d["result"] = json.loads(d["result"]) if d["result"] is not None else None
    d["notify"] = bool(d["notify"])
    return d


def caps_match(job_caps, worker_caps):
    return set(job_caps) <= set(worker_caps)


class Store:
    def __init__(self, path=":memory:", clock=time.time, keep_events=1000):
        self.clock = clock
        self.keep_events = keep_events
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self.cond = threading.Condition(threading.RLock())

    def close(self):
        with self.cond:
            self._db.close()

    def __del__(self):
        try:
            self._db.close()
        except Exception:
            pass

    # ---- 내부 ----
    def _q(self, sql, args=()):
        return self._db.execute(sql, args)

    def _get(self, job_id):
        return _job_row(self._q("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone())

    def _require(self, job_id):
        job = self._get(job_id)
        if job is None:
            raise NotFound("작업 #%s 없음" % job_id)
        return job

    # ---- 작업 ----
    def submit(self, type, payload, caps=(), priority=0, created_by="hub", max_attempts=3,
               lease_timeout=300, notify=False, title="", needs_approval=False):
        now = self.clock()
        status = "waiting_approval" if needs_approval else "queued"
        with self.cond:
            cur = self._q(
                "INSERT INTO jobs(type,title,payload,caps,priority,status,max_attempts,lease_timeout,"
                "created_by,notify,created_at,updated_at,available_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (type, title or "", _dumps(payload), _dumps(sorted(set(caps))), int(priority), status,
                 int(max_attempts), float(lease_timeout), created_by, int(bool(notify)), now, now, 0))
            job = self._get(cur.lastrowid)
            self.cond.notify_all()
            return job

    def get(self, job_id):
        with self.cond:
            return self._require(job_id)

    def list(self, status=None, limit=50, since_id=0):
        sql, args = "SELECT * FROM jobs WHERE id>?", [int(since_id)]
        if status:
            statuses = [status] if isinstance(status, str) else list(status)
            sql += " AND status IN (%s)" % ",".join("?" * len(statuses))
            args += statuses
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self.cond:
            return [_job_row(r) for r in self._q(sql, args).fetchall()]

    def counts(self, exclude_types=()):
        ex = list(exclude_types)
        sql = "SELECT status, COUNT(*) n FROM jobs"
        if ex:
            sql += " WHERE type NOT IN (%s)" % ",".join("?" * len(ex))
        with self.cond:
            rows = self._q(sql + " GROUP BY status", ex).fetchall()
        out = {s: 0 for s in STATUSES}
        out.update({r["status"]: r["n"] for r in rows})
        return out

    def count_active(self):
        with self.cond:
            return self._q("SELECT COUNT(*) FROM jobs WHERE status IN (?,?,?)", ACTIVE).fetchone()[0]

    def recent_failures(self, seconds):
        with self.cond:
            return self._q("SELECT COUNT(*) FROM jobs WHERE status='failed' AND finished_at>=?",
                           (self.clock() - seconds,)).fetchone()[0]

    def lease(self, device, caps, types):
        """조건에 맞는 다음 작업 하나를 빌린다 (우선순위 높은 것 먼저, 같으면 먼저 들어온 것)."""
        now = self.clock()
        types = set(types or ())
        with self.cond:
            rows = self._q("SELECT * FROM jobs WHERE status='queued' AND available_at<=? "
                           "ORDER BY priority DESC, id ASC LIMIT 500", (now,)).fetchall()
            for r in rows:
                job = _job_row(r)
                if job["type"] not in types or not caps_match(job["caps"], caps):
                    continue
                cur = self._q("UPDATE jobs SET status='leased', lease_owner=?, lease_expires=?, "
                              "attempts=attempts+1, updated_at=? WHERE id=? AND status='queued'",
                              (device, now + job["lease_timeout"], now, job["id"]))
                if cur.rowcount == 1:
                    return self._get(job["id"])
            return None

    def wait_lease(self, device, caps, types, wait):
        deadline = time.monotonic() + max(0.0, float(wait))
        with self.cond:
            while True:
                job = self.lease(device, caps, types)
                left = deadline - time.monotonic()
                if job or left <= 0:
                    return job
                # 재시도 대기(available_at)가 지나는 것도 잡도록 1초 단위로 깨어난다
                self.cond.wait(min(left, 1.0))

    def extend(self, device, job_ids):
        now = self.clock()
        done = []
        with self.cond:
            for jid in job_ids or ():
                cur = self._q("UPDATE jobs SET lease_expires=?+lease_timeout, updated_at=? "
                              "WHERE id=? AND status='leased' AND lease_owner=?",
                              (now, now, int(jid), device))
                if cur.rowcount:
                    done.append(int(jid))
        return done

    def _check_owner(self, job, device):
        if job["status"] != "leased" or job["lease_owner"] != device:
            raise Conflict("작업 #%s 은 이 기기가 빌린 상태가 아닙니다 (status=%s)" % (job["id"], job["status"]))

    def complete(self, job_id, device, result):
        now = self.clock()
        with self.cond:
            job = self._require(job_id)
            self._check_owner(job, device)
            self._q("UPDATE jobs SET status='done', result=?, error=NULL, lease_owner=NULL, lease_expires=NULL, "
                    "updated_at=?, finished_at=? WHERE id=?", (_dumps(result), now, now, job["id"]))
            self.cond.notify_all()
            return self._get(job["id"])

    def fail(self, job_id, device, error, retry=True, retry_delay=0):
        """retry이고 시도 횟수가 남았으면 다시 queued(지연 후), 아니면 failed. (job, final) 반환."""
        now = self.clock()
        with self.cond:
            job = self._require(job_id)
            self._check_owner(job, device)
            return self._fail_locked(job, str(error)[:2000], retry, retry_delay, now)

    def _fail_locked(self, job, error, retry, retry_delay, now):
        if retry and job["attempts"] < job["max_attempts"]:
            self._q("UPDATE jobs SET status='queued', error=?, lease_owner=NULL, lease_expires=NULL, "
                    "available_at=?, updated_at=? WHERE id=?", (error, now + retry_delay, now, job["id"]))
            final = False
        else:
            self._q("UPDATE jobs SET status='failed', error=?, lease_owner=NULL, lease_expires=NULL, "
                    "updated_at=?, finished_at=? WHERE id=?", (error, now, now, job["id"]))
            final = True
        self.cond.notify_all()
        return self._get(job["id"]), final

    def reap(self, retry_delay=0):
        """임대 시간이 지난 작업: 시도가 남았으면 다시 큐로, 아니면 failed. [(job, final)]"""
        now = self.clock()
        out = []
        with self.cond:
            rows = self._q("SELECT * FROM jobs WHERE status='leased' AND lease_expires<?", (now,)).fetchall()
            for r in rows:
                job = _job_row(r)
                out.append(self._fail_locked(job, "임대 시간 초과 (워커 %s 응답 없음)" % job["lease_owner"],
                                             True, retry_delay, now))
        return out

    def set_status(self, job_id, frm, to, error=None):
        now = self.clock()
        with self.cond:
            job = self._require(job_id)
            if job["status"] not in frm:
                raise Conflict("작업 #%s 상태가 %s 라서 %s 로 바꿀 수 없습니다" % (job_id, job["status"], to))
            fin = now if to in FINAL else None
            self._q("UPDATE jobs SET status=?, error=COALESCE(?, error), lease_owner=NULL, lease_expires=NULL, "
                    "updated_at=?, finished_at=? WHERE id=?", (to, error, now, fin, job["id"]))
            self.cond.notify_all()
            return self._get(job["id"])

    def expire_queued(self, type, older_than):
        """오래 대기한 type 작업을 cancelled로 (처리할 워커가 없는 알림 등). 바뀐 id 목록."""
        now = self.clock()
        with self.cond:
            ids = [r["id"] for r in self._q("SELECT id FROM jobs WHERE type=? AND status='queued' AND created_at<?",
                                            (type, now - older_than)).fetchall()]
            for i in ids:
                self._q("UPDATE jobs SET status='cancelled', error='만료 (처리할 워커 없음)', updated_at=?, "
                        "finished_at=? WHERE id=?", (now, now, i))
            return ids

    def has_active(self, type):
        with self.cond:
            return self._q("SELECT COUNT(*) FROM jobs WHERE type=? AND status IN ('queued','leased')",
                           (type,)).fetchone()[0] > 0

    # ---- 기기 ----
    def touch(self, device, caps=None, types=None, info=None):
        """기기가 살아 있음을 기록. 오프라인 -> 온라인으로 바뀌었으면 True."""
        now = self.clock()
        with self.cond:
            row = self._q("SELECT * FROM devices WHERE id=?", (device,)).fetchone()
            if row is None:
                self._q("INSERT INTO devices(id,caps,types,info,last_seen,online) VALUES(?,?,?,?,?,1)",
                        (device, _dumps(sorted(set(caps or ()))), _dumps(sorted(set(types or ()))),
                         _dumps(info or {}), now))
                return True
            sets, args = ["last_seen=?", "online=1"], [now]
            if caps is not None:
                sets.append("caps=?")
                args.append(_dumps(sorted(set(caps))))
            if types is not None:
                sets.append("types=?")
                args.append(_dumps(sorted(set(types))))
            if info is not None:
                sets.append("info=?")
                args.append(_dumps(info))
            self._q("UPDATE devices SET %s WHERE id=?" % ",".join(sets), args + [device])
            return not row["online"]

    def devices(self):
        with self.cond:
            rows = self._q("SELECT * FROM devices ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["caps"] = json.loads(d["caps"])
            d["types"] = json.loads(d["types"])
            d["info"] = json.loads(d["info"])
            d["online"] = bool(d["online"])
            out.append(d)
        return out

    def mark_offline(self, offline_after):
        """last_seen이 offline_after초보다 오래된 온라인 기기를 오프라인으로. 바뀐 id 목록."""
        cutoff = self.clock() - offline_after
        with self.cond:
            rows = self._q("SELECT id FROM devices WHERE online=1 AND last_seen<?", (cutoff,)).fetchall()
            ids = [r["id"] for r in rows]
            for i in ids:
                self._q("UPDATE devices SET online=0 WHERE id=?", (i,))
            return ids

    # ---- 이벤트 ----
    def add_event(self, kind, text="", **data):
        with self.cond:
            cur = self._q("INSERT INTO events(ts,kind,text,data) VALUES(?,?,?,?)",
                          (self.clock(), kind, text or "", _dumps(data)))
            eid = cur.lastrowid
            if eid % 100 == 0:
                self._q("DELETE FROM events WHERE id<=?", (eid - self.keep_events,))
            self.cond.notify_all()
            return {"id": eid, "kind": kind, "text": text or "", "data": data}

    def events_since(self, last_id, wait=0.0, limit=100):
        deadline = time.monotonic() + max(0.0, float(wait))
        with self.cond:
            while True:
                rows = self._q("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?",
                               (int(last_id), int(limit))).fetchall()
                left = deadline - time.monotonic()
                if rows or left <= 0:
                    return [dict(r, data=json.loads(r["data"])) for r in rows]
                self.cond.wait(left)

    def last_event_id(self):
        with self.cond:
            return self._q("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]

    def recent_events(self, limit=20):
        with self.cond:
            rows = self._q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r, data=json.loads(r["data"])) for r in rows]

    # ---- meta ----
    def meta_get(self, k, default=None):
        with self.cond:
            r = self._q("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def meta_set(self, k, v):
        with self.cond:
            self._q("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, _dumps(v)))
