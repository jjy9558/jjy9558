"""어댑터별 픽스처 테스트: 각 프로젝트의 실제 저장 형식을 흉내 낸 파일을 읽는다."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from helpers import FakeOpener, NOW, TOKEN, World, jarvis_status
from mbrief.model import SourceMissing, SourceSkip
from mbrief.runner import source_cfg
from mbrief.sources import BY_NAME


class SourceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.w = World(Path(self._tmp.name))
        self.cfg = self.w.config()

    def run_source(self, name, ctx=None, **override):
        mod = BY_NAME[name]
        scfg = source_cfg(self.cfg, mod)
        scfg.update(override)
        return mod.collect(scfg, ctx or self.w.ctx())

    def by_kind(self, items):
        return {i.kind: i for i in items}

    def test_game_calendar(self):
        items = self.run_source("game_calendar")
        kinds = [i.kind for i in items]
        self.assertEqual(sorted(kinds), ["event", "maintenance", "shutdown"])   # 공지, 지난 점검, 깨진 항목은 제외
        m = self.by_kind(items)["maintenance"]
        self.assertEqual(m.due, "2026-11-02T10:00+09:00")
        self.assertIn("오전 10시", m.speak)
        self.assertGreater(m.priority, 85)
        s = self.by_kind(items)["shutdown"]
        self.assertIn("3일 뒤", s.speak)
        self.assertEqual(s.due, "2026-11-05")
        self.assertTrue(m.actions[0].startswith("공지 열기"))

    def test_game_calendar_ongoing_maintenance_and_missing(self):
        later = NOW.replace(hour=11)       # 점검 시작(10시) 이후, 끝(12시) 전
        m = self.by_kind(self.run_source("game_calendar", self.w.ctx(now=later)))["maintenance"]
        self.assertIn("점검 중", m.speak)
        self.assertIn("정오까지", m.speak)
        gone = NOW.replace(hour=13)
        self.assertNotIn("maintenance", self.by_kind(self.run_source("game_calendar", self.w.ctx(now=gone))))
        with self.assertRaises(SourceMissing):
            self.run_source("game_calendar", state="")

    def test_community_picks_latest_numbered_file_and_top_n(self):
        items = self.run_source("community_briefing")
        lines = [i for i in items if i.kind == "news-line"]
        self.assertEqual(len(lines), 3)
        self.assertIn("llama.cpp", lines[0].title)
        self.assertTrue(lines[0].speak.startswith("커뮤니티 소식"))
        self.assertTrue(all(not i.card for i in lines))
        card = self.by_kind(items)["news"]
        self.assertEqual(card.speak, "")
        self.assertIn("3. 파이썬", card.detail)

    def test_community_ignores_stale_days(self):
        old = NOW + dt.timedelta(days=3)
        self.assertEqual(self.run_source("community_briefing", self.w.ctx(now=old)), [])

    def test_paper_butler(self):
        items = self.run_source("paper_butler")
        bills = [i for i in items if i.kind == "bill"]
        self.assertEqual(len(bills), 2)                  # 처리 완료(done=1)와 기한 먼 것은 제외
        gas = next(i for i in bills if "가스" in i.title)
        self.assertEqual(gas.due, "2026-11-04")
        self.assertIn("모레", gas.speak)
        self.assertIn("3만 2천 4백원", gas.speak)
        self.assertEqual(gas.actions, ("완료 처리: python -m paperbutler done 1",))
        elec = next(i for i in bills if "전기" in i.title)
        self.assertIn("자동이체", elec.speak)
        self.assertLess(elec.priority, 90)               # 자동이체는 조금 낮춘다
        refunds = [i for i in items if i.kind == "refund"]
        self.assertEqual(len(refunds), 1)                # 이미 지난 환불 기한은 제외
        self.assertIn("쿠팡", refunds[0].title)
        self.assertEqual(refunds[0].actions, ("완료 처리: python -m paperbutler done 5",))

    def test_voice_journal(self):
        items = self.run_source("voice_journal")
        k = self.by_kind(items)
        decl = k["declaration"]
        self.assertIn("블로그 1개", decl.speak)
        self.assertIn("1개 미달", decl.speak)
        self.assertIn("[달성] 운동 하기 (1/1)", decl.detail)
        self.assertEqual(k["first-task"].detail, "블로그 초안 마무리하기")
        self.assertEqual(k["first-task"].priority, 58)
        self.assertIn("배포 스크립트", k["blocked"].detail)
        self.assertEqual(k["goal-due"].due, "2026-11-02")
        self.assertIn("건강검진", k["flag"].detail)

    def test_voice_journal_weekly_and_daily_misses(self):
        import sqlite3
        con = sqlite3.connect(str(self.w.journal))
        con.execute("INSERT INTO goals(id,created_ts,created_day,title,keywords,period,target,unit,status) "
                    "VALUES(10,'2026-10-26 09:00:00','2026-10-26','주 3회 달리기','[\"달리기\"]','week',3,'회','open')")
        con.executemany("INSERT INTO goal_events(goal_id,ts,day,amount) VALUES(10,?,?,1)",
                        [("2026-10-27 07:00:00", "2026-10-27"), ("2026-10-29 07:00:00", "2026-10-29")])
        con.execute("DELETE FROM goal_events WHERE goal_id=2")      # 어제 운동 기록 없음 -> 일간 선언 미달
        con.commit()
        con.close()
        decl = self.by_kind(self.run_source("voice_journal"))["declaration"]
        self.assertEqual(decl.title, "어제 선언 3/3 미달")
        self.assertIn("[미달] 주 3회 달리기 (2/3)", decl.detail)
        self.assertIn("[미달] 운동 하기 (0/1)", decl.detail)
        # 일요일이 아닌 날(어제가 토요일)에는 주간 선언을 판정하지 않는다
        sunday = self.w.ctx(now=NOW.replace(day=8))
        self.assertNotIn("달리기", self.by_kind(self.run_source("voice_journal", sunday))["declaration"].detail
                         if "declaration" in self.by_kind(self.run_source("voice_journal", sunday)) else "")

    def test_voice_journal_all_met_is_quiet_card(self):
        import sqlite3
        con = sqlite3.connect(str(self.w.journal))
        con.execute("UPDATE goals SET status='done', done_day='2026-11-01' WHERE id=1")
        con.commit()
        con.close()
        decl = self.by_kind(self.run_source("voice_journal"))["declaration"]
        self.assertIn("모두 달성", decl.title)
        self.assertFalse(decl.card)
        self.assertLess(decl.priority, 30)

    def test_cool_off(self):
        items = self.run_source("cool_off")
        subs = [i for i in items if i.kind == "subscription"]
        # 넷플릭스(5일)는 3일 뒤. 31일 결제 구독은 11월에 없으니 말일(30일)로 당겨 28일 뒤라 제외
        self.assertEqual([s.title.split()[0] for s in subs], ["넷플릭스"])
        self.assertIn("3일 뒤", subs[0].speak)
        self.assertIn("1만 7천원", subs[0].speak)
        ended = [i for i in items if i.kind == "cooloff-ended"]
        self.assertEqual([e.title.split(": ")[1].split()[0] for e in ended], ["기계식"])   # 아직 안 끝난 모니터 암 제외
        fu = [i for i in items if i.kind == "followup"]
        self.assertEqual(len(fu), 1)                    # steam 출처 구매와 만족도 기록된 구매는 제외
        self.assertIn("마우스", fu[0].title)
        self.assertIn("approved", [i.kind for i in items])

    def test_cool_off_month_end_and_yearly(self):
        end_of_month = self.w.ctx(now=dt.datetime(2026, 11, 28, 7, 30, tzinfo=NOW.tzinfo))
        subs = [i for i in self.run_source("cool_off", end_of_month) if i.kind == "subscription"]
        self.assertIn("음악 앱", subs[0].title)         # 31일 결제 -> 11월은 30일
        self.assertIn("모레", subs[0].speak)
        dec = self.w.ctx(now=dt.datetime(2026, 12, 19, 7, 30, tzinfo=NOW.tzinfo))
        subs = [i for i in self.run_source("cool_off", dec) if i.kind == "subscription"]
        self.assertTrue(any("클라우드 저장소" in s.title and "내일" in s.speak for s in subs))

    def test_jarvis(self):
        op = FakeOpener({"/api/status": jarvis_status()})
        items = self.run_source("jarvis", self.w.ctx(opener=op), watch_devices=["5080"])
        k = self.by_kind(items)
        self.assertIn("1건", k["job-approval"].title)
        self.assertEqual(k["job-failed"].title, "밤사이 자비스 작업 1건 실패")
        self.assertEqual(k["jobs-done"].title, "밤사이 자비스 작업 2건 완료")   # 오래전 작업, notify.say 제외
        self.assertEqual(k["device-offline"].title, "5080 오프라인")
        req = op.requests[0]
        self.assertTrue(req.full_url.endswith("/api/status"))
        self.assertEqual(req.get_header("X-jarvis-device"), "s21")
        self.assertEqual(req.get_header("X-jarvis-token"), TOKEN)
        self.assertEqual(req.get_method(), "GET")

    def test_jarvis_skip_and_missing(self):
        op = FakeOpener()
        with self.assertRaises(SourceSkip):
            self.run_source("jarvis", self.w.ctx(opener=op, dry_run=True))
        self.assertEqual(op.requests, [])
        with self.assertRaises(SourceMissing):
            self.run_source("jarvis", device="")
        with self.assertRaises(RuntimeError):
            self.run_source("jarvis", self.w.ctx(opener=FakeOpener({"/api/status": OSError("x")})))

    def test_jarvis_token_from_env(self):
        op = FakeOpener({"/api/status": jarvis_status()})
        ctx = self.w.ctx(opener=op, env={"JARVIS_TOKEN": TOKEN})
        self.run_source("jarvis", ctx, token="")
        self.assertEqual(op.requests[0].get_header("X-jarvis-token"), TOKEN)

    def test_sound_watch(self):
        items = self.run_source("sound_watch")
        k = self.by_kind(items)
        noise = k["noise"]
        self.assertEqual(noise.title, "밤사이 소음 3번")          # 어제 20시 이벤트는 밤 창 밖
        self.assertIn("새벽 2시 10분", noise.speak)
        self.assertIn("74 dB", noise.detail)                      # -40 + 120 - 6
        self.assertNotIn("dB", noise.speak)
        det = k["sound-detect"]
        self.assertEqual(det.title, "밤사이 감지: 노크 2번, 초인종 1번")   # beep 은 기본 무시
        with self.assertRaises(SourceMissing):
            self.run_source("sound_watch", data_dir="")

    def test_sound_watch_uncalibrated_detail(self):
        (self.w.sw / "config.json").write_text("{}", encoding="utf-8")
        noise = self.by_kind(self.run_source("sound_watch"))["noise"]
        self.assertIn("미보정 상대값", noise.detail)

    def test_claude_night(self):
        items = self.run_source("claude_night")
        k = self.by_kind(items)
        self.assertEqual(k["claude-summary"].title, "밤새 Claude 작업 2개, 확인 필요 1개")
        self.assertIn("확인이 필요", k["claude-summary"].speak)
        blocked = k["claude-blocked"]
        self.assertIn("api-server", blocked.title)
        self.assertIn("권한 승인", blocked.speak)
        done = k["claude-night"]
        self.assertIn("blog-site", done.title)
        lines = done.detail.splitlines()
        self.assertEqual(len(lines), 3)                          # 요청/완료/막힘 3줄
        self.assertIn("파일 1개 수정(build.py)", lines[1])
        self.assertIn("커밋 1회", lines[1])
        self.assertIn("테스트 1회", lines[1])
        self.assertTrue(lines[2].endswith("없음"))
        self.assertEqual(done.speak, "")                         # 막힌 게 없으면 화면에만
        self.assertNotIn("오래된 메시지", " ".join(i.detail for i in items))   # 밤 창 밖

    def test_claude_night_window_and_missing(self):
        later = NOW + dt.timedelta(days=2)
        self.assertEqual(self.run_source("claude_night", self.w.ctx(now=later)), [])
        with self.assertRaises(SourceMissing):
            self.run_source("claude_night", projects_dir=str(self.w.root / "nope"))

    def test_claude_night_redacts_secrets(self):
        proj = self.w.claude / "proj" / "leak.jsonl"
        fake_key = "sk-" + "a1" * 14
        line = {"type": "user", "timestamp": "2026-11-01T16:10:00Z", "cwd": "/work/leak", "sessionId": "s9",
                "uuid": "u9", "message": {"role": "user", "content": "키는 " + fake_key + " 이고 메일은 a@b.example"}}
        proj.write_text(json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8")
        blob = " ".join(i.detail + i.title + i.speak for i in self.run_source("claude_night"))
        self.assertNotIn(fake_key, blob)
        self.assertNotIn("a@b.example", blob)


if __name__ == "__main__":
    unittest.main()
