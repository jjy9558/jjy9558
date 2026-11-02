-- voice-journal/vjournal/store.py 의 SCHEMA 를 그대로 가져온 것

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

-- 어제(2026-11-01): 'once' 선언 마감(미달), 'day' 선언(달성), 퇴근 3문답, 표시해 둔 메모
INSERT INTO goals(id,created_ts,created_day,title,keywords,period,target,unit,start_day,deadline,status) VALUES
 (1,'2026-10-30 21:00:00','2026-10-30','블로그 1개','["블로그"]','once',1,'개',NULL,'2026-11-01','open'),
 (2,'2026-10-25 21:00:00','2026-10-25','운동 하기','["운동"]','day',1,'회',NULL,NULL,'open'),
 (3,'2026-10-30 21:00:00','2026-10-30','세금 서류 제출','["세금"]','once',1,NULL,NULL,'2026-11-02','open'),
 (4,'2026-10-20 21:00:00','2026-10-20','옛 선언','["옛"]','once',1,NULL,NULL,'2026-10-25','open');
INSERT INTO goal_events(goal_id,ts,day,amount) VALUES (2,'2026-11-01 19:00:00','2026-11-01',1);
INSERT INTO entries(id,ts,day,text,mood,kind,qkey,flagged) VALUES
 (1,'2026-11-01 18:00:00','2026-11-01','블로그 초안 마무리하기',NULL,'checkout','next',0),
 (2,'2026-11-01 18:01:00','2026-11-01','배포 스크립트가 계속 실패함',NULL,'checkout','blocked',0),
 (3,'2026-11-01 20:00:00','2026-11-01','#중요 다음 주까지 건강검진 예약하기',NULL,'diary',NULL,1);
