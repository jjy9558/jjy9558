-- paper-butler/paperbutler/storage.py 의 SCHEMA 를 그대로 가져온 것

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

-- 고지서: 가스(모레), 전기(오늘, 자동이체), 이미 처리한 것, 기한이 먼 것
INSERT INTO documents(id,sha256,added_at,doc_type,subtype,issuer,amount,due_date,auto_debit,done) VALUES
 (1,'a1','2026-10-20 10:00:00','bill','가스','서울도시가스',32400,'2026-11-04',0,0),
 (2,'a2','2026-10-20 10:00:00','bill','전기','한국전력',45200,'2026-11-02',1,0),
 (3,'a3','2026-10-20 10:00:00','bill','통신','통신사',55000,'2026-11-03',0,1),
 (4,'a4','2026-10-20 10:00:00','bill','수도','수도사업소',12000,'2026-12-30',0,0);
INSERT INTO refunds(id,doc_id,merchant,amount,purchase_date,policy_days,deadline,source,done) VALUES
 (1,5,'쿠팡',23000,'2026-10-27',7,'2026-11-03','policy',0),
 (2,6,'옥션',9000,'2026-10-01',7,'2026-10-08','policy',0);
