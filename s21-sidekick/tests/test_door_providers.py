"""현관 출발 provider 3개 (objmem_items / soundwatch_door / paperbutler_returns).
sqlite 픽스처는 원본 프로젝트의 스키마 텍스트를 그대로 옮겼다 (sound-watch/soundwatch/store.py, paper-butler/paperbutler/storage.py)."""
import datetime as dt
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from helpers import FakeClock, FakeMono, config, free_port
from sidekick import door_providers as dp
from sidekick.departure import Departure
from sidekick.server import Sidekick

NOW = dt.datetime(2026, 11, 2, 8, 0)  # 월요일 아침
FAKE_TOKEN = "t-" + "x" * 12  # 런타임에서 만든 가짜 토큰

SOUNDWATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  start REAL NOT NULL, end REAL NOT NULL, duration REAL NOT NULL,
  peak_dbfs REAL NOT NULL, leq_dbfs REAL NOT NULL,
  kind TEXT NOT NULL DEFAULT '', n_segments INTEGER NOT NULL DEFAULT 1,
  clip TEXT
);
CREATE TABLE IF NOT EXISTS detections(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_det_ts ON detections(ts);
"""

PAPERBUTLER_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256 TEXT UNIQUE NOT NULL,
  added_at TEXT NOT NULL,
  merchant TEXT, amount INTEGER, due_date TEXT, done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS refunds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER REFERENCES documents(id),
  merchant TEXT, amount INTEGER,
  purchase_date TEXT, policy_days INTEGER, deadline TEXT,
  source TEXT, done INTEGER DEFAULT 0, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_refund_deadline ON refunds(deadline);
"""


def ts(h, m, day=2):
    return dt.datetime(2026, 11, day, h, m).timestamp()


def sighting(name, where, t, match=1.0):
    return {"name": name, "where": where, "ts": t, "match": match, "confidence": 0.9, "camera": "방",
            "when": "", "age": "", "photo": None, "frame_id": 1, "score": 1.0}


class FakeObjMem:
    """object-memory GET /api/ask 의 응답 모양을 흉내 내는 가짜 서버 (포트 0)."""

    def __init__(self, table, token=FAKE_TOKEN, status=200):
        self.table, self.token, self.status = table, token, status
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlparse(self.path)
                outer.requests.append((self.command, u.path, parse_qs(u.query), self.headers.get("X-ObjMem-Token")))
                if self.headers.get("X-ObjMem-Token") != outer.token:
                    return self._send(401, {"error": "unauthorized"})
                if outer.status != 200:
                    return self._send(outer.status, {"error": "x"})
                q = (parse_qs(u.query).get("q") or [""])[0]
                hits = [s for k, v in outer.table.items() if q.startswith(k) for s in v]
                self._send(200, {"question": q, "found": bool(hits), "sightings": hits, "answer": "x"})

            def _send(self, code, body):
                b = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class ObjmemTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeObjMem({
            "지갑": [sighting("지갑", "책상 위", ts(21, 40, day=1))],            # 어제 21:40 (10시간 전)
            "이어폰": [sighting("이어폰", "침대 위", ts(7, 30))],                # 오늘 07:30
            "열쇠": [sighting("열쇠", "현관", ts(21, 0, day=1) - 2 * 86400)],  # 3일 전 -> 모름
            "리모컨": [sighting("리모컨", "탁자", ts(7, 0), match=0.4)],          # 위치 설명에만 걸린 약한 일치
        })
        self.addCleanup(self.fake.close)
        self.opts = {"enabled": True, "url": self.fake.url, "token": FAKE_TOKEN, "items": ["지갑"]}

    def run_with(self, **kw):
        return dp.objmem_items(dict(self.opts, **kw), NOW)

    def test_fake_http_server_answer(self):
        self.assertEqual(self.run_with(), "물건 기억 - 지갑: 어제 21:40 책상 위에서 봤어요.")
        method, path, q, token = self.fake.requests[0]
        self.assertEqual((method, path, token), ("GET", "/api/ask", FAKE_TOKEN))
        self.assertEqual(q["q"], ["지갑 어디 있어"])
        self.assertEqual(q["llm"], ["0"])

    def test_today_sighting_phrase(self):
        self.assertEqual(self.run_with(items=["이어폰"]), "물건 기억 - 이어폰: 오늘 07:30 침대 위에서 봤어요.")

    def test_old_sighting_is_unknown(self):
        out = self.run_with(items=["지갑", "열쇠", "이어폰"])
        self.assertIn("열쇠: 모름, 24시간 넘게 못 봤어요", out)
        self.assertIn("지갑: 어제 21:40 책상 위에서 봤어요", out)
        self.assertNotIn("현관", out)  # 오래된 위치는 말하지 않는다 (거짓 안심 방지)
        self.assertIn("지갑: 모름", self.run_with(stale_hours=6))  # 기준 시간은 설정 가능

    def test_no_record_and_weak_match_are_unknown(self):
        out = self.run_with(items=["우산", "리모컨"])
        self.assertEqual(out, "물건 기억 - 우산: 모름, 본 기록이 없어요. 리모컨: 모름, 본 기록이 없어요.")

    def test_disabled_by_default_makes_no_request(self):
        self.assertIsNone(dp.objmem_items({"url": self.fake.url, "token": FAKE_TOKEN, "items": ["지갑"]}, NOW))
        self.assertEqual(self.fake.requests, [])

    def test_failures_are_silent(self):
        with self.assertLogs("sidekick.door", level="WARNING") as cm:
            self.assertIsNone(self.run_with(token="wrong-token-0000"))       # 401
        self.assertEqual(len(cm.output), 1)
        self.assertNotIn("wrong-token-0000", cm.output[0])
        self.fake.status = 500
        with self.assertLogs("sidekick.door", level="WARNING"):
            self.assertIsNone(self.run_with())
        with self.assertLogs("sidekick.door", level="WARNING"):               # 아무도 안 듣는 포트
            self.assertIsNone(self.run_with(url="http://127.0.0.1:%d" % free_port(), timeout=1))
        with self.assertLogs("sidekick.door", level="WARNING"):               # http 이외 스킴은 거부
            self.assertIsNone(self.run_with(url="file:///etc"))
        with self.assertLogs("sidekick.door", level="WARNING"):               # 토큰 없음
            self.assertIsNone(self.run_with(token="", token_env="NOPE_NOT_SET_ENV"))

    def test_injected_fetch_and_timeout_passed(self):
        seen = []

        def fetch(url, headers, timeout):
            seen.append((url, headers, timeout))
            return {"sightings": [sighting("지갑", "", ts(7, 50))]}
        out = dp.objmem_items({"enabled": True, "token": FAKE_TOKEN, "items": ["지갑"], "timeout": 2}, NOW, fetch=fetch)
        self.assertEqual(out, "물건 기억 - 지갑: 오늘 07:50 봤어요.")
        self.assertEqual(seen[0][2], 2)
        self.assertTrue(seen[0][0].startswith("http://127.0.0.1:8788/api/ask?"))
        self.assertEqual(seen[0][1]["X-ObjMem-Token"], FAKE_TOKEN)


class SqliteFixtureBase(unittest.TestCase):
    schema = ""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "source.sqlite"
        con = sqlite3.connect(str(self.path))
        con.executescript(self.schema)
        self.con = con
        self.addCleanup(con.close)  # tmp.cleanup 보다 먼저 실행됨 (Windows 파일 잠금)

    def finish(self):
        self.con.commit()
        self.con.close()
        self.snapshot = (self.path.read_bytes(), self.path.stat().st_mtime_ns, sorted(os.listdir(self.tmp.name)))

    def assert_untouched(self):
        now = (self.path.read_bytes(), self.path.stat().st_mtime_ns, sorted(os.listdir(self.tmp.name)))
        self.assertEqual(self.snapshot, now)


class SoundwatchTests(SqliteFixtureBase):
    schema = SOUNDWATCH_SCHEMA

    def add(self, t, kind, name):
        self.con.execute("INSERT INTO detections(ts,kind,name,score,message) VALUES(?,?,?,?,?)", (t, kind, name, 0, ""))

    def opts(self, **kw):
        return dict({"enabled": True, "db_path": str(self.path)}, **kw)

    def test_counts_knock_and_doorbell_in_window(self):
        self.add(ts(1, 0), "knock", "노크")                 # 7시간 전 -> 창 밖
        self.add(ts(3, 30, day=1), "knock", "노크")         # 창 밖
        self.add(ts(2, 10), "knock", "노크")                # 5시간 50분 전 -> 안
        self.add(ts(5, 0), "template", "초인종")            # 안
        self.add(ts(6, 20), "template", "현관 초인종 소리")  # 안 (이름에 포함)
        self.add(ts(6, 30), "beep", "삐 소리")               # 다른 종류 -> 제외
        self.add(ts(7, 0), "melody", "멜로디")
        self.finish()
        out = dp.soundwatch_door(self.opts(), NOW)
        self.assertEqual(out, "최근 6시간 안에 노크·초인종 소리가 3번 있었어요 (마지막 06:20). 문 앞 택배 있을 수도 있어요.")
        self.assert_untouched()

    def test_window_and_names_configurable(self):
        self.add(ts(7, 0), "template", "택배 알림음")
        self.add(ts(7, 10), "knock", "노크")
        self.finish()
        self.assertIn("2번", dp.soundwatch_door(self.opts(kinds=["knock"], names=["택배"], hours=1), NOW) or "")
        self.assertIn("1번", dp.soundwatch_door(self.opts(kinds=["zzz"], names=["택배"], hours=1), NOW) or "")
        self.assertIsNone(dp.soundwatch_door(self.opts(hours=0.1), NOW))

    def test_nothing_detected_is_silent(self):
        self.finish()
        self.assertIsNone(dp.soundwatch_door(self.opts(), NOW))

    def test_disabled_missing_and_broken_db(self):
        self.finish()
        self.assertIsNone(dp.soundwatch_door({"db_path": str(self.path)}, NOW))
        missing = str(Path(self.tmp.name) / "none.sqlite")
        with self.assertLogs("sidekick.door", level="WARNING"):
            self.assertIsNone(dp.soundwatch_door(self.opts(db_path=missing), NOW))
        self.assertFalse(Path(missing).exists())  # mode=ro: 없는 DB를 새로 만들지 않는다
        bad = Path(self.tmp.name) / "bad.sqlite"
        bad.write_bytes(b"not a database" * 50)
        with self.assertLogs("sidekick.door", level="WARNING"):
            self.assertIsNone(dp.soundwatch_door(self.opts(db_path=str(bad)), NOW))
        other = Path(self.tmp.name) / "empty.sqlite"
        sqlite3.connect(str(other)).close()
        with self.assertLogs("sidekick.door", level="WARNING"):  # 표가 없음
            self.assertIsNone(dp.soundwatch_door(self.opts(db_path=str(other)), NOW))

    def test_cannot_write_through_connection(self):
        self.finish()
        con = dp.open_ro(self.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO detections(ts,kind,name) VALUES(1,'k','n')")
        finally:
            con.close()
        self.assert_untouched()


class PaperbutlerTests(SqliteFixtureBase):
    schema = PAPERBUTLER_SCHEMA

    def add(self, merchant, deadline, done=0):
        self.con.execute("INSERT INTO refunds(doc_id,merchant,amount,purchase_date,policy_days,deadline,source,done)"
                         " VALUES(NULL,?,?,?,?,?,?,?)", (merchant, 39000, "2026-10-26", 7, deadline, "rule", done))

    def opts(self, **kw):
        return dict({"enabled": True, "db_path": str(self.path)}, **kw)

    def test_today_and_tomorrow(self):
        self.add("무신사", "2026-11-03")
        self.add("쿠팡", "2026-11-02")
        self.add("오래된상점", "2026-10-30")       # 지난 마감은 제외
        self.add("멀리있는상점", "2026-11-09")     # 범위 밖
        self.add("처리끝상점", "2026-11-03", done=1)
        self.finish()
        self.assertEqual(dp.paperbutler_returns(self.opts(), NOW), "반품 마감: 쿠팡 오늘, 무신사 내일.")
        self.assert_untouched()

    def test_days_range_and_cap(self):
        for i, m in enumerate(["가", "나", "다", "라", "마"]):
            self.add(m + "상점", "2026-11-04" if i else "2026-11-02")
        self.finish()
        out = dp.paperbutler_returns(self.opts(), NOW)
        self.assertEqual(out, "반품 마감: 가상점 오늘, 나상점 모레, 다상점 모레 외 2건.")
        self.assertEqual(dp.paperbutler_returns(self.opts(days=1), NOW), "반품 마감: 가상점 오늘.")

    def test_none_due_disabled_and_missing(self):
        self.add("멀리", "2026-12-25")
        self.finish()
        self.assertIsNone(dp.paperbutler_returns(self.opts(), NOW))
        self.assertIsNone(dp.paperbutler_returns({"db_path": str(self.path)}, NOW))
        with self.assertLogs("sidekick.door", level="WARNING"):
            self.assertIsNone(dp.paperbutler_returns(self.opts(db_path=str(self.path) + ".nope"), NOW))
        self.assertIsNone(dp.paperbutler_returns({"enabled": True, "db_path": ""}, NOW))

    def test_merchant_text_is_sanitized(self):
        self.add("무신사\n\x07  스토어" + "가" * 40, "2026-11-03")
        self.finish()
        out = dp.paperbutler_returns(self.opts(), NOW)
        self.assertNotIn("\n", out)
        self.assertLess(len(out), 60)


class DepartureIntegrationTests(SqliteFixtureBase):
    """세 provider를 실제 레지스트리로 묶어 출발 체크 전체를 돌린다."""
    schema = SOUNDWATCH_SCHEMA

    def make_dbs(self):
        self.con.execute("INSERT INTO detections(ts,kind,name,score,message) VALUES(?,?,?,?,?)",
                         (ts(6, 0), "knock", "노크", 0, ""))
        self.finish()
        pb = Path(self.tmp.name) / "pb.sqlite"
        con = sqlite3.connect(str(pb))
        con.executescript(PAPERBUTLER_SCHEMA)
        con.execute("INSERT INTO refunds(merchant,amount,deadline,done) VALUES('무신사',1,'2026-11-03',0)")
        con.commit()
        con.close()
        return pb

    def providers(self, pb, fake_url):
        return [{"name": "reminders", "items": ["지갑"]},
                {"name": "objmem_items", "enabled": True, "url": fake_url, "token": FAKE_TOKEN, "items": ["지갑"]},
                {"name": "soundwatch_door", "enabled": True, "db_path": str(self.path)},
                {"name": "paperbutler_returns", "enabled": True, "db_path": str(pb)}]

    def test_all_three_in_departure(self):
        pb = self.make_dbs()
        fake = FakeObjMem({"지갑": [sighting("지갑", "책상 위", ts(7, 40))]})
        self.addCleanup(fake.close)
        cfg = config(departure={"providers": self.providers(pb, fake.url)})
        d = Departure(cfg["departure"], clock=FakeClock(NOW), monotonic=FakeMono())
        r = d.leave("button")
        self.assertEqual(len(r["items"]), 4)
        self.assertIn("지갑: 오늘 07:40 책상 위에서 봤어요", r["items"][1])
        self.assertIn("노크·초인종 소리가 1번", r["items"][2])
        self.assertEqual(r["items"][3], "반품 마감: 무신사 내일.")

    def test_one_failing_provider_does_not_stop_others(self):
        pb = self.make_dbs()
        provs = self.providers(pb, "http://127.0.0.1:%d" % free_port())  # objmem 서버 없음
        provs[1]["timeout"] = 1
        cfg = config(departure={"providers": provs})
        d = Departure(cfg["departure"], clock=FakeClock(NOW), monotonic=FakeMono())
        with self.assertLogs("sidekick.door", level="WARNING"):
            r = d.leave("button")
        self.assertEqual(len(r["items"]), 3)
        self.assertIn("반품 마감", r["say"])
        self.assertIn("노크", r["say"])

    def test_provider_raising_is_skipped(self):
        reg = {"boom": lambda o, n: 1 / 0, "ok": lambda o, n: "정상 항목."}
        cfg = config(departure={"providers": [{"name": "boom"}, {"name": "ok"}]})
        r = Departure(cfg["departure"], clock=FakeClock(NOW), monotonic=FakeMono(), registry=reg).leave("button")
        self.assertEqual(r["items"], ["정상 항목."])

    def test_say_max_chars_cuts_lowest_priority_whole_items(self):
        reg = {"a": lambda o, n: "가" * 100 + ".", "b": lambda o, n: "나" * 100 + ".", "c": lambda o, n: "다" * 100 + "."}
        cfg = config(departure={"providers": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}, say={"max_chars": 300})
        d = Departure(cfg["departure"], clock=FakeClock(NOW), monotonic=FakeMono(), registry=reg, max_chars=300)
        r = d.leave("button")
        self.assertLessEqual(len(r["say"]), 300)
        self.assertEqual(len(r["items"]), 2)          # 앞선 provider가 우선
        self.assertTrue(r["items"][0].startswith("가") and r["items"][1].startswith("나"))
        self.assertEqual(r["dropped"], 1)
        self.assertTrue(r["say"].startswith("출발 체크할게요.") and r["say"].endswith("다녀오세요!"))
        # 한도 없으면 그대로
        full = Departure(cfg["departure"], clock=FakeClock(NOW), monotonic=FakeMono(), registry=reg).leave("button")
        self.assertEqual(len(full["items"]), 3)
        self.assertNotIn("dropped", full)

    def test_single_oversized_item_is_truncated(self):
        reg = {"a": lambda o, n: "가" * 500}
        cfg = config(departure={"providers": [{"name": "a"}]})
        r = Departure(cfg["departure"], clock=FakeClock(NOW), monotonic=FakeMono(), registry=reg, max_chars=300).leave("button")
        self.assertLessEqual(len(r["say"]), 300)
        self.assertEqual(len(r["items"]), 1)

    def test_sidekick_passes_say_max_chars(self):
        cfg = config(say={"max_chars": 120}, departure={"providers": [
            {"name": "reminders", "items": ["지갑"]}, {"name": "reminders", "items": ["가" * 100]}]})
        app = Sidekick(cfg, clock=FakeClock(NOW))
        r = app.departure.leave("button")
        self.assertLessEqual(len(r["say"]), 120)

    def test_defaults_include_disabled_providers_and_stay_offline(self):
        names = {p["name"]: p for p in config()["departure"]["providers"]}
        for n in ("objmem_items", "soundwatch_door", "paperbutler_returns"):
            self.assertFalse(names[n]["enabled"])
        self.finish()


if __name__ == "__main__":
    unittest.main()
