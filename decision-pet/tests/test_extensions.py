import json
import os
import random
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from decision_pet import council, lunch
from decision_pet.cli import run
from decision_pet.core import Pet, epitaph
from decision_pet.storage import Store

NOW = datetime(2026, 11, 20, 12, 0, 0)
TODAY = NOW.date()

# voice-journal/vjournal/store.py 의 SCHEMA 와 동일
VJ_SCHEMA = """
CREATE TABLE entries(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL,
  text TEXT NOT NULL, mood INTEGER, kind TEXT NOT NULL DEFAULT 'diary', qkey TEXT, flagged INTEGER NOT NULL DEFAULT 0);
CREATE TABLE goals(id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts TEXT NOT NULL, created_day TEXT NOT NULL,
  entry_id INTEGER, title TEXT NOT NULL, keywords TEXT NOT NULL, period TEXT NOT NULL, target REAL, unit TEXT,
  start_day TEXT, deadline TEXT, status TEXT NOT NULL DEFAULT 'open', done_day TEXT, dropped_day TEXT, raw TEXT);
CREATE TABLE goal_events(id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, ts TEXT NOT NULL,
  day TEXT NOT NULL, amount REAL NOT NULL, entry_id INTEGER, note TEXT);
"""

# paper-butler/paperbutler/storage.py payments 테이블 (필요 컬럼 포함)
PB_SCHEMA = """
CREATE TABLE payments(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, ts TEXT NOT NULL,
  time_known INTEGER DEFAULT 1, merchant TEXT, merchant_norm TEXT, amount INTEGER NOT NULL,
  kind TEXT NOT NULL, installment INTEGER DEFAULT 0, category TEXT, card TEXT, status TEXT NOT NULL, matched_to INTEGER);
"""


def make_goal(conn, title, created, deadline=None, status="open", period="once", target=None,
              raw=None, dropped_day=None, progress=0):
    cur = conn.execute(
        "INSERT INTO goals(created_ts,created_day,title,keywords,period,target,deadline,status,dropped_day,raw)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (created + " 09:00:00", created, title, "[]", period, target, deadline, status, dropped_day, raw))
    if progress:
        conn.execute("INSERT INTO goal_events(goal_id,ts,day,amount) VALUES(?,?,?,?)",
                     (cur.lastrowid, created + " 10:00:00", created, progress))
    return cur.lastrowid


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.pet = Pet(Store(self.dir / "pet"), rng=random.Random(1), clock=lambda: NOW)

    def run_cli(self, *argv, pet=None):
        lines = []
        code = run(list(argv), pet=pet or self.pet, out=lines.append, inp=lambda p="": "")
        return code, "\n".join(lines)


class VJournalImportTests(Base):
    def make_db(self):
        p = self.dir / "j.db"
        if p.exists():
            return str(p)
        c = sqlite3.connect(p)
        c.executescript(VJ_SCHEMA)
        make_goal(c, "블로그 글 쓰기", "2026-10-01", "2026-10-31", raw="이번 달 안에 블로그 글 쓰기로 했다")   # 1 기한 지남, 미달성
        make_goal(c, "운동 10회", "2026-10-01", "2026-10-31", period="week", target=10, progress=3, raw="운동 10회")  # 2 미달
        make_goal(c, "책 읽기", "2026-10-02", "2026-10-31", raw="책 읽기", progress=1)                   # 3 달성(target 없음→1)
        make_goal(c, "정리하기", "2026-10-03", "2026-10-31", status="done", raw="정리하기")             # 4 done
        make_goal(c, "영어 공부", "2026-10-04", None, status="dropped", dropped_day="2026-10-10", raw="영어 공부 시작")  # 5 dropped
        make_goal(c, "다음 달 목표", "2026-11-01", "2026-12-31", raw="다음 달 목표")                     # 6 진행 중
        c.commit()
        c.close()
        return str(p)

    def test_import_and_idempotent(self):
        db = self.make_db()
        added = self.pet.import_vjournal(db)
        self.assertEqual([t["source_key"] for t in added], ["vjournal:1", "vjournal:2", "vjournal:5"])
        self.assertTrue(all(t["status"] == "buried" for t in added))
        self.assertEqual(len(self.pet.import_vjournal(db)), 0)
        self.assertEqual(len(self.pet.graveyard()), 3)
        # 재시작 후에도 중복 없음
        pet2 = Pet(Store(self.dir / "pet"), clock=lambda: NOW)
        self.assertEqual(len(pet2.import_vjournal(db)), 0)

    def test_epitaph_format(self):
        self.pet.import_vjournal(self.make_db())
        e = epitaph(self.pet.graveyard()[0])
        self.assertEqual(e, "여기 잠들다: 선언했다가 포기 (2026-10-01, 이번 달 안에 블로그 글 쓰기로 했다)")

    def test_read_only_and_no_modification(self):
        db = self.make_db()
        before = Path(db).read_bytes()
        self.pet.import_vjournal(db)
        self.assertEqual(Path(db).read_bytes(), before)
        from decision_pet.vjournal_import import open_readonly
        conn = open_readonly(db)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM goals")
        conn.close()

    def test_resurrect_works_and_not_reburied(self):
        self.pet.import_vjournal(self.make_db())
        t = self.pet.graveyard()[0]
        self.pet.resurrect(t["id"])
        self.assertEqual(self.pet.get(t["id"])["status"], "todo")
        self.assertEqual(self.pet.sweep(), [])
        self.assertEqual(len(self.pet.import_vjournal(self.make_db())), 0)

    def test_cli(self):
        code, out = self.run_cli("import-vjournal", self.make_db())
        self.assertEqual(code, 0)
        self.assertIn("3개를 묘지에 묻었어요", out)
        _, out = self.run_cli("graveyard")
        self.assertIn("선언했다가 포기", out)

    def test_missing_and_bad_db(self):
        code, out = self.run_cli("import-vjournal", str(self.dir / "nope.db"))
        self.assertEqual(code, 1)
        self.assertIn("찾을 수 없어요", out)
        bad = self.dir / "bad.db"
        sqlite3.connect(bad).close()
        code, out = self.run_cli("import-vjournal", str(bad))
        self.assertEqual(code, 1)
        self.assertIn("형식", out)


class CouncilBase(Base):
    def setUp(self):
        super().setUp()
        d = self.dir
        # cool-off data.json
        (d / "cooloff.json").write_text(json.dumps({
            "version": 1, "next_id": 9, "subscriptions": [], "notified": {},
            "wishes": [
                {"id": 1, "name": "에어팟", "price": 359000, "status": "skipped", "cool_until": "2026-10-01T10:00:00"},
                {"id": 2, "name": "키보드", "price": 100000, "status": "skipped", "cool_until": "2026-10-01T10:00:00"},
                {"id": 3, "name": "모니터", "price": 300000, "status": "cooling", "cool_until": "2026-11-22T10:00:00"},
            ],
            "purchases": [
                {"id": 4, "name": "러닝머신", "price": 500000, "date": "2026-08-01", "satisfaction": 1, "usage": "거의 안 씀"},
                {"id": 5, "name": "책상", "price": 200000, "date": "2026-08-01", "satisfaction": 5, "usage": "매일"},
            ]}, ensure_ascii=False), encoding="utf-8")
        # steam-buddy data.json
        (d / "steam.json").write_text(json.dumps({"config": {}, "games": {
            "10": {"appid": 10, "name": "A", "price_paid": 20000, "minutes": 0},
            "11": {"appid": 11, "name": "B", "price_paid": 30000, "minutes": 0},
            "12": {"appid": 12, "name": "C", "price_paid": 10000, "minutes": 0},
            "13": {"appid": 13, "name": "D", "price_paid": 40000, "minutes": 600},
            "14": {"appid": 14, "name": "환불함", "price_paid": 1, "minutes": 0, "refunded": True},
        }}, ensure_ascii=False), encoding="utf-8")
        # life-nagger 상태 폴더
        (d / "nagger").mkdir()
        (d / "nagger" / "status.json").write_text(json.dumps(
            {"updated": "2026-11-20T03:00:00", "mombot": {"state": "warning", "stage": 2, "allow_sleep": False}}))
        (d / "nagger" / "work.json").write_text(json.dumps({"days": {
            "2026-11-18": {"work": 100.0, "game": 3600.0 * 30}, "2026-11-19": {"work": 0.0, "game": 3600.0 * 15},
            "2026-10-30": {"work": 0.0, "game": 3600.0 * 99}}, "last_tick": None}))
        # voice-journal
        c = sqlite3.connect(d / "j.db")
        c.executescript(VJ_SCHEMA)
        c.execute("INSERT INTO entries(ts,day,text) VALUES('2026-11-03 09:00:00','2026-11-03','오늘 날씨 좋다. 이번 달은 게임 안 산다 결심!')")
        c.execute("INSERT INTO entries(ts,day,text) VALUES('2026-10-03 09:00:00','2026-10-03','10월엔 충동구매 안 한다')")
        c.commit()
        c.close()
        # paper-butler
        c = sqlite3.connect(d / "pb.db")
        c.executescript(PB_SCHEMA)
        rows = [("2026-11-05 12:00:00", 400000, "approve", "active"), ("2026-11-06 12:00:00", 50000, "approve", "canceled"),
                ("2026-11-07 12:00:00", 50000, "cancel", "cancel"), ("2026-10-07 12:00:00", 900000, "approve", "active"),
                ("2026-11-09 12:00:00", 200000, "approve", "active")]
        c.executemany("INSERT INTO payments(ts,amount,kind,status) VALUES(?,?,?,?)", rows)
        c.commit()
        c.close()
        self.paths = {"cool_off": d / "cooloff.json", "steam_buddy": d / "steam.json", "life_nagger": d / "nagger",
                      "voice_journal": d / "j.db", "paper_butler": d / "pb.db"}

    def evidence(self):
        return council.collect(self.paths, TODAY)


class CouncilSourceTests(CouncilBase):
    def test_cool_off(self):
        ev = self.evidence()["cool_off"]
        self.assertEqual((ev["saved_count"], ev["saved_total"]), (2, 459000))
        self.assertEqual(ev["regrets"], ["러닝머신"])
        self.assertEqual([c["name"] for c in ev["cooling"]], ["모니터"])

    def test_steam(self):
        ev = self.evidence()["steam_buddy"]
        self.assertEqual(ev["unplayed"], ["A", "B", "C"])
        self.assertAlmostEqual(ev["per_hour"], 100000 / 10.0)

    def test_nagger_uses_only_this_month(self):
        ev = self.evidence()["life_nagger"]
        self.assertEqual(ev["stage"], 2)
        self.assertAlmostEqual(ev["game_hours"], 45.0)

    def test_journal_this_month_only(self):
        decl = self.evidence()["voice_journal"]["declarations"]
        self.assertEqual(len(decl), 1)
        self.assertIn("게임 안 산다", decl[0])

    def test_paper_counts_active_approvals_this_month(self):
        self.assertEqual(self.evidence()["paper_butler"]["month_total"], 600000)

    def test_missing_and_corrupt_sources_skipped_silently(self):
        bad = self.dir / "bad.json"
        bad.write_text("{not json")
        baddb = self.dir / "bad.db"
        baddb.write_bytes(b"garbage" * 100)
        paths = {"cool_off": bad, "steam_buddy": self.dir / "none.json", "life_nagger": self.dir / "nonedir",
                 "voice_journal": baddb, "paper_butler": self.dir / "none.db"}
        ev = council.collect(paths, TODAY)
        self.assertTrue(all(v is None for v in ev.values()))

    def test_sources_not_modified(self):
        before = {k: Path(p).read_bytes() for k, p in self.paths.items() if Path(p).is_file()}
        self.evidence()
        self.assertEqual(before, {k: Path(self.paths[k]).read_bytes() for k in before})

    def test_path_resolution_priority(self):
        env = {"HOME": "/h", "COOLOFF_HOME": "/c", "VJOURNAL_DB": "/v.db"}
        p = council.resolve_paths({"steam_buddy": "/cfg/s.json"}, {"cool_off": "/cli/c.json"}, env)
        self.assertEqual(p["cool_off"], Path("/cli/c.json"))
        self.assertEqual(p["steam_buddy"], Path("/cfg/s.json"))
        self.assertEqual(p["voice_journal"], Path("/v.db"))
        self.assertEqual(p["paper_butler"], Path("/h/.paperbutler/paperbutler.db"))
        self.assertEqual(council.resolve_paths({}, {}, {"HOME": "/h", "COOLOFF_HOME": "/c"})["cool_off"],
                         Path("/c/data.json"))


class CouncilVoteTests(CouncilBase):
    def convene(self, q, paths=None, cfg=None):
        if paths is not None:
            self.paths = paths
        return council.convene(q, self.evidence(), cfg)

    def test_game_rejected(self):
        r = self.convene("이 게임 살까?")
        stances = {m["key"]: m["stance"] for m in r["members"]}
        self.assertEqual(stances, {"cool_off": "반대", "steam_buddy": "반대", "life_nagger": "반대",
                                   "voice_journal": "반대", "paper_butler": "반대"})
        self.assertEqual((r["verdict"], r["yes"], r["no"]), ("부결", 0, 5))
        self.assertFalse(r["tie_broken"])
        self.assertIn("python -m cool_off want '게임' --price <가격>", r["suggestion"])
        text = "\n".join(council.render(r))
        self.assertIn("부결", text)
        self.assertIn("찬성 0 : 반대 5", text)

    def test_matching_cooling_item_named(self):
        r = self.convene("모니터 살까?")
        self.assertIn("'모니터'는 지금 쿨링오프 중", r["members"][0]["line"])
        self.assertIn("want '모니터' --price", r["suggestion"])

    def test_non_game_question_abstains_game_members(self):
        r = self.convene("치킨 먹을까?")
        st = {m["key"]: m["stance"] for m in r["members"]}
        self.assertEqual(st["steam_buddy"], "기권")
        self.assertEqual(st["life_nagger"], "기권")
        self.assertIsNone(r["suggestion"])  # 구매 질문 아님

    def test_pass_when_clean(self):
        # 후회/쿨링 없음, 안 켜본 게임 0, 게임 시간 적음, 선언 없음, 지출 적음 -> 가결
        d = self.dir
        (d / "c2.json").write_text(json.dumps({"wishes": [], "purchases": []}))
        (d / "s2.json").write_text(json.dumps({"games": {"1": {"name": "A", "price_paid": 100, "minutes": 600}}}))
        (d / "n2").mkdir()
        (d / "n2" / "work.json").write_text(json.dumps({"days": {"2026-11-01": {"game": 3600}}}))
        c = sqlite3.connect(d / "j2.db")
        c.executescript(VJ_SCHEMA)
        c.commit()
        c.close()
        c = sqlite3.connect(d / "pb2.db")
        c.executescript(PB_SCHEMA)
        c.commit()
        c.close()
        paths = {"cool_off": d / "c2.json", "steam_buddy": d / "s2.json", "life_nagger": d / "n2",
                 "voice_journal": d / "j2.db", "paper_butler": d / "pb2.db"}
        r = self.convene("이 게임 살까?", paths)
        self.assertEqual(r["verdict"], "가결")
        self.assertEqual((r["yes"], r["no"], r["abstain"]), (4, 0, 1))
        self.assertIsNone(r["suggestion"])

    def test_tie_goes_to_deterministic_chair(self):
        (self.dir / "only.json").write_text(json.dumps({"wishes": [], "purchases": []}))
        paths = {k: self.dir / "missing" for k in council.SOURCE_KEYS}
        paths["cool_off"] = self.dir / "only.json"  # 찬성 1 만
        r1 = self.convene("점심 뭐 먹지", paths)
        self.assertEqual((r1["yes"], r1["no"]), (1, 0))  # 동수 아님
        # 의원 0명 -> 동수 -> 의장
        none = {k: self.dir / "missing" for k in council.SOURCE_KEYS}
        a = self.convene("이 게임 살까?", none)
        b = self.convene("이 게임 살까?", none)
        self.assertTrue(a["tie_broken"])
        self.assertEqual(a["verdict"], b["verdict"])
        self.assertEqual(a["chair"]["stance"], council.chair_vote("이 게임 살까?"))
        self.assertIn("캐스팅보트", "\n".join(council.render(a)))
        # 서로 다른 질문이면 판결이 갈릴 수 있음(결정적이되 상수는 아님)
        outcomes = {council.chair_vote("질문%d" % i) for i in range(10)}
        self.assertEqual(outcomes, {"찬성", "반대"})

    def test_two_two_tie(self):
        ev = {"cool_off": {"saved_total": 0, "saved_count": 0, "regrets": ["x"], "cooling": []},
              "paper_butler": {"month_total": 0, "count": 0},
              "voice_journal": {"declarations": ["안 산다"]},
              "steam_buddy": {"unplayed": [], "per_hour": None, "total": 0, "hours": 0}}
        r = council.convene("이 게임 살까?", ev)
        self.assertEqual(r["chair"]["stance"], council.chair_vote("이 게임 살까?"))
        self.assertTrue(r["tie_broken"])
        self.assertEqual(r["yes"] + r["no"], 5)

    def test_item_extraction(self):
        self.assertEqual(council.item_from_question("이 게임 살까?"), "게임")
        self.assertEqual(council.item_from_question("에어팟 프로 사야 할까?"), "에어팟 프로")
        self.assertEqual(council.item_from_question("아이패드를 살까"), "아이패드")


class CouncilCliTests(CouncilBase):
    def args(self, *extra):
        return ["decide", "--mode", "council", "이 게임 살까?",
                "--cool-off", str(self.paths["cool_off"]), "--steam-buddy", str(self.paths["steam_buddy"]),
                "--life-nagger", str(self.paths["life_nagger"]), "--voice-journal", str(self.paths["voice_journal"]),
                "--paper-butler", str(self.paths["paper_butler"])] + list(extra)

    def test_cli_output_and_record(self):
        code, out = self.run_cli(*self.args())
        self.assertEqual(code, 0)
        self.assertIn("결정 의회", out)
        self.assertIn("안건은 부결! (찬성 0 : 반대 5", out)
        self.assertIn("python -m cool_off want", out)
        self.assertEqual(self.pet.state["decisions"][-1]["mode"], "council")
        self.assertEqual(self.pet.state["decisions"][-1]["choice"], "말자")
        # 제안만 하고 실행하지 않음: cool-off 데이터가 그대로
        self.assertEqual(len(json.loads(self.paths["cool_off"].read_text())["wishes"]), 3)

    def test_unquoted_multiword_question(self):
        code, out = self.run_cli("decide", "--mode", "council", "이", "게임", "살까?")
        self.assertEqual(code, 0)
        self.assertIn('안건: "이 게임 살까?"', out)

    def test_config_sources(self):
        self.pet.state["config"]["sources"]["cool_off"] = str(self.paths["cool_off"])
        _, out = self.run_cli("decide", "--mode", "council", "모니터 살까?",
                              "--steam-buddy", "/nonexistent")
        self.assertIn("'모니터'는 지금 쿨링오프 중", out)

    def test_llm_phrasing_and_fallback(self):
        class Resp:
            def __init__(self, content):
                self.b = json.dumps({"choices": [{"message": {"content": content}}]}).encode()

            def read(self):
                return self.b

        def good(req, timeout=0):
            return Resp(json.dumps(["하나", "둘", "셋", "넷", "다섯"], ensure_ascii=False))

        self.pet.opener = good
        _, out = self.run_cli(*self.args("--llm"))
        self.assertIn("[쿨링오프 의원] 하나 (반대)", out)
        self.assertIn("(반대)", out)

        def bad_len(req, timeout=0):
            return Resp('["하나"]')

        def boom(req, timeout=0):
            raise OSError("down")

        for op in (bad_len, boom):
            self.pet.opener = op
            _, out = self.run_cli(*self.args("--llm"))
            self.assertIn("쿨링오프 대기줄에 1개(모니터)", out)  # 템플릿 폴백


class LunchTests(Base):
    MENU = "김치찌개,라면,돈까스,비빔밥,국밥"

    def test_avoids_last_three(self):
        picks = []
        for _ in range(30):
            choice, excluded = self.pet.lunch(lunch.parse_menu(self.MENU))
            for prev in picks[-3:]:
                self.assertNotEqual(choice, prev)
            picks.append(choice)
        self.assertEqual(len(set(picks)), 5)

    def test_small_menu_still_works(self):
        for _ in range(5):
            self.pet.lunch(["a", "b"])
        a, _ = self.pet.lunch(["a", "b"])
        b, _ = self.pet.lunch(["a", "b"])
        self.assertNotEqual(a, b)
        c, _ = self.pet.lunch(["only"])
        self.assertEqual(c, "only")

    def test_empty_menu_error(self):
        code, out = self.run_cli("lunch", "--no-anim")
        self.assertEqual(code, 1)
        self.assertIn("--menu", out)

    def test_cli_no_anim_and_persist(self):
        code, out = self.run_cli("lunch", "--menu", self.MENU, "--no-anim")
        self.assertEqual(code, 0)
        self.assertIn("오늘의 점심 운명:", out)
        self.assertNotIn("█", out)
        code, out2 = self.run_cli("lunch", "--no-anim")  # 저장된 메뉴 재사용
        self.assertEqual(code, 0)
        self.assertIn("최근에 먹은", out2)
        pet2 = Pet(Store(self.dir / "pet"), clock=lambda: NOW)
        self.assertEqual(len(pet2.state["lunch"]["history"]), 2)

    def test_animation_frames(self):
        buf = []
        lunch.animate("라면", random.Random(3), buf.append, sleep=lambda s: None)
        text = "".join(buf)
        self.assertIn("█", text)
        self.assertIn("░", text)
        self.assertIn("100%", text)
        self.assertIn("99%", text)
        self.assertTrue(text.rstrip().endswith("오늘의 점심 운명: 라면"))
        pcts = [int(x) for x in __import__("re").findall(r"(\d+)%\.\.\.", text)]
        self.assertEqual(pcts, sorted(pcts))

    def test_bar(self):
        self.assertEqual(lunch.bar(50, 10), "█████░░░░░")


if __name__ == "__main__":
    unittest.main()
