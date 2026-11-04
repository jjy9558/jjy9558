"""JSON 계약(schema v1) 모드 테스트: 가짜 명령(임시 폴더의 작은 파이썬 스크립트)으로 json 모드, 되돌아가기, direct 모드를 확인하고
실제 형제 프로젝트의 --json 명령을 빈 데이터 폴더로 한 번씩 부른다(형제 폴더가 없으면 건너뜀)."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import KST, NOW, World
from mbrief import runner
from mbrief.sources import BY_NAME
from mbrief.sources._contract import BadConfig, ContractUnavailable
from mbrief.runner import source_cfg

REPO = Path(__file__).resolve().parents[2]
TODAY = NOW.date().isoformat()          # 2026-11-02

FAKE = '''\
import json, os, sys
spec = json.load(open(sys.argv[1], encoding="utf-8"))
args = sys.argv[2:]
if spec.get("log"):
    with open(spec["log"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "env": {k: os.environ.get(k) for k in spec.get("env_keys", [])}}) + "\\n")
beh = spec.get("behavior", "ok")
if beh == "fail":
    sys.stderr.write("boom\\n")
    sys.exit(3)
if beh == "garbage":
    print("not json {")
    sys.exit(0)
out = spec.get("out")
if "by_arg" in spec:
    out = next(v for k, v in spec["by_arg"].items() if k in args)
print(json.dumps(out, ensure_ascii=False))
'''

PAPER_DUE = {"version": 1, "generated_for": TODAY, "items": [
    {"kind": "refund", "title": "환불·반품 기한", "merchant": "쿠팡", "amount": 29900, "due": "2026-11-05",
     "days_left": 3, "auto_debit": False, "doc_id": 2},
    {"kind": "bill", "title": "가스요금", "merchant": "서울도시가스", "amount": 32400, "due": "2026-11-04",
     "days_left": 2, "auto_debit": False, "doc_id": 1}]}
PAPER_REFUNDS = {"version": 1, "generated_for": TODAY, "items": [PAPER_DUE["items"][0]]}


def game_item(kind, game, start, end, all_day, d_day, ends, ongoing, title="공지"):
    return {"kind": kind, "game": game, "appid": 1, "title": title, "start": start, "end": end, "all_day": all_day,
            "d_day": d_day, "ends_in_days": ends, "ongoing": ongoing, "url": "https://example.com/n/1"}


GAME = {"version": 1, "generated_for": TODAY, "items": [
    game_item("event", "이벤트게임", "2026-10-25T00:00:00+09:00", "2026-11-05T00:00:00+09:00", True, -8, 3, True),
    game_item("maintenance", "로스트아크", "2026-11-02T10:00:00+09:00", "2026-11-02T12:00:00+09:00", False, 0, 0, False),
    game_item("shutdown", "게임비", "2026-11-05T00:00:00+09:00", "2026-11-06T00:00:00+09:00", True, 3, 4, False),
    game_item("patch", "패치게임", "2026-11-02T00:00:00+09:00", None, True, 0, None, False)]}

JOURNAL = {"version": 1, "date": TODAY, "first_task": "PR 리뷰 먼저",
           "goals_yesterday": [{"id": 1, "text": "푸시업", "result": "partial", "progress": "12/30개"},
                               {"id": 2, "text": "접은 것", "result": "dropped", "progress": "접음"}],
           "goals_due_soon": [{"id": 4, "text": "발표 준비", "deadline": TODAY, "days_left": 0}],
           "flags": [{"id": 7, "text": "세금 서류 챙기기", "created": "2026-11-01T18:30:00"},
                     {"id": 8, "text": "너무 오래된 메모", "created": "2026-10-01T09:00:00"}]}

COOL = {"version": 1, "generated_for": TODAY,
        "cooling_ended": [{"id": "1", "name": "키보드", "price": 150000, "ended": "2026-11-01"}],
        "followups_due": [{"id": "3", "name": "이어폰", "bought": "2026-09-01"}],
        "renewals": [{"name": "넷플릭스", "amount": 13500, "date": "2026-11-03", "days_left": 1},
                     {"name": "먼 구독", "amount": 5000, "date": "2026-11-20", "days_left": 18}],
        "approved_not_bought": [{"id": "4", "name": "모니터", "price": 300000}]}

# 어댑터 -> (프로젝트, 패키지, 가짜 출력, 확인용 환경변수)
CASES = {
    "paper_butler": ("paper-butler", "paperbutler", {"by_arg": {"refunds": PAPER_REFUNDS, "due": PAPER_DUE}},
                     ["PAPERBUTLER_HOME"]),
    "game_calendar": ("game-calendar", "gamecal", {"out": GAME}, []),
    "voice_journal": ("voice-journal", "vjournal", {"out": JOURNAL}, ["VJOURNAL_DB"]),
    "cool_off": ("cool-off", "cool_off", {"out": COOL}, ["COOLOFF_HOME"]),
}


class JsonModeBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "world").mkdir()
        self.w = World(self.root / "world")
        self.cfg = self.w.config()
        self.script = self.root / "fake.py"
        self.script.write_text(FAKE, encoding="utf-8")
        self.log = self.root / "calls.jsonl"

    def fake(self, name, behavior="ok", out=None):
        """name 어댑터용 가짜 명령 설정을 만들어 (설정 조각, 호출 기록 경로)를 돌려준다."""
        _, _, spec, env_keys = CASES[name]
        spec = {**spec, "behavior": behavior, "log": str(self.log), "env_keys": env_keys}
        if out is not None:
            spec = {"out": out, "behavior": behavior, "log": str(self.log), "env_keys": env_keys}
        f = self.root / ("spec-%s.json" % name)
        f.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return {"command": [sys.executable, str(self.script), str(f)], "cwd": str(self.root)}

    def collect(self, name, mode="auto", ctx=None, **over):
        mod = BY_NAME[name]
        scfg = source_cfg(self.cfg, mod)
        scfg.update(over)
        scfg["mode"] = mode
        ctx = ctx or self.w.ctx()
        return mod.collect(scfg, ctx), ctx

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]


class JsonModeTests(JsonModeBase):
    def test_paper_butler_json(self):
        items, ctx = self.collect("paper_butler", **self.fake("paper_butler"))
        self.assertEqual(ctx.notes["paper_butler"], "모드: json")
        kinds = {i.kind: i for i in items}
        self.assertEqual(sorted(kinds), ["bill", "refund"])
        self.assertEqual(kinds["bill"].due, "2026-11-04")
        self.assertIn("서울도시가스 가스요금", kinds["bill"].title)
        self.assertIn("32,400원", kinds["bill"].title)
        self.assertEqual(kinds["bill"].actions, ("완료 처리: python -m paperbutler done 1",))
        self.assertIn("쿠팡", kinds["refund"].title)
        self.assertEqual(kinds["refund"].actions, ("완료 처리: python -m paperbutler done 2",))
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["args"][:2], ["--today", TODAY])
        self.assertIn("due", calls[0]["args"])
        self.assertIn("--json", calls[0]["args"])
        self.assertIn("refunds", calls[1]["args"])

    def test_paper_butler_db_becomes_home_env(self):
        self.collect("paper_butler", **self.fake("paper_butler"))
        self.assertEqual(Path(self.calls()[0]["env"]["PAPERBUTLER_HOME"]), self.w.paper.parent)

    def test_game_calendar_json_event_ends_in_two_days(self):
        items, ctx = self.collect("game_calendar", **self.fake("game_calendar"))
        self.assertEqual(ctx.notes["game_calendar"], "모드: json")
        self.assertEqual(sorted(i.kind for i in items), ["event", "maintenance", "shutdown"])     # patch 는 제외
        ev = next(i for i in items if i.kind == "event")
        self.assertIn("이틀 뒤 종료", ev.speak)             # 종일 일정: 배타적 end 11-05 -> 마지막 날 11-04
        self.assertEqual(ev.due, "2026-11-04")
        m = next(i for i in items if i.kind == "maintenance")
        self.assertEqual(m.due, "2026-11-02T10:00+09:00")
        self.assertIn("오전 10시", m.speak)
        s = next(i for i in items if i.kind == "shutdown")
        self.assertIn("3일 뒤", s.speak)
        args = self.calls()[0]["args"]
        self.assertEqual(args[:2], ["alerts", "--json"])
        self.assertIn("--days", args)

    def test_game_calendar_json_event_tomorrow_and_far(self):
        far = game_item("event", "먼이벤트", "2026-10-25T00:00:00+09:00", "2026-11-12T00:00:00+09:00", True, -8, 10, True)
        soon = game_item("event", "내일끝", "2026-10-25T00:00:00+09:00", "2026-11-04T00:00:00+09:00", True, -8, 2, True)
        out = {"version": 1, "generated_for": TODAY, "items": [far, soon]}
        items, _ = self.collect("game_calendar", **{**self.fake("game_calendar", out=out), "gamecal_config": "g.json"})
        self.assertEqual(len(items), 1)
        self.assertIn("내일 종료", items[0].speak)
        self.assertEqual(self.calls()[0]["args"][-2:], ["--config", str(self.root / "world" / "g.json")])

    def test_voice_journal_json(self):
        items, ctx = self.collect("voice_journal", **self.fake("voice_journal"))
        self.assertEqual(ctx.notes["voice_journal"], "모드: json")
        kinds = {i.kind: i for i in items}
        self.assertEqual(sorted(kinds), ["declaration", "first-task", "flag", "goal-due"])    # 접은 선언·오래된 메모 제외
        self.assertIn("(12/30개)", kinds["declaration"].detail)
        self.assertIn("PR 리뷰", kinds["first-task"].speak)
        self.assertGreater(kinds["first-task"].priority, 55)              # 어제 기록한 것으로 취급
        self.assertIn("세금", kinds["flag"].title + kinds["flag"].detail)
        self.assertEqual(kinds["goal-due"].due, TODAY)
        args = self.calls()[0]["args"]
        self.assertEqual(args, ["morning", "--json", "--date", TODAY])
        self.assertEqual(Path(self.calls()[0]["env"]["VJOURNAL_DB"]), self.w.journal)

    def test_cool_off_json(self):
        items, ctx = self.collect("cool_off", **self.fake("cool_off"))
        self.assertEqual(ctx.notes["cool_off"], "모드: json")
        kinds = {}
        for i in items:
            kinds.setdefault(i.kind, []).append(i)
        self.assertEqual(sorted(kinds), ["approved", "cooloff-ended", "followup", "subscription"])
        self.assertEqual(len(kinds["subscription"]), 1)                    # 3일 밖 구독은 renewal_days 로 걸러짐
        self.assertIn("넷플릭스", kinds["subscription"][0].title)
        self.assertEqual(kinds["followup"][0].due, "2026-10-01")
        self.assertEqual(self.calls()[0]["args"], ["check", "--json"])
        self.assertEqual(Path(self.calls()[0]["env"]["COOLOFF_HOME"]), self.w.cooloff.parent)


class FallbackTests(JsonModeBase):
    def bad_outputs(self, name):
        _, _, spec, _ = CASES[name]
        base = spec.get("out") or spec["by_arg"]["due"]
        yield "명령 실패", self.fake(name, "fail")
        yield "JSON 아님", self.fake(name, "garbage")
        yield "모르는 버전", self.fake(name, out={**base, "version": 2})
        yield "버전 없음", self.fake(name, out={k: v for k, v in base.items() if k != "version"})
        yield "모양이 다름", self.fake(name, out={"version": 1, "generated_for": TODAY, "date": TODAY, "items": "x",
                                              "first_task": 5, "goals_yesterday": [1], "goals_due_soon": [],
                                              "flags": [], "cooling_ended": [{}], "followups_due": [], "renewals": [],
                                              "approved_not_bought": []})
        yield "다른 날짜", self.fake(name, out={**base, "generated_for": "2026-01-01", "date": "2026-01-01"})
        yield "없는 명령", {"command": [str(self.root / "no-such-binary")]}

    def test_each_adapter_falls_back_to_direct(self):
        for name in CASES:
            direct, _ = self.collect(name, "direct")
            self.assertTrue(direct, name)
            for label, conf in self.bad_outputs(name):
                with self.subTest(adapter=name, case=label):
                    with self.assertLogs("mbrief", "WARNING"):
                        items, ctx = self.collect(name, "auto", **conf)
                    self.assertEqual([(i.kind, i.title, i.due) for i in items],
                                     [(i.kind, i.title, i.due) for i in direct])
                    self.assertTrue(ctx.notes[name].startswith("모드: direct (json 실패"), ctx.notes[name])

    def test_json_mode_does_not_fall_back(self):
        for name in CASES:
            with self.subTest(adapter=name):
                with self.assertRaises(ContractUnavailable):
                    self.collect(name, "json", **self.fake(name, "fail"))

    def test_timeout_falls_back(self):
        sleeper = self.root / "sleep.py"
        sleeper.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        with self.assertLogs("mbrief", "WARNING"):
            items, ctx = self.collect("cool_off", command=[sys.executable, str(sleeper)], timeout=0.5)
        self.assertTrue(items)
        self.assertIn("시간 초과", ctx.notes["cool_off"])

    def test_bad_mode_and_command_config(self):
        with self.assertRaises(BadConfig):
            self.collect("cool_off", "sometimes")
        with self.assertRaises(BadConfig):
            self.collect("cool_off", command="python -m cool_off")        # 문자열이 아니라 목록이어야 함


class DirectModeTests(JsonModeBase):
    def test_direct_never_runs_command(self):
        for name in CASES:
            with self.subTest(adapter=name):
                items, ctx = self.collect(name, "direct", **self.fake(name))
                self.assertTrue(items)
                self.assertEqual(ctx.notes[name], "모드: direct")
        self.assertEqual(self.calls(), [])

    def test_direct_unchanged_texts(self):
        gc, _ = self.collect("game_calendar", "direct")
        ev = next(i for i in gc if i.kind == "event")
        self.assertEqual(ev.speak, "이벤트 마감: 이벤트게임 내일까지")        # json 전용 '이틀 뒤 종료' 문구가 아님

    def test_missing_source_stays_missing_in_direct(self):
        from mbrief.model import SourceMissing
        with self.assertRaises(SourceMissing):
            self.collect("cool_off", "direct", data=str(self.root / "none.json"))


class RunnerNoteTests(JsonModeBase):
    def test_sources_report_mode(self):
        sources = dict(self.cfg["sources"])
        sources["cool_off"] = {**sources["cool_off"], "mode": "auto", **self.fake("cool_off")}
        sources["voice_journal"] = {**sources["voice_journal"], "mode": "auto", **self.fake("voice_journal", "fail")}
        cfg = {**self.cfg, "sources": sources}
        with self.assertLogs("mbrief", "WARNING"):
            res = runner.build(cfg, self.w.ctx())
        src = res.brief["sources"]
        self.assertEqual(src["cool_off"]["note"], "모드: json")
        self.assertTrue(src["voice_journal"]["note"].startswith("모드: direct (json 실패"))
        self.assertEqual(src["paper_butler"]["note"], "모드: direct")
        self.assertTrue(src["cool_off"]["ok"] and src["voice_journal"]["ok"])
        from mbrief import contract
        self.assertEqual(contract.validate_brief(res.brief), [])


class RealSiblingTests(unittest.TestCase):
    """실제 형제 프로젝트의 --json 명령을 빈 데이터 폴더로 부른다 (형제 폴더가 없으면 건너뜀)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "world").mkdir()
        self.w = World(self.root / "world")

    def run_real(self, name, project, env=None, local_tz=False, **over):
        if not (REPO / project / "tests").is_dir():
            self.skipTest("형제 프로젝트 폴더가 없어요: %s" % project)
        mod = BY_NAME[name]
        cfg = source_cfg(self.w.config(), mod)
        cfg.update({"mode": "json", "timeout": 60, "cwd": str(REPO / project)})
        cfg.update(over)
        base_env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME")}
        ctx = self.w.ctx(env={**base_env, **(env or {})}, now=dt.datetime.now().astimezone() if local_tz else dt.datetime.now(KST))   # 형제 명령은 실제 오늘 기준
        return mod.collect(cfg, ctx), ctx

    def test_paper_butler_empty(self):
        home = self.root / "pb"
        home.mkdir()
        items, ctx = self.run_real("paper_butler", "paper-butler", {"PAPERBUTLER_HOME": str(home)}, db="")
        self.assertEqual(items, [])
        self.assertEqual(ctx.notes["paper_butler"], "모드: json")

    def test_game_calendar_empty(self):
        gdir = self.root / "gc"
        gdir.mkdir()
        conf = gdir / "gamecal.json"
        conf.write_text(json.dumps({"state_file": "state.json"}), encoding="utf-8")
        items, ctx = self.run_real("game_calendar", "game-calendar", gamecal_config=str(conf))
        self.assertEqual(items, [])
        self.assertEqual(ctx.notes["game_calendar"], "모드: json")

    def test_voice_journal_empty(self):
        items, ctx = self.run_real("voice_journal", "voice-journal", {"VJOURNAL_DB": str(self.root / "vj.db")}, db="")
        self.assertEqual(items, [])
        self.assertEqual(ctx.notes["voice_journal"], "모드: json")

    def test_cool_off_empty(self):
        home = self.root / "co"
        home.mkdir()
        items, ctx = self.run_real("cool_off", "cool-off", {"COOLOFF_HOME": str(home)}, local_tz=True, data="")
        self.assertEqual(items, [])
        self.assertEqual(ctx.notes["cool_off"], "모드: json")


if __name__ == "__main__":
    unittest.main()
