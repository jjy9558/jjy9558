"""voice-journal 의 sqlite 를 읽기 전용으로 열어 '선언했다가 포기한' 목표를 찾는다.

스키마(voice-journal/vjournal/store.py) 의 goals 테이블:
  id, created_day, title, period(once|day|week|...), target, deadline(YYYY-MM-DD|NULL),
  status(open|done|dropped), done_day, dropped_day, raw
goal_events(goal_id, amount) 는 진행 기록.

포기로 보는 조건:
  - status='dropped' (명시적으로 접음)
  - status='open' 이고 deadline 이 오늘보다 과거이며 목표를 못 채운 경우
    (once: 진행 합계 < target(없으면 1), day/week: 진행 합계 < target(없으면 1))
"""
import sqlite3
from datetime import date
from pathlib import Path


def open_readonly(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def read_abandoned(db_path, today):
    """반환: [{id, title, raw, declared_day, reason}] (id 오름차순). DB 가 없거나 깨졌으면 ValueError."""
    if not Path(db_path).is_file():
        raise ValueError("voice-journal DB 를 찾을 수 없어요: %s" % db_path)
    today_s = today.isoformat() if isinstance(today, date) else str(today)
    try:
        conn = open_readonly(db_path)
    except sqlite3.Error as e:
        raise ValueError("DB 를 열 수 없어요: %s" % e)
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT g.id, g.created_day, g.title, g.period, g.target, g.deadline, g.status, g.raw,"
                " COALESCE((SELECT SUM(amount) FROM goal_events e WHERE e.goal_id=g.id),0) AS progress"
                " FROM goals g WHERE g.status IN ('dropped','open') ORDER BY g.id").fetchall()
        except sqlite3.Error as e:
            raise ValueError("voice-journal 형식이 아니에요: %s" % e)
    finally:
        conn.close()
    out = []
    for r in rows:
        if r["status"] == "dropped":
            reason = "dropped"
        elif r["deadline"] and r["deadline"] < today_s and r["progress"] < (r["target"] or 1):
            reason = "expired"
        else:
            continue
        out.append({"id": r["id"], "title": r["title"], "raw": (r["raw"] or r["title"]).strip(),
                    "declared_day": r["created_day"], "reason": reason})
    return out
