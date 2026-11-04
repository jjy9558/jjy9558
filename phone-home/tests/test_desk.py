import json
import shutil
import subprocess
import unittest
from pathlib import Path

from phonehome.desk import card_key, due_sort_key, load_cards, resolve_action

from helpers import FakeBackend, Harness, dead_url, fake_token

FIX = Path(__file__).resolve().parent / "fixtures" / "cards-latest.json"


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout="리포트 완료", stderr="")


def sidekick_routes(state):
    def pending(h, b):
        return 200, {"pending": [i for i in state["items"] if i["status"] == "pending"]}, None

    def decide(h, b):
        req = json.loads(b)
        for i in state["items"]:
            if i["id"] == req["id"]:
                i["status"] = req["decision"]
                return 200, dict(i), None
        return 404, {"error": "없음"}, None
    return {("GET", "/api/approval/pending"): pending, ("POST", "/api/approval/decide"): decide}


def jarvis_routes(state):
    def jobs(h, b):
        assert "status=waiting_approval" in h.path
        return 200, {"jobs": [j for j in state["jobs"] if j["status"] == "waiting_approval"]}, None

    routes = {("GET", "/api/jobs"): jobs}
    for j in state["jobs"]:
        def act(h, b, j=j):
            j["status"] = "queued" if h.path.endswith("/approve") else "cancelled"
            return 200, {"job": j}, None
        routes[("POST", "/api/jobs/%d/approve" % j["id"])] = act
        routes[("POST", "/api/jobs/%d/reject" % j["id"])] = act
    return routes


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.sk_state = {"items": [
            {"id": "a1b2c3d4e5f60708", "status": "pending", "label": "강제 푸시", "command": "git push --force",
             "summary": "강제 푸시", "tool": "Bash", "expires_in": 40},
            {"id": "0f0e0d0c0b0a0908", "status": "pending", "label": "강제 삭제", "command": "rm -rf build/",
             "summary": "강제 삭제 build", "tool": "Bash", "expires_in": 40}]}
        self.jv_state = {"jobs": [
            {"id": 7, "type": "claude.task", "title": "리팩터링", "status": "waiting_approval",
             "payload": {"prompt": "고쳐줘"}, "created_by": "s21"},
            {"id": 8, "type": "shell.allowed", "title": "디스크 확인", "status": "waiting_approval",
             "payload": {"cmd": "df"}, "created_by": "s21"}]}
        self.sk = FakeBackend(sidekick_routes(self.sk_state))
        self.jv = FakeBackend(jarvis_routes(self.jv_state))
        self.runner = FakeRunner()
        self.h = Harness({"sidekick": {"url": self.sk.url, "token": fake_token("sk")},
                          "jarvis": {"url": self.jv.url, "token": fake_token("jv")}},
                         desk={"actions": {"cool-report": {"label": "구독 리포트", "argv": ["echo", "hi"]}}},
                         runner=self.runner)
        self.cards = Path(self.h.cfg["desk"]["cards_path"])
        self.log = Path(self.h.cfg["desk"]["log_path"])

    def tearDown(self):
        self.h.close()
        self.sk.close()
        self.jv.close()

    # (6) 카드 파일이 없거나 깨져도 /desk가 빈 상태로 200
    def test_missing_or_broken_cards_still_200(self):
        code, _, page = self.h.request("GET", "/desk")
        self.assertEqual(code, 200)
        self.assertIn('"cards_status": "missing"'.encode(), page)
        self.cards.write_text("{not json", encoding="utf-8")
        code, _, page = self.h.request("GET", "/desk")
        self.assertEqual(code, 200)
        self.assertIn('"cards_status": "broken"'.encode(), page)
        code, snap = self.h.json("GET", "/desk/api/items")
        self.assertEqual((code, snap["cards"], snap["cards_status"]), (200, [], "broken"))
        self.cards.write_text('{"cards": "x"}', encoding="utf-8")
        self.assertEqual(self.h.json("GET", "/desk/api/items")[1]["cards_status"], "broken")

    def test_desk_requires_login(self):
        code, _, page = self.h.request("GET", "/desk", token=None)
        self.assertEqual(code, 401)
        self.assertNotIn("cards_status".encode(), page)
        self.assertEqual(self.h.json("GET", "/desk/api/items", token=fake_token("x"))[0], 401)

    def test_cards_sorted_by_due(self):
        shutil.copy(FIX, self.cards)
        _, snap = self.h.json("GET", "/desk/api/items")
        titles = [c["title"] for c in snap["cards"]]
        self.assertEqual(titles, ["로스트아크 점검", "가스비 납부", "넷플릭스 갱신", "마감 없는 메모"])
        gas = snap["cards"][1]
        self.assertEqual(gas["actions"][0]["url"], "/s/sidekick/")
        self.assertFalse(gas["actions"][1]["runnable"])  # 문자열 action 은 표시 전용
        self.assertTrue(gas["actions"][1]["text"])
        self.assertTrue(snap["cards"][2]["actions"][0]["runnable"])

    def test_stamp_logs_only(self):
        shutil.copy(FIX, self.cards)
        before = self.cards.read_bytes()
        _, snap = self.h.json("GET", "/desk/api/items")
        key = snap["cards"][0]["key"]
        code, r = self.h.json("POST", "/desk/api/stamp", {"key": key, "stamp": "ok"})
        self.assertEqual((code, r["label"]), (200, "확인"))
        code, r = self.h.json("POST", "/desk/api/stamp", {"key": key, "stamp": "reject"})
        self.assertEqual(code, 200)
        lines = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["stamp"] for x in lines], ["ok", "reject"])
        self.assertEqual(lines[0]["title"], "로스트아크 점검")
        self.assertEqual(self.cards.read_bytes(), before)  # 카드 파일은 건드리지 않는다
        self.assertEqual(self.h.json("GET", "/desk/api/items")[1]["cards"][0]["stamp"], "reject")
        self.assertEqual(self.h.json("POST", "/desk/api/stamp", {"key": "0" * 12, "stamp": "ok"})[0], 404)
        self.assertEqual(self.h.json("POST", "/desk/api/stamp", {"key": key, "stamp": "maybe"})[0], 400)
        self.assertEqual(self.runner.calls, [])

    # (7) 화이트리스트에 없는 action 요청은 403
    def test_action_whitelist(self):
        for bad in ("mark-paid", "rm", "", None, ["echo"]):
            code, _ = self.h.json("POST", "/desk/api/action", {"action": bad})
            self.assertEqual(code, 403, bad)
        self.assertEqual(self.runner.calls, [])
        code, r = self.h.json("POST", "/desk/api/action", {"action": "cool-report"})
        self.assertEqual((code, r["ok"], r["output"]), (200, True, "리포트 완료"))
        argv, kw = self.runner.calls[0]
        self.assertEqual(argv, ["echo", "hi"])
        self.assertIs(kw["shell"], False)
        logged = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x.get("result", x.get("rc")) for x in logged], ["forbidden"] * 5 + [0])

    def test_default_whitelist_empty(self):
        h = Harness({}, runner=self.runner)
        try:
            self.assertEqual(h.cfg["desk"]["actions"], {})
            self.assertEqual(h.json("POST", "/desk/api/action", {"action": "cool-report"})[0], 403)
        finally:
            h.close()

    def test_desk_post_needs_json(self):
        code, _, _ = self.h.request("POST", "/desk/api/action", b"action=cool-report",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(code, 415)

    def test_approvals_aggregated_with_link_only(self):
        _, snap = self.h.json("GET", "/desk/api/items")
        by = {(a["source"], a["id"]): a for a in snap["approvals"]}
        self.assertEqual(len(by), 4)
        self.assertFalse(by[("sidekick", "a1b2c3d4e5f60708")]["link_only"])
        self.assertTrue(by[("sidekick", "0f0e0d0c0b0a0908")]["link_only"])  # rm -rf
        self.assertTrue(by[("jarvis", "7")]["link_only"])  # claude.task
        self.assertFalse(by[("jarvis", "8")]["link_only"])
        self.assertEqual(by[("jarvis", "7")]["link"], "/go/jarvis")
        self.assertEqual(snap["errors"], {})

    def test_decide_goes_to_owner_only(self):
        code, r = self.h.json("POST", "/desk/api/decide", {"source": "sidekick", "id": "a1b2c3d4e5f60708",
                                                            "decision": "allow"})
        self.assertEqual(code, 200)
        self.assertEqual(self.sk_state["items"][0]["status"], "allow")
        dec = [x for x in self.sk.requests if x["path"] == "/api/approval/decide"][0]
        self.assertTrue(dec["headers"]["X-Sidekick-Token"].startswith("sk-"))
        self.assertFalse(any("/api/jobs/" in x["path"] and x["method"] == "POST" for x in self.jv.requests))
        code, r = self.h.json("POST", "/desk/api/decide", {"source": "jarvis", "id": "8", "decision": "deny"})
        self.assertEqual(code, 200)
        self.assertEqual(self.jv_state["jobs"][1]["status"], "cancelled")
        self.assertTrue(any(x["path"] == "/api/jobs/8/reject" for x in self.jv.requests))

    def test_risky_decide_refused(self):
        for src, rid in (("sidekick", "0f0e0d0c0b0a0908"), ("jarvis", "7")):
            code, r = self.h.json("POST", "/desk/api/decide", {"source": src, "id": rid, "decision": "allow"})
            self.assertEqual(code, 403, src)
        self.assertEqual(self.sk_state["items"][1]["status"], "pending")
        self.assertEqual(self.jv_state["jobs"][0]["status"], "waiting_approval")

    def test_decide_bad_input(self):
        cases = [({"source": "sidekick", "id": "zz", "decision": "allow"}, 400),
                 ({"source": "jarvis", "id": "1/../x", "decision": "allow"}, 400),
                 ({"source": "sidekick", "id": "ffff", "decision": "allow"}, 404),
                 ({"source": "nope", "id": "1", "decision": "allow"}, 404),
                 ({"source": "jarvis", "id": "8", "decision": "yes"}, 400)]
        for body, want in cases:
            self.assertEqual(self.h.json("POST", "/desk/api/decide", body)[0], want, body)

    def test_backend_down_shows_error_not_crash(self):
        h = Harness({"sidekick": {"url": dead_url()}, "jarvis": {"url": dead_url()}})
        try:
            code, snap = h.json("GET", "/desk/api/items")
            self.assertEqual(code, 200)
            self.assertEqual(set(snap["errors"]), {"sidekick", "jarvis"})
            self.assertEqual(h.request("GET", "/desk")[0], 200)
            code, _ = h.json("POST", "/desk/api/decide", {"source": "jarvis", "id": "1", "decision": "allow"})
            self.assertEqual(code, 502)
        finally:
            h.close()


class CardUnitTests(unittest.TestCase):
    def test_load_cards_tolerant(self):
        cards, status = load_cards(FIX)
        self.assertEqual(status, "ok")
        self.assertEqual(len(cards), 4)
        self.assertEqual(load_cards(None), ([], "missing"))

    def test_external_action_url_dropped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps([{"kind": "x", "title": "t", "actions": [
                {"id": "a", "url": "https://evil.example/"}, {"label": "no id"}, {"url": "//evil"}]}]),
                encoding="utf-8")
            cards, _ = load_cards(p)
        self.assertEqual(cards[0]["actions"], [{"id": "a", "label": "a", "url": ""}])

    def test_due_order_and_key(self):
        keys = sorted(["", "2026-11-05", "아무때나", "2026-11-03T09:00"], key=due_sort_key)
        self.assertEqual(keys, ["2026-11-03T09:00", "2026-11-05", "아무때나", ""])
        self.assertEqual(card_key("a", "b", "c"), card_key("a", "b", "c"))
        self.assertNotEqual(card_key("a", "b", "c"), card_key("a", "b", "d"))




class PrefixActionTests(unittest.TestCase):
    ALLOWED = {"paperbutler.done:": {"label": "완료 처리", "argv": ["python3", "-m", "paperbutler", "done", "{arg}"]},
               "cool-report": {"label": "리포트", "argv": ["echo", "hi"]}}

    def setUp(self):
        self.runner = FakeRunner()
        self.h = Harness({}, desk={"actions": self.ALLOWED}, runner=self.runner)
        self.cards = Path(self.h.cfg["desk"]["cards_path"])
        self.log = Path(self.h.cfg["desk"]["log_path"])

    def tearDown(self):
        self.h.close()

    def test_resolve_action(self):
        self.assertEqual(resolve_action(self.ALLOWED, "paperbutler.done:12")[1],
                         ["python3", "-m", "paperbutler", "done", "12"])
        self.assertEqual(resolve_action(self.ALLOWED, "cool-report")[1], ["echo", "hi"])
        for bad in ("paperbutler.done:", "paperbutler.done:1a", "paperbutler.done:-1", "paperbutler.done: 1",
                    "paperbutler.done:1 2", "paperbutler.done:1;rm", "paperbutler.done:1\n", "paperbutler.done:٣",
                    "paperbutler.done:" + "1" * 13, "paperbutler.done", "paperbutler.undo:1", "cool-report:1",
                    "xpaperbutler.done:1", "", None, 5):
            self.assertIsNone(resolve_action(self.ALLOWED, bad), repr(bad))

    def test_string_action_is_text_even_if_whitelisted(self):
        """문자열 action 은 id 로 해석하지 않는다: 화이트리스트와 같은 글자여도 버튼이 아니다."""
        self.cards.write_text(json.dumps([{"kind": "x", "title": "t", "actions": ["cool-report", "읽기만 하세요"]}]),
                              encoding="utf-8")
        acts = self.h.json("GET", "/desk/api/items")[1]["cards"][0]["actions"]
        self.assertEqual([(a["label"], a["runnable"], a.get("text")) for a in acts],
                         [("cool-report", False, True), ("읽기만 하세요", False, True)])
        self.assertEqual(self.runner.calls, [])

    def test_object_action_runnable_only_when_whitelisted(self):
        self.cards.write_text(json.dumps([{"kind": "x", "title": "t", "actions": [
            {"id": "cool-report", "label": "리포트"}, {"id": "other", "label": "다른 것"}]}]), encoding="utf-8")
        acts = self.h.json("GET", "/desk/api/items")[1]["cards"][0]["actions"]
        self.assertEqual([a["runnable"] for a in acts], [True, False])

    def test_prefix_card_button_and_run(self):
        self.cards.write_text(json.dumps([{"kind": "bill", "title": "가스", "detail": "", "due": "2026-11-04", "actions": [
            "완료 처리: python -m paperbutler done 12",
            {"id": "paperbutler.done:12", "label": "완료 처리", "text": "완료 처리: ..."},
            {"id": "paperbutler.done:abc", "label": "이상한 것"}]}]), encoding="utf-8")
        card = self.h.json("GET", "/desk/api/items")[1]["cards"][0]
        self.assertEqual([a["runnable"] for a in card["actions"]], [False, True, False])
        code, r = self.h.json("POST", "/desk/api/action", {"action": "paperbutler.done:12", "key": card["key"]})
        self.assertEqual((code, r["ok"]), (200, True))
        argv, kw = self.runner.calls[0]
        self.assertEqual(argv, ["python3", "-m", "paperbutler", "done", "12"])
        self.assertIs(kw["shell"], False)

    def test_invalid_suffix_forbidden_and_never_run(self):
        for bad in ("paperbutler.done:abc", "paperbutler.done:1;ls", "paperbutler.done:", "paperbutler.done:$(id)",
                    "paperbutler.done:12 13", "paperbutler.purge:1"):
            self.assertEqual(self.h.json("POST", "/desk/api/action", {"action": bad})[0], 403, bad)
        self.assertEqual(self.runner.calls, [])

    def test_default_config_runs_nothing(self):
        h = Harness({})
        try:
            self.assertEqual(h.json("POST", "/desk/api/action", {"action": "paperbutler.done:1"})[0], 403)
        finally:
            h.close()

    def test_config_validation(self):
        from phonehome import config as C
        ok = C.normalize({"server": {"token": "t" * 12}, "services": {}, "desk": {"actions": self.ALLOWED}})
        self.assertIn("paperbutler.done:", ok["desk"]["actions"])
        for bad in ({"a.b:": {"argv": ["x"]}},                      # {arg} 없음
                    {"a.b:": {"argv": ["x", "{arg}", "{arg}"]}},       # 둘
                    {"a.b:": {"argv": ["x", "--id={arg}"]}},           # 인자 일부로 끼움
                    {"a.b:": {"argv": ["x", "{arg}"], "arg": "any"}},  # 모르는 검증
                    {"a-b": {"argv": ["x", "{arg}"]}}):                # 정확한 id 에 {arg}
            with self.assertRaises(ValueError, msg=str(bad)):
                C.normalize({"server": {"token": "t" * 12}, "services": {}, "desk": {"actions": bad}})


class CrossMorningBriefTests(unittest.TestCase):
    """morning-brief 가 실제로 만든 cards-latest.json 을 책상이 그대로 읽는다."""

    def setUp(self):
        mb = Path(__file__).resolve().parents[2] / "morning-brief"
        if not (mb / "mbrief").is_dir():
            self.skipTest("morning-brief 폴더가 없음")
        import sys
        import datetime as dt
        sys.path.insert(0, str(mb))
        self.addCleanup(lambda: sys.path.remove(str(mb)))
        for m in [m for m in sys.modules if m == "mbrief" or m.startswith("mbrief.")]:
            del sys.modules[m]
        self.addCleanup(lambda: [sys.modules.pop(m) for m in [m for m in sys.modules if m == "mbrief" or m.startswith("mbrief.")]])
        from mbrief import compose, contract
        from mbrief.config import load_config
        from mbrief.model import Context, Item
        from mbrief.sources import paper_butler
        cfg, _ = load_config(None)
        ctx = Context(now=dt.datetime(2026, 11, 2, 7, 30, tzinfo=dt.timezone(dt.timedelta(hours=9))), env={})
        items = [
            Item("paper_butler", "bill", "서울도시가스 3만원", "납부기한", 90, "납부", "", "2026-11-04",
                 (paper_butler.done_action(12),)),
            Item("game_calendar", "maintenance", "점검", "", 80, "점검", "", "2026-11-02T10:00+09:00",
                 ("공지 열기: https://notice.example/1",)),
            Item("paper_butler", "bill", "수상한 id", "", 50, "x", "", "2026-11-05",
                 ({"id": "paperbutler.done:1;rm -rf /", "label": "이상", "text": "이상"},))]
        c = compose.compose(items, {}, {}, cfg, ctx)
        self.assertEqual(contract.validate_cards(c.cards), [])
        self.h = Harness({}, desk={"actions": {
            "paperbutler.done:": {"label": "완료 처리", "argv": ["python3", "-m", "paperbutler", "done", "{arg}"]}}},
            runner=FakeRunner())
        self.addCleanup(self.h.close)
        Path(self.h.cfg["desk"]["cards_path"]).write_text(json.dumps(c.cards, ensure_ascii=False), encoding="utf-8")

    def test_desk_renders_brief_cards(self):
        code, snap = self.h.json("GET", "/desk/api/items")
        self.assertEqual((code, snap["cards_status"], len(snap["cards"])), (200, "ok", 3))
        by = {c["title"]: c for c in snap["cards"]}
        gas = by["서울도시가스 3만원"]["actions"]
        self.assertEqual([(a["id"], a["label"], a["runnable"]) for a in gas], [("paperbutler.done:12", "완료 처리", True)])
        notice = by["점검"]["actions"]
        self.assertEqual([(a["runnable"], a.get("text"), a["label"]) for a in notice],
                         [(False, True, "공지 열기: https://notice.example/1")])
        self.assertEqual([a["runnable"] for a in by["수상한 id"]["actions"]], [False])
        # 버튼으로 실제 실행 (가짜 runner)
        code, r = self.h.json("POST", "/desk/api/action", {"action": "paperbutler.done:12"})
        self.assertEqual(code, 200)
        self.assertEqual(self.h.app.desk.runner.calls[0][0], ["python3", "-m", "paperbutler", "done", "12"])
        self.assertEqual(self.h.json("POST", "/desk/api/action", {"action": "paperbutler.done:1;rm -rf /"})[0], 403)
        self.assertEqual(len(self.h.app.desk.runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
