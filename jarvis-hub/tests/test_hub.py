import unittest

from helpers import FakeClock, make_hub, register_worker
from jarvis.hub import ApiError


class PresenceTest(unittest.TestCase):
    def test_heartbeat_online_offline(self):
        clock = FakeClock()
        hub = make_hub(clock=clock)
        devs = {d["id"]: d for d in hub.device_list()}
        self.assertFalse(devs["main"]["online"])
        hub.heartbeat("main", {"caps": ["gpu", "llm"], "types": ["llm.chat"], "jobs": []})
        devs = {d["id"]: d for d in hub.device_list()}
        self.assertTrue(devs["main"]["online"])
        self.assertEqual(devs["main"]["types"], ["llm.chat"])
        self.assertEqual(hub.store.recent_events(1)[0]["kind"], "device.online")
        clock.advance(20)
        self.assertTrue({d["id"]: d for d in hub.device_list()}["main"]["online"])
        clock.advance(15)  # 35s > 30s
        self.assertFalse({d["id"]: d for d in hub.device_list()}["main"]["online"])
        hub.tick()
        kinds = [e["kind"] for e in hub.store.recent_events(5)]
        self.assertIn("device.offline", kinds)
        # 다시 하트비트 -> 다시 online 이벤트
        hub.heartbeat("main", {"jobs": []})
        self.assertEqual(hub.store.recent_events(1)[0]["kind"], "device.online")

    def test_worker_caps_restricted_by_config(self):
        hub = make_hub()
        with self.assertRaises(ApiError) as cm:
            hub.lease("minipc", {"caps": ["gpu"], "types": ["wake"]})
        self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(ApiError) as cm:
            hub.lease("s21", {"caps": [], "types": ["llm.chat"]})  # 워커 아님
        self.assertEqual(cm.exception.code, 403)

    def test_heartbeat_extends_lease(self):
        clock = FakeClock()
        hub = make_hub(clock=clock)
        j = hub.submit("main", {"type": "test.echo", "lease_seconds": 60})
        hub.lease("main", {"caps": ["test"], "types": ["test.echo"]})
        clock.advance(50)
        self.assertEqual(hub.heartbeat("main", {"jobs": [j["id"]]})["extended"], [j["id"]])
        clock.advance(50)
        hub.tick()
        self.assertEqual(hub.store.get(j["id"])["status"], "leased")


class SubmitTest(unittest.TestCase):
    def setUp(self):
        self.hub = make_hub()

    def test_validation(self):
        bad = [{"type": "Bad Type"}, {"type": "x", "payload": []}, {"type": "x", "caps": ["GPU!"]},
               {"type": "x", "priority": "high"}, {}]
        for body in bad:
            with self.assertRaises(ApiError, msg=body) as cm:
                self.hub.submit("s21", body)
            self.assertEqual(cm.exception.code, 400)

    def test_payload_size_limit(self):
        with self.assertRaises(ApiError) as cm:
            self.hub.submit("s21", {"type": "llm.chat", "payload": {"prompt": "가" * 70000}})
        self.assertEqual(cm.exception.code, 413)

    def test_submit_permission_globs(self):
        self.hub.submit("guest", {"type": "llm.chat"})
        with self.assertRaises(ApiError) as cm:
            self.hub.submit("guest", {"type": "shell.allowed", "payload": {"name": "backup"}})
        self.assertEqual(cm.exception.code, 403)

    def test_clamps(self):
        j = self.hub.submit("s21", {"type": "llm.chat", "priority": 999, "max_attempts": 99, "lease_seconds": 1})
        self.assertEqual((j["priority"], j["max_attempts"], j["lease_timeout"]), (100, 10, 5.0))

    def test_max_active(self):
        hub = make_hub({"queue": {"max_active_jobs": 2}})
        hub.submit("s21", {"type": "llm.chat"})
        hub.submit("s21", {"type": "llm.chat"})
        with self.assertRaises(ApiError) as cm:
            hub.submit("s21", {"type": "llm.chat"})
        self.assertEqual(cm.exception.code, 429)

    def test_approval_flow(self):
        j = self.hub.submit("s21", {"type": "claude.task", "payload": {"prompt": "hi"}})
        self.assertEqual(j["status"], "waiting_approval")
        trusted = self.hub.submit("main", {"type": "claude.task", "payload": {"prompt": "hi"}})
        self.assertEqual(trusted["status"], "queued")
        with self.assertRaises(ApiError):
            self.hub.approve("guest", j["id"])
        self.assertEqual(self.hub.approve("s21", j["id"])["status"], "queued")

    def test_reject_by_creator(self):
        j = self.hub.submit("s21", {"type": "claude.task", "payload": {"prompt": "hi"}})
        self.assertEqual(self.hub.reject("s21", j["id"])["status"], "cancelled")
        g = self.hub.submit("guest", {"type": "llm.chat"})
        with self.assertRaises(ApiError):
            self.hub.reject("main", g["id"])  # main은 승인권한 없음, 만든 사람도 아님


class WakeTest(unittest.TestCase):
    def wake_jobs(self, hub):
        return [j for j in hub.store.list() if j["type"] == "wake"]

    def test_gpu_job_without_gpu_worker_requests_wol(self):
        hub = make_hub()
        hub.submit("s21", {"type": "llm.chat", "caps": ["gpu"]})
        w = self.wake_jobs(hub)
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["payload"]["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(w[0]["payload"]["broadcast"], "192.168.0.255")
        self.assertEqual(w[0]["caps"], [])
        self.assertEqual(w[0]["created_by"], "hub")
        # 두 번째 gpu 작업: 이미 wake 작업이 있으니 또 만들지 않는다
        hub.submit("s21", {"type": "llm.chat", "caps": ["gpu"]})
        self.assertEqual(len(self.wake_jobs(hub)), 1)

    def test_no_wol_when_gpu_worker_online(self):
        hub = make_hub()
        register_worker(hub, "main", ["gpu", "llm"], ["llm.chat"])
        hub.submit("s21", {"type": "llm.chat", "caps": ["gpu"]})
        self.assertEqual(self.wake_jobs(hub), [])

    def test_no_wol_for_non_gpu_jobs(self):
        hub = make_hub()
        hub.submit("s21", {"type": "llm.chat", "caps": []})
        self.assertEqual(self.wake_jobs(hub), [])

    def test_cooldown(self):
        clock = FakeClock()
        hub = make_hub(clock=clock)
        hub.submit("s21", {"type": "llm.chat", "caps": ["gpu"]})
        wj = self.wake_jobs(hub)[0]
        hub.lease("minipc", {"caps": ["lan"], "types": ["wake"]})
        hub.complete("minipc", wj["id"], {"result": {"sent_bytes": 306}})
        clock.advance(60)
        hub.tick()  # gpu 작업은 아직 대기, 하지만 쿨다운(300s) 안
        self.assertEqual(len(self.wake_jobs(hub)), 1)
        clock.advance(300)
        hub.tick()
        self.assertEqual(len(self.wake_jobs(hub)), 2)

    def test_wake_disabled(self):
        hub = make_hub({"wake": {"enabled": False}})
        hub.submit("s21", {"type": "llm.chat", "caps": ["gpu"]})
        self.assertEqual(self.wake_jobs(hub), [])


class NotifyTest(unittest.TestCase):
    def test_done_with_notify_pushes_ntfy_say_event_and_say_job(self):
        hub = make_hub()
        register_worker(hub, "main", ["gpu"], ["llm.chat"])
        j = hub.submit("s21", {"type": "llm.chat", "caps": ["gpu"], "notify": True, "title": "문서 요약"})
        hub.lease("main", {"caps": ["gpu"], "types": ["llm.chat"]})
        hub.complete("main", j["id"], {"result": {"text": "요약 결과입니다", "summary": "요약 결과입니다"}})
        self.assertEqual(len(hub.ntfy.pushed), 1)
        self.assertIn("요약 결과입니다", hub.ntfy.pushed[0][1])
        says = [e for e in hub.store.recent_events(10) if e["kind"] == "say"]
        self.assertEqual(len(says), 1)
        self.assertIn("%d번" % j["id"], says[0]["text"])
        sj = [x for x in hub.store.list() if x["type"] == "notify.say"]
        self.assertEqual(len(sj), 1)
        self.assertEqual(sj[0]["payload"]["text"], says[0]["text"])
        # notify.say 작업이 끝나도 또 알림을 만들지 않는다 (무한루프 방지)
        hub.lease("minipc", {"caps": ["speaker"], "types": ["notify.say"]})
        hub.complete("minipc", sj[0]["id"], {"result": {"delivered": True}})
        self.assertEqual(len([x for x in hub.store.list() if x["type"] == "notify.say"]), 1)

    def test_unhandled_say_jobs_expire_and_are_not_counted(self):
        clock = FakeClock()
        hub = make_hub(clock=clock)
        j = hub.submit("s21", {"type": "llm.chat", "notify": True})
        hub.lease("main", {"caps": [], "types": ["llm.chat"]})
        hub.complete("main", j["id"], {"result": {"text": "ok"}})
        self.assertEqual(hub.router.status_line(), "진행 중인 작업 없음, 5080 켜져 있음.")
        clock.advance(601)
        hub.tick()
        sj = [x for x in hub.store.list() if x["type"] == "notify.say"][0]
        self.assertEqual(sj["status"], "cancelled")

    def test_no_notify_flag_no_push(self):
        hub = make_hub()
        j = hub.submit("s21", {"type": "llm.chat"})
        hub.lease("main", {"caps": [], "types": ["llm.chat"]})
        hub.complete("main", j["id"], {})
        self.assertEqual(hub.ntfy.pushed, [])
        self.assertEqual([e["kind"] for e in hub.store.recent_events(1)], ["job.done"])

    def test_final_failure_notifies(self):
        hub = make_hub()
        j = hub.submit("s21", {"type": "llm.chat", "notify": True, "max_attempts": 1})
        hub.lease("main", {"caps": [], "types": ["llm.chat"]})
        hub.fail("main", j["id"], {"error": "모델 없음"})
        self.assertIn("실패", hub.ntfy.pushed[0][1])

    def test_result_size_limit(self):
        hub = make_hub()
        j = hub.submit("s21", {"type": "llm.chat"})
        hub.lease("main", {"caps": [], "types": ["llm.chat"]})
        with self.assertRaises(ApiError) as cm:
            hub.complete("main", j["id"], {"result": {"text": "x" * 300000}})
        self.assertEqual(cm.exception.code, 413)


class NtfyTest(unittest.TestCase):
    def test_request_shape(self):
        import json
        from jarvis.notifier import Ntfy
        sent = []
        n = Ntfy({"enabled": True, "server": "https://ntfy.example", "topic": "t1", "token": "abc", "priority": 3},
                 spawn=lambda fn, *a: fn(*a), opener=lambda req, timeout: sent.append(req) or _Resp())
        self.assertTrue(n.push("자비스", "작업 끝났어요"))
        req = sent[0]
        self.assertEqual(req.full_url, "https://ntfy.example/")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual((body["topic"], body["title"], body["message"]), ("t1", "자비스", "작업 끝났어요"))
        self.assertEqual(req.get_header("Authorization"), "Bearer abc")
        self.assertFalse(Ntfy({"enabled": False}).push("a", "b"))


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self):
        return b"{}"


if __name__ == "__main__":
    unittest.main()
