"""가짜 소스 5개 픽스처. 모든 값은 런타임에 만든 가짜 데이터."""
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

NOW = date(2026, 11, 2)
REAL_NAME = "김민수"          # 일기/묘지에 섞여 들어가는 가짜 실명
REAL_PLACE = "행복아파트"


def make_life_nagger(d: Path) -> Path:
    p = d / "counters.jsonl"
    rows = []
    last_dish = datetime(2026, 10, 24, 21, 0)       # 9일 전
    rows.append({"t": last_dish.isoformat(), "k": "dishes", "n": 1})
    for i in range(7):
        rows.append({"t": datetime(2026, 10, 28 + i // 3, 14 + i % 3, 5).isoformat(), "k": "sigh", "n": 1})
    rows.append({"t": datetime(2026, 10, 31, 15, 30).isoformat(), "k": "sigh", "n": 2})
    for day in range(1, 8):
        rows.append({"t": datetime(2026, 10, day, 7, 0).isoformat(), "k": "pushup", "n": 10})
    rows.append("깨진 줄")
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write((json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else r) + "\n")
    return p


def _task(i, title, skipped, status="buried", reason="skips", created="2026-09-01T09:00:00", **extra):
    t = {"id": i, "title": title, "created": created, "effort": 15, "tags": [], "times_skipped": skipped,
         "last_suggested": None, "status": status, "done_at": None, "buried_at": "2026-10-20T09:00:00",
         "bury_reason": reason}
    t.update(extra)
    return t


def make_decision_pet(d: Path) -> Path:
    p = d / "data.json"
    state = {"version": 1, "next_id": 9, "tasks": [
        _task(1, "책상 서랍 정리", 6),
        _task(2, "영어 단어 외우기", 5, created="2026-08-01T09:00:00"),
        _task(3, "자전거 타이어 갈기", 2, created="2026-08-15T09:00:00"),
        _task(4, "블로그 글 쓰기", 0, reason="vjournal", created="2026-08-30T00:00:00",
              declared_day="2026-08-30", raw="이번 달엔 블로그 매일 쓴다"),
        _task(5, "살아있는 할 일", 0, status="todo", reason=None),
    ], "decisions": [], "config": {}}
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return p


def make_cool_off(d: Path) -> Path:
    p = d / "data.json"
    state = {"version": 1, "next_id": 20, "wishes": [
        {"id": 1, "name": "게이밍 의자", "price": 250000, "status": "skipped"},
        {"id": 2, "name": "모니터 암", "price": 60000, "status": "skipped"},
    ], "purchases": [
        {"id": 3, "name": "블루투스 키보드", "price": 189000, "date": "2026-08-01", "satisfaction": 2, "usage": "가끔 씀"},
        {"id": 4, "name": "요가 매트", "price": 35000, "date": "2026-09-01", "satisfaction": 4, "usage": "거의 안 씀"},
        {"id": 5, "name": "머그컵", "price": 9000, "date": "2026-09-10", "satisfaction": 5, "usage": "자주 씀"},
    ], "subscriptions": [], "notified": {}}
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return p


def make_kakao(d: Path, anonymized=True) -> Path:
    p = d / "wrapped.json"
    stats = {"meta": {"year": 2026, "title": None, "anonymized": anonymized, "top": 5}, "empty": False,
             "members": [{"name": "멤버1", "messages": 900, "top_words": [[REAL_NAME, 33]]}],
             "totals": {"messages": 1500, "members": 4, "active_days": 120},
             "busiest_day": {"date": "2026-03-14", "count": 321, "weekday": 5, "top_name": "멤버1", "top_count": 90},
             "hours": [0] * 22 + [200, 100], "peak_hour": 22,
             "streak": {"days": 12, "start": "2026-02-01", "end": "2026-02-12"},
             "quote_of_year": {"name": "멤버2", "text": "비밀 원문 문장 XYZZY"},
             "first_message": {"name": "멤버1", "text": "원문 인용 XYZZY"}}
    p.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    return p


def make_voice_journal(d: Path) -> Path:
    p = d / "journal.db"
    conn = sqlite3.connect(p)
    conn.executescript("""CREATE TABLE entries(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL,
        text TEXT NOT NULL, mood INTEGER, kind TEXT NOT NULL DEFAULT 'diary', qkey TEXT, flagged INTEGER NOT NULL DEFAULT 0);""")
    rows = [("2026-10-01 22:00:00", "2026-10-01", "%s 씨랑 %s 앞에서 밥 먹고 오늘은 일찍 잘 거다. 연락은 010-1234-5678 로." % (REAL_NAME, REAL_PLACE)),
            ("2026-10-02 22:00:00", "2026-10-02", "비밀 일기: 아무도 지정하지 않은 항목 PRIVATEZZ"),
            ("2026-10-03 22:00:00", "2026-10-03", "오늘 병원에서 우울증 진단을 받았다."),
            ("2026-10-04 22:00:00", "2026-10-04", "상사가 사기꾼 같아서 퇴근하고 한참 걸었다.")]
    for ts, day, text in rows:
        conn.execute("INSERT INTO entries(ts, day, text) VALUES (?,?,?)", (ts, day, text))
    conn.commit()
    conn.close()
    return p


def make_all(d: Path, journal_ids=(1,)):
    from storyfactory.seeds import Sources
    d = Path(d)
    dirs = {}
    for n in ("nagger", "pet", "cool", "kakao", "vj"):
        (d / n).mkdir(exist_ok=True)
        dirs[n] = d / n
    return Sources(make_life_nagger(dirs["nagger"]), make_decision_pet(dirs["pet"]), make_cool_off(dirs["cool"]),
                   make_kakao(dirs["kakao"]), make_voice_journal(dirs["vj"]), list(journal_ids))


# ------------------------------------------------------------ 가짜 네트워크
import io
import urllib.error


class FakeResponse:
    def __init__(self, body, status=200):
        self._b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.status = status

    def read(self):
        return self._b

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, BaseException):
            raise r
        return FakeResponse(r)

    def last_json(self):
        return json.loads(self.requests[-1].data.decode("utf-8"))


def chat_reply(obj):
    """LLM 서버 응답 흉내: obj 를 JSON 문자열로 content 에 담는다."""
    content = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return {"choices": [{"message": {"content": content}}]}


def tree_state(root: Path) -> dict:
    """폴더 안 모든 파일의 (바이트, mtime_ns) 스냅샷 - 읽기 전용 검증용."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (p.read_bytes(), st.st_mtime_ns)
    return out
