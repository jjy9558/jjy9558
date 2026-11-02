import datetime
import json
import os
import tempfile
import unittest

from helpers import make_hub, register_worker


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sched = os.path.join(self.tmp.name, "schedule.json")
        with open(self.sched, "w", encoding="utf-8") as f:
            json.dump({"events": [{"date": "2026-11-02", "time": "19:30", "title": "LCK 결승"},
                                  {"date": "2026-11-02", "time": "14:00", "title": "치과"},
                                  {"date": "2026-11-03", "time": "10:00", "title": "내일 회의"}]}, f,
                      ensure_ascii=False)
        self.hub = make_hub({"schedule": {"path": self.sched},
                             "router": {"shell_aliases": {"백업": {"name": "backup"}}}},
                            today=lambda: datetime.date(2026, 11, 2))
        self.say = lambda t, **kw: self.hub.router.handle("s21", t, **kw)

    def tearDown(self):
        self.tmp.cleanup()

    def _jobs(self, n_leased, n_queued, n_wait):
        register_worker(self.hub, "main", ["test"], ["test.echo"])
        for _ in range(n_leased + n_queued):
            self.hub.submit("main", {"type": "test.echo"})
        for _ in range(n_leased):
            self.hub.lease("main", {"caps": ["test"], "types": ["test.echo"]})
        for _ in range(n_wait):
            self.hub.submit("s21", {"type": "claude.task", "payload": {"prompt": "x"}})

    def test_ignored_without_wake_word(self):
        r = self.say("작업 상태 알려줘")
        self.assertTrue(r["ignored"])
        self.assertFalse(self.say("작업 상태 알려줘", direct=True)["ignored"])

    def test_status_answer_from_state_example(self):
        self._jobs(3, 0, 1)
        # main(5080)은 테스트에서 등록됐으니 오프라인으로 만든다
        self.hub.clock.advance(31)
        r = self.say("자비스, 작업 상태 알려줘")
        self.assertEqual(r["answer"], "작업 3개 진행 중, 1개 승인 대기, 5080 꺼져 있음.")

    def test_status_queued_and_gpu_online(self):
        self._jobs(1, 2, 0)
        r = self.say("자비스 상태 알려줘")
        self.assertEqual(r["answer"], "작업 1개 진행 중, 2개 대기, 5080 켜져 있음.")

    def test_status_empty(self):
        self.assertEqual(self.say("자비스 작업 상태")["answer"], "진행 중인 작업 없음, 5080 꺼져 있음.")

    def test_schedule_and_status_combined(self):
        r = self.say("자비스, 오늘 일정이랑 작업 상태 알려줘")
        self.assertEqual(r["intents"], ["schedule", "status"])
        self.assertEqual(r["answer"], "오늘 일정 2개: 14:00 치과, 19:30 LCK 결승. 진행 중인 작업 없음, 5080 꺼져 있음.")
        # 대답은 say 이벤트로도 나간다
        self.assertEqual(self.hub.store.recent_events(1)[0]["kind"], "say")

    def test_schedule_missing(self):
        hub = make_hub()
        self.assertEqual(hub.router.handle("s21", "자비스 오늘 일정")["answer"], "일정 파일이 설정되지 않았어요.")

    def test_devices(self):
        register_worker(self.hub, "minipc", ["lan"], ["wake"])
        r = self.say("자비스 기기 상태")
        self.assertIn("미니PC 켜져 있음", r["answer"])
        self.assertIn("5080 꺼져 있음", r["answer"])
        self.assertIn("S21 켜져 있음", r["answer"])  # 말한 기기 자신

    def test_wake_pc(self):
        r = self.say("자비스, 컴퓨터 켜")
        self.assertEqual(r["answer"], "5080 켜는 신호 보낼게요.")
        job = self.hub.store.get(r["jobs"][0])
        self.assertEqual((job["type"], job["created_by"], job["notify"]), ("wake", "s21", True))
        self.assertEqual(self.say("자비스 컴퓨터 켜")["answer"], "5080 켜는 중이에요. 조금만 기다려 주세요.")
        register_worker(self.hub, "main", ["gpu"], ["llm.chat"])
        self.assertEqual(self.say("자비스 컴퓨터 켜")["answer"], "5080은(는) 이미 켜져 있어요.")

    def test_wake_not_configured(self):
        hub = make_hub({"wake": {"enabled": False}})
        self.assertIn("설정되지 않았어요", hub.router.handle("s21", "자비스 컴퓨터 켜")["answer"])

    def test_summarize_creates_llm_job_and_wakes(self):
        r = self.say("자비스, 이 PDF 요약해줘")
        jid = r["jobs"][0]
        job = self.hub.store.get(jid)
        self.assertEqual(job["type"], "llm.chat")
        self.assertEqual(job["caps"], ["gpu"])
        self.assertEqual(job["payload"]["file"], "@latest")
        self.assertTrue(job["notify"])
        self.assertIn("PDF 요약 작업 %d번" % jid, r["answer"])
        self.assertIn("꺼져 있어서 켜는 중", r["answer"])
        self.assertEqual(len([j for j in self.hub.store.list() if j["type"] == "wake"]), 1)

    def test_summarize_file_override(self):
        r = self.say("자비스 이거 요약해줘", file="회의록.txt")
        self.assertEqual(self.hub.store.get(r["jobs"][0])["payload"]["file"], "회의록.txt")

    def test_claude_needs_approval_then_voice_approve(self):
        r = self.say("자비스, 클로드한테 README 오타 고쳐달라고 해줘")
        jid = r["jobs"][0]
        job = self.hub.store.get(jid)
        self.assertEqual(job["status"], "waiting_approval")
        self.assertEqual(job["payload"]["prompt"], "README 오타 고쳐달라고")
        self.assertIn("%d번 승인" % jid, r["answer"])
        r2 = self.say("자비스 승인해")
        self.assertEqual(r2["answer"], "%d번 승인했어요. 시작할게요." % jid)
        self.assertEqual(self.hub.store.get(jid)["status"], "queued")

    def test_approve_ambiguous_and_none(self):
        self.assertEqual(self.say("자비스 승인")["answer"], "승인할 작업이 없어요.")
        self.say("자비스 클로드한테 A 해달라고 시켜줘")
        self.say("자비스 클로드한테 B 해달라고 시켜줘")
        self.assertIn("번호로 말해 주세요", self.say("자비스 승인")["answer"])

    def test_approve_permission(self):
        j = self.hub.submit("s21", {"type": "claude.task", "payload": {"prompt": "x"}})
        r = self.hub.router.handle("guest", "자비스 %d번 승인" % j["id"])
        self.assertEqual(r["answer"], "이 기기는 승인 권한이 없어요.")

    def test_reject(self):
        r = self.say("자비스 클로드한테 뭔가 해달라고 시켜줘")
        jid = r["jobs"][0]
        self.assertEqual(self.say("자비스 %d번 취소해" % jid)["answer"], "%d번 취소했어요." % jid)
        self.assertEqual(self.hub.store.get(jid)["status"], "cancelled")

    def test_run_alias(self):
        r = self.say("자비스 백업 실행해")
        job = self.hub.store.get(r["jobs"][0])
        self.assertEqual((job["type"], job["payload"]), ("shell.allowed", {"name": "backup", "args": {}}))

    def test_permission_error_spoken(self):
        r = self.hub.router.handle("guest", "자비스 백업 실행해")
        self.assertIn("처리 실패", r["answer"])
        self.assertEqual(r["jobs"], [])

    def test_unknown_and_llm_fallback(self):
        self.assertIn("잘 못 알아들었어요", self.say("자비스 바나나 노래 불러")["answer"])
        hub = make_hub({"router": {"llm_fallback": True}})
        r = hub.router.handle("s21", "자비스 바나나 노래 불러")
        self.assertEqual(r["intents"], ["ask"])
        self.assertEqual(hub.store.get(r["jobs"][0])["payload"]["prompt"], "바나나 노래 불러")

    def test_time_and_help(self):
        self.assertRegex(self.say("자비스 지금 몇 시야")["answer"], r"^지금 오[전후] \d+시 \d+분이에요\.$")
        self.assertIn("할 수 있는 것", self.say("자비스 뭐 할 수 있어")["answer"])


class ScheduleTest(unittest.TestCase):
    def test_ics(self):
        from jarvis import schedule
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "cal.ics")
            with open(p, "w", encoding="utf-8") as f:
                f.write("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;TZID=Asia/Seoul:20261102T140000\r\n"
                        "SUMMARY:치과\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261102\r\n"
                        "SUMMARY:엄마 생신\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART:20261105T090000Z\r\n"
                        "SUMMARY:다른 날\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
            ev = schedule.today_events(p, datetime.date(2026, 11, 2))
        self.assertEqual([(e["time"], e["title"]) for e in ev], [("14:00", "치과"), ("", "엄마 생신")])
        self.assertEqual(schedule.describe(ev), "오늘 일정 2개: 14:00 치과, 엄마 생신.")
        self.assertEqual(schedule.describe([]), "오늘 일정은 없어요.")


if __name__ == "__main__":
    unittest.main()
