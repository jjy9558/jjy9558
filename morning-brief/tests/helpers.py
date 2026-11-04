"""테스트 공통: 임시 '세상'(7개 어댑터 픽스처 + Claude 로그)을 만들고, 가짜 opener 를 제공한다."""
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent))

from mbrief.config import load_config  # noqa: E402
from mbrief.model import Context  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 2, 7, 30, tzinfo=KST)      # 월요일 아침
TOKEN = "x" * 6 + "-test"                                  # 진짜처럼 보이지 않는 가짜 값


class FakeResponse:
    def __init__(self, body=b"", status=200):
        self._body = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """URL 부분 문자열 -> 응답 dict. 모든 요청을 기록한다."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        for key, reply in self.routes.items():
            if key in req.full_url:
                if isinstance(reply, BaseException):
                    raise reply
                return FakeResponse(reply)
        return FakeResponse({})

    def urls(self):
        return [r.full_url for r in self.requests]


def run_sql(db_path: Path, *scripts: str) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        for s in scripts:
            con.executescript(s)
        con.commit()
    finally:
        con.close()


def epoch(y, m, d, hh, mm) -> float:
    return dt.datetime(y, m, d, hh, mm, tzinfo=KST).timestamp()


def jarvis_status() -> dict:
    now = NOW.timestamp()
    return {
        "version": "0", "now": now,
        "counts": {"waiting_approval": 1, "queued": 0, "leased": 0, "done": 2, "failed": 1, "cancelled": 0},
        "devices": [{"id": "main", "label": "5080", "worker": True, "online": False},
                    {"id": "minipc", "label": "미니PC", "worker": True, "online": True}],
        "jobs": [
            {"id": 5, "type": "claude.task", "status": "waiting_approval", "title": "리팩터링 작업", "finished_at": None},
            {"id": 4, "type": "claude.task", "status": "failed", "title": "빌드 점검", "finished_at": now - 3600},
            {"id": 3, "type": "llm.chat", "status": "done", "title": "요약", "finished_at": now - 7200},
            {"id": 2, "type": "claude.task", "status": "done", "title": "문서 정리", "finished_at": now - 9000},
            {"id": 1, "type": "claude.task", "status": "done", "title": "오래전 작업", "finished_at": now - 99999},
            {"id": 6, "type": "notify.say", "status": "done", "title": "알림 낭독", "finished_at": now - 100},
        ],
        "events": [],
    }


class World:
    """임시 폴더에 어댑터별 픽스처를 깔고, 그것을 가리키는 설정을 만든다."""

    def __init__(self, root: Path):
        self.root = root
        self.out = root / "out"
        self.paper = root / "paperbutler.db"
        self.journal = root / "journal.db"
        self.state = root / "state.json"
        self.cooloff = root / "data.json"
        self.community = root / "community"
        self.sw = root / "sw"
        self.claude = root / "claude"
        self._build()

    def _build(self):
        sql = lambda n: (FIX / n).read_text(encoding="utf-8")  # noqa: E731
        run_sql(self.paper, sql("paper_butler.sql"))
        run_sql(self.journal, sql("voice_journal.sql"))
        shutil.copy(FIX / "game_calendar_state.json", self.state)
        shutil.copy(FIX / "cool_off_data.json", self.cooloff)
        shutil.copytree(FIX / "community", self.community)
        self.sw.mkdir()
        (self.sw / "config.json").write_text(json.dumps(
            {"utc_offset_minutes": 540, "night": {"start": "22:00", "end": "06:00"},
             "calibration": {"offset_db": -6.0, "calibrated": True}}), encoding="utf-8")
        run_sql(self.sw / "soundwatch.db", sql("sound_watch.schema.sql"))
        con = sqlite3.connect(str(self.sw / "soundwatch.db"))
        try:
            ev = [(epoch(2026, 11, 2, 2, 10), -40.0, "impact"), (epoch(2026, 11, 2, 3, 30), -50.0, "sustained"),
                  (epoch(2026, 11, 2, 1, 0), -55.0, "impact"), (epoch(2026, 11, 1, 20, 0), -20.0, "impact")]
            con.executemany("INSERT INTO events(start,end,duration,peak_dbfs,leq_dbfs,kind) VALUES(?,?,?,?,?,?)",
                            [(s, s + 2, 2.0, p, p - 5, k) for s, p, k in ev])
            dets = [(epoch(2026, 11, 2, 0, 30), "knock", "노크"), (epoch(2026, 11, 2, 0, 31), "knock", "노크"),
                    (epoch(2026, 11, 2, 5, 0), "template", "초인종"), (epoch(2026, 11, 2, 4, 0), "beep", "삐 소리")]
            con.executemany("INSERT INTO detections(ts,kind,name,score,message) VALUES(?,?,?,?,?)",
                            [(t, k, n, 0.9, "") for t, k, n in dets])
            con.commit()
        finally:
            con.close()
        proj = self.claude / "proj"
        proj.mkdir(parents=True)
        for f in (FIX / "claude").iterdir():
            shutil.copy(f, proj / f.name)
            os.utime(proj / f.name, (NOW.timestamp(), NOW.timestamp()))

    def config(self, **over) -> dict:
        raw = {
            "out_dir": str(self.out),
            "timezone": "+09:00",          # 픽스처가 KST 기준이라 고정 (기본은 PC 로컬)
            "notify": {"sidekick": {"url": "http://127.0.0.1:8765", "token": TOKEN},
                       "ntfy": {"topic": "t-" + "z" * 8, "server": "https://ntfy.example"}},
            "sources": {
                "paper_butler": {"db": str(self.paper), "mode": "direct"},
                "game_calendar": {"state": str(self.state), "mode": "direct"},
                "cool_off": {"data": str(self.cooloff), "mode": "direct"},
                "voice_journal": {"db": str(self.journal), "mode": "direct"},
                "claude_night": {"projects_dir": str(self.claude)},
                "jarvis": {"device": "s21", "token": TOKEN},
                "sound_watch": {"data_dir": str(self.sw)},
                "community_briefing": {"dir": str(self.community)},
            },
        }
        for k, v in over.items():
            raw[k] = {**raw.get(k, {}), **v} if isinstance(v, dict) and isinstance(raw.get(k), dict) else v
        path = self.root / "mbrief.json"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        cfg, base = load_config(path)
        return cfg

    def ctx(self, **kw) -> Context:
        kw.setdefault("now", NOW)
        kw.setdefault("base_dir", self.root)
        kw.setdefault("env", {})
        return Context(**kw)


def pb_done(doc_id: int) -> dict:
    """paper_butler 어댑터가 내는 '완료 처리' action 객체."""
    return {"id": "paperbutler.done:%d" % doc_id, "label": "완료 처리",
            "text": "완료 처리: python -m paperbutler done %d" % doc_id}
