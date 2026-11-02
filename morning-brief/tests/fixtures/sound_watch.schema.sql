-- sound-watch/soundwatch/store.py 의 SCHEMA 를 그대로 가져온 것 (이벤트 행은 테스트가 시각 계산해서 넣는다)

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
