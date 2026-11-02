"""sqlite 저장소: 문서, 결제 원장(중복 제거/취소 매칭), 환불 기한, 인박스 기록."""
from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256 TEXT UNIQUE NOT NULL,
  added_at TEXT NOT NULL,
  orig_name TEXT,
  stored_path TEXT,
  method TEXT,
  doc_type TEXT, subtype TEXT, issuer TEXT, merchant TEXT,
  amount INTEGER,
  due_date TEXT, event_dt TEXT, doc_date TEXT, billing_month TEXT,
  customer_no TEXT,
  auto_debit INTEGER DEFAULT 0, auto_debit_day INTEGER,
  installment INTEGER DEFAULT 0, is_cancel INTEGER DEFAULT 0,
  summary TEXT, todo TEXT, verdict TEXT, reasons TEXT, retention_years INTEGER,
  confidence REAL,
  done INTEGER DEFAULT 0,
  text TEXT,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_docs_due ON documents(due_date);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER REFERENCES documents(id),
  ts TEXT NOT NULL,
  time_known INTEGER DEFAULT 1,
  merchant TEXT, merchant_norm TEXT,
  amount INTEGER NOT NULL,
  kind TEXT NOT NULL,             -- approve | cancel
  installment INTEGER DEFAULT 0,
  category TEXT, card TEXT,
  status TEXT NOT NULL,           -- active | canceled (approve) / cancel
  matched_to INTEGER              -- cancel -> approve id
);
CREATE INDEX IF NOT EXISTS idx_pay_ts ON payments(ts);
CREATE TABLE IF NOT EXISTS refunds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER REFERENCES documents(id),
  merchant TEXT, amount INTEGER,
  purchase_date TEXT, policy_days INTEGER, deadline TEXT,
  source TEXT, done INTEGER DEFAULT 0, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_refund_deadline ON refunds(deadline);
CREATE TABLE IF NOT EXISTS inbox_seen (
  path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, sha256 TEXT, seen_at TEXT
);
"""


def norm_merchant(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("(주)", "").replace("주식회사", "")
    return re.sub(r"[\s()\[\]\-_.,/·*&]+", "", s)


def merchant_match(a: str, b: str) -> bool:
    """정규화한 가맹점 이름이 같거나, 한쪽이 다른 쪽에 포함(지점명 차이)되면 같은 가맹점."""
    if not a and not b:
        return True
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and short in long_


def parse_ts(ts: str) -> dt.datetime:
    ts = ts.strip()
    if len(ts) <= 10:
        return dt.datetime.strptime(ts, "%Y-%m-%d")
    return dt.datetime.strptime(ts[:16], "%Y-%m-%d %H:%M")


def _near(a: dt.datetime, a_known: bool, b: dt.datetime, b_known: bool, minutes: int) -> bool:
    if a_known and b_known:
        return abs((a - b).total_seconds()) <= minutes * 60
    return a.date() == b.date()


@dataclass
class PaymentResult:
    status: str                 # inserted | duplicate | canceled_match | unmatched_cancel
    payment_id: Optional[int] = None
    matched_id: Optional[int] = None


class Database:
    def __init__(self, path: os.PathLike):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        new = not self.path.exists()
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        if new:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
        self.conn.commit()
        self._depth = 0

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self.conn.rollback()
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self.conn.commit()

    def _commit(self) -> None:
        if self._depth == 0:
            self.conn.commit()

    # -- documents ----------------------------------------------------------
    def find_doc_by_sha(self, sha: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM documents WHERE sha256=?", (sha,)).fetchone()

    def insert_document(self, **f: Any) -> int:
        cols = ", ".join(f)
        qs = ", ".join("?" for _ in f)
        cur = self.conn.execute("INSERT INTO documents (%s) VALUES (%s)" % (cols, qs), tuple(f.values()))
        self._commit()
        return int(cur.lastrowid)

    def update_document(self, doc_id: int, **f: Any) -> None:
        if not f:
            return
        sets = ", ".join("%s=?" % k for k in f)
        self.conn.execute("UPDATE documents SET %s WHERE id=?" % sets, tuple(f.values()) + (doc_id,))
        self._commit()

    def get_document(self, doc_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def count_documents(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def list_documents(self, limit: int = 1000) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM documents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def docs_due_between(self, start: str, end: str, include_done: bool = False) -> List[sqlite3.Row]:
        q = "SELECT * FROM documents WHERE due_date IS NOT NULL AND due_date BETWEEN ? AND ?"
        if not include_done:
            q += " AND done=0"
        q += " ORDER BY due_date, id"
        return self.conn.execute(q, (start, end)).fetchall()

    def mark_done(self, doc_id: int, done: bool = True) -> bool:
        cur = self.conn.execute("UPDATE documents SET done=? WHERE id=?", (1 if done else 0, doc_id))
        self.conn.execute("UPDATE refunds SET done=? WHERE doc_id=?", (1 if done else 0, doc_id))
        self._commit()
        return cur.rowcount > 0

    def search(self, query: str, limit: int = 20) -> List[sqlite3.Row]:
        q = unicodedata.normalize("NFKC", query).strip()
        terms = q.split()
        if not terms:
            return []
        where, params = [], []
        for term in terms:
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(" + " OR ".join(
                "%s LIKE ? ESCAPE '\\'" % c
                for c in ("text", "issuer", "merchant", "summary", "orig_name", "subtype", "doc_type")) + ")")
            params.extend([like] * 7)
        sql = "SELECT * FROM documents WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT ?"
        return self.conn.execute(sql, params + [limit]).fetchall()

    # -- inbox ---------------------------------------------------------------
    def seen_get(self, path: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM inbox_seen WHERE path=?", (path,)).fetchone()

    def seen_set(self, path: str, size: int, mtime_ns: int, sha: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO inbox_seen(path,size,mtime_ns,sha256,seen_at) VALUES (?,?,?,?,?)",
            (path, size, mtime_ns, sha, dt.datetime.now().isoformat(timespec="seconds")))
        self._commit()

    # -- payments ------------------------------------------------------------
    def add_payment(self, doc_id: Optional[int], ts: str, time_known: bool, merchant: str,
                    amount: int, is_cancel: bool, installment: int = 0, category: str = "",
                    card: str = "", dedupe_minutes: int = 10) -> PaymentResult:
        mnorm = norm_merchant(merchant)
        when = parse_ts(ts)
        tk = bool(time_known)
        tol = max(dedupe_minutes, 0)

        def rows(where: str, *args: Any) -> List[sqlite3.Row]:
            return self.conn.execute("SELECT * FROM payments WHERE " + where, args).fetchall()

        if not is_cancel:
            # 1) 같은 가맹점+금액이 N분 안에 이미 있으면 중복
            if dedupe_minutes > 0:
                for r in rows("kind='approve' AND status='active' AND amount=?", amount):
                    if merchant_match(mnorm, r["merchant_norm"]) and _near(
                            when, tk, parse_ts(r["ts"]), bool(r["time_known"]), dedupe_minutes):
                        return PaymentResult("duplicate", r["id"])
            cur = self.conn.execute(
                "INSERT INTO payments(doc_id,ts,time_known,merchant,merchant_norm,amount,kind,installment,category,card,status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, ts, int(tk), merchant, mnorm, amount, "approve", installment, category, card, "active"))
            pid = int(cur.lastrowid)
            # 2) 먼저 들어온 취소(매칭 전)가 있으면 연결
            pend = [r for r in rows("kind='cancel' AND matched_to IS NULL AND amount=?", amount)
                    if merchant_match(mnorm, r["merchant_norm"])
                    and self._cancel_after(parse_ts(r["ts"]), bool(r["time_known"]), when, tk, tol)]
            pend.sort(key=lambda r: (r["ts"], r["id"]))
            if pend:
                self.conn.execute("UPDATE payments SET matched_to=? WHERE id=?", (pid, pend[0]["id"]))
                self.conn.execute("UPDATE payments SET status='canceled' WHERE id=?", (pid,))
                self._commit()
                return PaymentResult("canceled_match", pid, pend[0]["id"])
            self._commit()
            return PaymentResult("inserted", pid)

        # ---- 취소 ----
        if dedupe_minutes > 0:
            for r in rows("kind='cancel' AND amount=?", amount):
                if merchant_match(mnorm, r["merchant_norm"]) and _near(
                        when, tk, parse_ts(r["ts"]), bool(r["time_known"]), dedupe_minutes):
                    return PaymentResult("duplicate", r["id"])
        cands = [r for r in rows("kind='approve' AND status='active' AND amount=?", amount)
                 if merchant_match(mnorm, r["merchant_norm"])
                 and self._cancel_after(when, tk, parse_ts(r["ts"]), bool(r["time_known"]), tol)]
        cands.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
        matched = cands[0]["id"] if cands else None
        cur = self.conn.execute(
            "INSERT INTO payments(doc_id,ts,time_known,merchant,merchant_norm,amount,kind,installment,category,card,status,matched_to)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, ts, int(tk), merchant, mnorm, amount, "cancel", installment, category, card, "cancel", matched))
        pid = int(cur.lastrowid)
        if matched is not None:
            self.conn.execute("UPDATE payments SET status='canceled' WHERE id=?", (matched,))
            self._commit()
            return PaymentResult("canceled_match", pid, matched)
        self._commit()
        return PaymentResult("unmatched_cancel", pid)

    @staticmethod
    def _cancel_after(cancel_ts: dt.datetime, c_known: bool, approve_ts: dt.datetime,
                      a_known: bool, tol_minutes: int) -> bool:
        """취소 시각이 승인 시각 이후(허용오차 포함)인가."""
        if c_known and a_known:
            return cancel_ts >= approve_ts - dt.timedelta(minutes=tol_minutes)
        return cancel_ts.date() >= approve_ts.date()

    def payments_for_month(self, month: Optional[str]) -> List[sqlite3.Row]:
        if month:
            return self.conn.execute(
                "SELECT * FROM payments WHERE substr(ts,1,7)=? ORDER BY ts, id", (month,)).fetchall()
        return self.conn.execute("SELECT * FROM payments ORDER BY ts, id").fetchall()

    # -- refunds -------------------------------------------------------------
    def add_refund(self, doc_id: int, merchant: str, amount: Optional[int], purchase_date: str,
                   policy_days: int, deadline: str, source: str, note: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO refunds(doc_id,merchant,amount,purchase_date,policy_days,deadline,source,note)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, merchant, amount, purchase_date, policy_days, deadline, source, note))
        self._commit()
        return int(cur.lastrowid)

    def refunds_between(self, start: str, end: str, include_done: bool = False) -> List[sqlite3.Row]:
        q = "SELECT * FROM refunds WHERE deadline BETWEEN ? AND ?"
        if not include_done:
            q += " AND done=0"
        return self.conn.execute(q + " ORDER BY deadline, id", (start, end)).fetchall()

    def refunds_for_doc(self, doc_id: int) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM refunds WHERE doc_id=?", (doc_id,)).fetchall()
