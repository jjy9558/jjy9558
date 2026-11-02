"""plan-v2 D-1 인수 테스트 5개 + 낭독 변환(6) + 계약 스키마(7)."""
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeOpener, NOW, TOKEN, World, jarvis_status
from mbrief import compose as C
from mbrief import contract, runner
from mbrief.model import Item
from mbrief.render import sources_table
from mbrief.sources import paper_butler
from mbrief.sqlro import open_ro


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.w = World(Path(self._tmp.name))
        self.cfg = self.w.config()

    def ctx(self, **kw):
        kw.setdefault("opener", FakeOpener({"/api/status": jarvis_status()}))
        return self.w.ctx(**kw)

    def snapshot(self):
        """out/ 를 뺀 모든 파일의 (경로 -> mtime_ns, 크기, 해시)."""
        snap = {}
        for p in sorted(self.w.root.rglob("*")):
            if p.is_file() and self.w.out not in p.parents and p.name != "mbrief.json":
                st = p.stat()
                snap[p.relative_to(self.w.root).as_posix()] = (st.st_mtime_ns, st.st_size,
                                                                hashlib.sha256(p.read_bytes()).hexdigest())
        return snap


class Acceptance1Order(Base):
    """(1) 어댑터 픽스처로 합성하면 우선순위 순서가 맞고 예산을 넘지 않는다."""

    def test_priority_order_and_budget(self):
        res = runner.build(self.cfg, self.ctx())
        prios = [p for _, p in res.ranked]
        self.assertEqual(prios, sorted(prios, reverse=True))
        self.assertEqual({i.source for i, _ in res.ranked},
                         {"paper_butler", "game_calendar", "cool_off", "voice_journal", "claude_night",
                          "jarvis", "sound_watch", "community_briefing"})   # 어댑터 8개 모두 참여
        text = res.brief["speak_text"]
        self.assertLessEqual(len(text), res.budget_chars)
        self.assertLessEqual(len(text), 300)
        # 낭독 순서 = 우선순위 순서 (점검 > 환불 > 전기 > 가스)
        pos = [text.index(w) for w in ("로스트아크", "쿠팡", "한국전력", "서울도시가스")]
        self.assertEqual(pos, sorted(pos))
        spoken_titles = [it.title for it, _ in res.spoken]
        ranked_titles = [it.title for it, _ in res.ranked if it.title in spoken_titles]
        self.assertEqual(spoken_titles, ranked_titles)

    def test_budget_scales_with_seconds(self):
        for sec in (10, 20, 40, 60):
            res = runner.build(self.cfg, self.ctx(), seconds=sec)
            self.assertLessEqual(len(res.brief["speak_text"]), min(int(sec * 5.0), 300), sec)
            self.assertIn("로스트아크", res.brief["speak_text"])      # 가장 급한 건 늘 말한다
        tiny = runner.build(self.cfg, self.ctx(), seconds=10).brief
        self.assertGreater(tiny["dropped"], 0)
        self.assertIn("그 밖에 %d건은 화면에서 확인하세요." % tiny["dropped"], tiny["speak_text"])

    def test_long_budget_speaks_everything_spoken(self):
        res = runner.build(self.cfg, self.ctx(), seconds=600, cap=False)
        self.assertEqual(res.brief["dropped"], 0)
        self.assertEqual(len(res.spoken), len([1 for it, _ in res.ranked if it.speak]))
        self.assertNotIn("그 밖에", res.brief["speak_text"])

    def test_seconds_from_config_and_cap(self):
        cfg = self.w.config(speak={"seconds": 10, "max_chars": 9999})
        self.assertEqual(cfg["speak"]["max_chars"], 300)
        self.assertEqual(C.budget_chars(cfg), 50)
        self.assertEqual(C.budget_chars(self.w.config(speak={"seconds": 600}), None), 300)

    def test_quiet_day_boosts_games(self):
        sat = self.ctx(now=NOW.replace(day=7))     # 2026-11-07 토요일
        cfg = self.cfg
        item = Item("game_calendar", "event", "x", priority=40)
        self.assertEqual(C.effective(item, cfg, quiet=False), 40)
        self.assertEqual(C.effective(item, cfg, quiet=True), 60)
        self.assertTrue(C.is_quiet_day(cfg, sat))
        self.assertFalse(C.is_quiet_day(cfg, self.ctx()))
        self.assertTrue(runner.build(cfg, sat).brief["quiet_day"])


class Acceptance2Isolation(Base):
    """(2) 어댑터 하나가 예외여도 나머지는 나오고 sources 에 실패가 표시된다."""

    def test_one_adapter_raises(self):
        with mock.patch.object(paper_butler, "collect", side_effect=RuntimeError("boom 터짐")):
            res = runner.build(self.cfg, self.ctx())
        src = res.brief["sources"]
        self.assertEqual(src["paper_butler"]["status"], "error")
        self.assertFalse(src["paper_butler"]["ok"])
        self.assertIn("RuntimeError: boom 터짐", src["paper_butler"]["note"])
        self.assertTrue(all(s["ok"] for n, s in src.items() if n != "paper_butler"))
        self.assertIn("로스트아크", res.brief["speak_text"])
        self.assertGreater(len(res.cards), 5)
        self.assertFalse([c for c in res.cards if c["kind"] in ("bill", "refund")])
        self.assertIn("❌ 오류", "\n".join(sources_table(src)))

    def test_corrupt_db_is_reported_not_fatal(self):
        self.w.journal.write_bytes(b"this is not a sqlite file " * 50)
        res = runner.build(self.cfg, self.ctx())
        self.assertEqual(res.brief["sources"]["voice_journal"]["status"], "error")
        self.assertEqual(res.brief["sources"]["paper_butler"]["status"], "ok")

    def test_missing_disabled_and_unreachable_hub(self):
        cfg = self.w.config(sources={**self.cfg["sources"], "cool_off": {"data": str(self.w.root / "none.json")},
                                     "sound_watch": {"enabled": False},
                                     "jarvis": {"device": "s21", "token": TOKEN}})
        op = FakeOpener({"/api/status": OSError("down")})
        res = runner.build(cfg, self.ctx(opener=op))
        src = res.brief["sources"]
        self.assertEqual(src["cool_off"]["status"], "missing")
        self.assertEqual(src["sound_watch"]["status"], "disabled")
        self.assertEqual(src["jarvis"]["status"], "error")
        self.assertIn("허브 호출 실패", src["jarvis"]["note"])
        self.assertTrue(contract.validate_brief(res.brief) == [])

    def test_error_note_has_no_paths(self):
        self.w.paper.write_bytes(b"garbage" * 30)
        note = runner.build(self.cfg, self.ctx()).brief["sources"]["paper_butler"]["note"]
        self.assertNotIn(str(self.w.root), note)


class Acceptance3ReadOnly(Base):
    """(3) sqlite 를 mode=ro 로 열어 원본 mtime 이 그대로다."""

    def test_sources_do_not_touch_originals(self):
        before = self.snapshot()
        res = runner.build(self.cfg, self.ctx())
        runner.write_outputs(res, self.cfg, self.ctx())
        self.assertEqual(self.snapshot(), before)         # 파일 목록, mtime, 크기, 해시 모두 동일
        self.assertTrue(res.brief["sources"]["paper_butler"]["ok"])

    def test_connection_really_is_read_only(self):
        with open_ro(self.w.paper) as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("DELETE FROM documents")
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("CREATE TABLE x(a)")

    def test_open_ro_never_creates_a_missing_db(self):
        ghost = self.w.root / "ghost.db"
        with self.assertRaises(Exception):
            with open_ro(ghost):
                pass
        self.assertFalse(ghost.exists())


class Acceptance4DryRun(Base):
    """(4) --dry-run 이면 네트워크 호출이 0이다."""

    def test_dry_run_makes_no_calls(self):
        from mbrief.cli import main
        import io
        op = FakeOpener({"/api/status": jarvis_status()})
        cfgfile = self.w.root / "mbrief.json"
        self.w.config()
        out = io.StringIO()
        rc = main(["run", "--config", str(cfgfile), "--dry-run", "--speak", "--push"], out, opener=op,
                  clock=lambda: NOW, env={})
        self.assertEqual(rc, 0)
        self.assertEqual(op.requests, [])
        self.assertFalse(self.w.out.exists())            # 파일도 쓰지 않는다
        self.assertIn("건너뜀", out.getvalue())           # 자비스 어댑터
        self.assertIn("로스트아크", out.getvalue())

    def test_normal_run_does_use_network_as_expected(self):
        from mbrief.cli import main
        import io
        op = FakeOpener({"/api/status": jarvis_status()})
        self.w.config()
        out = io.StringIO()
        rc = main(["run", "--config", str(self.w.root / "mbrief.json"), "--speak", "--push"], out,
                  opener=op, clock=lambda: NOW, env={})
        self.assertEqual(rc, 0, out.getvalue())
        urls = op.urls()
        self.assertEqual(len(urls), 3)
        self.assertTrue(urls[0].endswith("/api/status"))
        say = next(r for r in op.requests if r.full_url.endswith("/api/say"))
        self.assertEqual(say.get_header("X-sidekick-token"), TOKEN)
        spoken = json.loads(say.data.decode("utf-8"))["text"]
        self.assertEqual(spoken, json.loads((self.w.out / "brief-latest.json").read_text(encoding="utf-8"))["speak_text"])
        ntfy = next(r for r in op.requests if r.full_url.startswith("https://ntfy.example"))
        body = json.loads(ntfy.data.decode("utf-8"))
        self.assertTrue(body["title"].startswith("아침 브리핑 11월 2일"))
        self.assertLess(len(body["message"]), 1000)


class Acceptance5Idempotent(Base):
    """(5) 같은 날 두 번 돌려도 같은 결과가 나온다."""

    def test_two_runs_identical_bytes(self):
        names = ["brief-2026-11-02.md", "brief-2026-11-02.json", "brief-latest.json", "cards-latest.json"]
        outs = []
        for now in (NOW, NOW.replace(minute=47)):
            res = runner.build(self.cfg, self.ctx(now=now))
            runner.write_outputs(res, self.cfg, self.ctx(now=now))
            outs.append({n: (self.w.out / n).read_bytes() for n in names})
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(sorted(p.name for p in self.w.out.iterdir()), sorted(names))   # 임시 파일이 남지 않음
        self.assertEqual(outs[0]["brief-latest.json"], outs[0]["brief-2026-11-02.json"])


class Acceptance6Speakable(Base):
    """(6) speak_text 가 300자를 넘지 않고 숫자·날짜를 읽기 좋게 변환한다."""

    def test_speak_text_is_speakable(self):
        text = runner.build(self.cfg, self.ctx()).brief["speak_text"]
        self.assertLessEqual(len(text), 300)
        for bad in (r"D-\d", r"\d{4}-\d{2}-\d{2}", r"\d,\d{3}", r"\d:\d{2}", r"https?://", r"[*`#\[\]]"):
            self.assertIsNone(re.search(bad, text), "%s in %s" % (bad, text))
        self.assertIn("만", text)             # 금액이 '4만 5천 2백원' 식
        self.assertIn("오전 10시", text)
        self.assertRegex(text, r"\d+일 뒤|모레|내일")

    def test_raw_adapter_text_is_cleaned_in_final_speech(self):
        res = C.compose([Item("jarvis", "x", "t", priority=99, speak="D-3 에 2026-11-05 10:30 까지 12,000원 결제")],
                        {"jarvis": {}}, {}, self.cfg, self.ctx())
        t = res.brief["speak_text"]
        self.assertIn("3일 뒤", t)
        self.assertIn("11월 5일 오전 10시 30분", t)
        self.assertIn("1만 2천원", t)

    def test_overlong_top_item_is_shortened_not_overflowed(self):
        long = "아주 긴 문장 " * 80
        res = C.compose([Item("jarvis", "x", "t", priority=99, speak=long),
                         Item("jarvis", "y", "t2", priority=50, speak="짧은 일")],
                        {"jarvis": {}}, {}, self.cfg, self.ctx())
        t = res.brief["speak_text"]
        self.assertLessEqual(len(t), 300)
        self.assertIn("아주 긴 문장", t)
        self.assertTrue(res.spoken)

    def test_empty_day(self):
        res = C.compose([], {}, {}, self.cfg, self.ctx())
        self.assertEqual(res.brief["speak_text"], "좋은 아침입니다. 11월 2일 월요일. " + C.EMPTY_SPEECH)
        self.assertEqual(contract.validate_brief(res.brief), [])
        self.assertEqual(res.cards, [])

    def test_many_dropped_digits_still_fit(self):
        items = [Item("jarvis", "x", "t%d" % i, priority=50 - i * 0.01, speak="항목 번호 %d 입니다 정말로" % i)
                 for i in range(150)]
        res = C.compose(items, {"jarvis": {}}, {}, self.cfg, self.ctx())
        t = res.brief["speak_text"]
        self.assertLessEqual(len(t), 300)
        self.assertIn("그 밖에 %d건" % res.brief["dropped"], t)


class Acceptance7Contract(Base):
    """(7) 두 계약 파일의 스키마."""

    def written(self):
        res = runner.build(self.cfg, self.ctx())
        runner.write_outputs(res, self.cfg, self.ctx())
        brief = json.loads((self.w.out / "brief-latest.json").read_text(encoding="utf-8"))
        cards = json.loads((self.w.out / "cards-latest.json").read_text(encoding="utf-8"))
        return brief, cards

    def test_written_files_validate(self):
        brief, cards = self.written()
        self.assertEqual(contract.validate_brief(brief), [])
        self.assertEqual(contract.validate_cards(cards), [])
        self.assertEqual(contract.check_files(self.w.out), {"brief-latest.json": [], "cards-latest.json": []})

    def test_brief_fields(self):
        brief, _ = self.written()
        self.assertEqual(brief["date"], "2026-11-02")
        self.assertLessEqual(len(brief["speak_text"]), 300)
        self.assertEqual(brief["reject_text"], "일어나면 해 드립니다")
        self.assertEqual(brief["version"], 1)
        titles = [s["title"] for s in brief["sections"]]
        self.assertIn("납부·기한", titles)
        first = brief["sections"][0]["items"][0]
        self.assertEqual(set(first), {"source", "kind", "text", "detail", "priority", "due", "spoken"})
        self.assertTrue(set(brief["sources"]) >= {"paper_butler", "jarvis"})

    def test_cards_shape_and_order(self):
        _, cards = self.written()
        self.assertIsInstance(cards, list)
        self.assertGreater(len(cards), 8)
        for c in cards:
            self.assertEqual(set(c), {"kind", "title", "detail", "due", "actions"})
            self.assertIsInstance(c["actions"], list)
        dues = [c["due"] for c in cards]
        timed = [d for d in dues if d is not None]
        self.assertEqual(dues[:len(timed)], timed)                 # due 없는 카드는 맨 뒤
        keys = [d[:16] if "T" in d else d + "T00:00" for d in timed]
        self.assertEqual(keys, sorted(keys))                       # 마감순
        self.assertNotIn("news-line", [c["kind"] for c in cards])   # 낭독 전용 줄은 카드가 아님
        self.assertIn("news", [c["kind"] for c in cards])

    def test_validators_reject_bad_files(self):
        brief, cards = self.written()
        bad = dict(brief, speak_text="가" * 301)
        self.assertTrue(any("301" in e for e in contract.validate_brief(bad)))
        self.assertTrue(contract.validate_brief(dict(brief, reject_text="두\n줄")))
        self.assertTrue(contract.validate_brief(dict(brief, reject_text="")))
        self.assertTrue(contract.validate_brief(dict(brief, date="오늘")))
        self.assertTrue(contract.validate_brief({**brief, "sources": {"x": {"status": "?", "ok": True}}}))
        self.assertTrue(contract.validate_brief([]))
        extra = [dict(cards[0], extra=1)] + cards[1:]
        self.assertTrue(contract.validate_cards(extra))
        self.assertTrue(contract.validate_cards([dict(cards[0], due="내일")]))
        self.assertTrue(contract.validate_cards([dict(cards[0], actions="x")]))
        self.assertTrue(contract.validate_cards(list(reversed([c for c in cards if c["due"]]))))
        self.assertTrue(contract.validate_cards({}))

    def test_check_files_reports_missing_and_garbage(self):
        res = contract.check_files(self.w.root / "empty")
        self.assertEqual(res["brief-latest.json"], ["파일이 없어요"])
        self.w.out.mkdir(exist_ok=True)
        (self.w.out / "brief-latest.json").write_text("{not json", encoding="utf-8")
        self.assertIn("JSON 이 아님", contract.check_files(self.w.out)["brief-latest.json"][0])

    def test_consumer_view_only_needs_documented_keys(self):
        """WP18/WP4 가 읽는 방식 그대로: 문서화된 키만으로 충분하다."""
        brief, cards = self.written()
        speak, reject = brief["speak_text"], brief["reject_text"]
        self.assertTrue(speak and reject)
        for c in cards:
            c["kind"], c["title"], c["detail"], c["due"], c["actions"]


if __name__ == "__main__":
    unittest.main()
