"""허브 HTTP 서버 + 워커 스레드를 localhost 포트 0에서 실제로 띄우는 종단 간 테스트."""
import json
import threading
import time
import unittest
import urllib.request

from helpers import TOKENS, ServerThread, make_hub
from jarvis.client import HubClient, HubError
from jarvis.config import build_worker_config
from jarvis.handlers import HandlerError, PermanentError
from jarvis.worker import Worker


class EchoHandler:
    type = "test.echo"

    def __init__(self):
        self.seen = []

    def run(self, job):
        self.seen.append(job["id"])
        p = job["payload"]
        if p.get("mode") == "permanent":
            raise PermanentError("bad request")
        if p.get("mode") == "flaky" and len(self.seen) < 2:
            raise HandlerError("temporary")
        return {"echo": p.get("msg"), "summary": "echo %s" % p.get("msg")}


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("시간 초과")


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.hub = make_hub({"queue": {"retry_delay_seconds": 0}}, clock=time.time)
        self.server = ServerThread(self.hub).__enter__()
        self.url = self.server.url
        self.s21 = HubClient(self.url, "s21", TOKENS["s21"])
        self.workers = []

    def tearDown(self):
        for w in self.workers:
            w.stop.set()
        self.server.__exit__()

    def start_worker(self, device="main", caps=("test",), handlers=None):
        cfg = build_worker_config({"device": device, "token": TOKENS[device], "hub": self.url, "caps": list(caps),
                                   "lease_wait_seconds": 1, "heartbeat_seconds": 0.2, "retry_backoff_seconds": 0.1})
        handlers = handlers or {"test.echo": EchoHandler()}
        w = Worker(cfg, HubClient(self.url, device, TOKENS[device]), handlers)
        self.workers.append(w)
        threading.Thread(target=w.run_forever, daemon=True).start()
        return w, handlers

    def raw(self, path, body=None, headers=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method or ("POST" if data else "GET"))
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    # ---- 인증 ----
    def test_auth(self):
        self.assertEqual(self.raw("/api/status")[0], 401)
        self.assertEqual(self.raw("/api/status", headers={"X-Jarvis-Device": "s21", "X-Jarvis-Token": "wrong"})[0], 401)
        self.assertEqual(self.raw("/api/status", headers={"X-Jarvis-Device": "evil",
                                                           "X-Jarvis-Token": TOKENS["s21"]})[0], 401)
        # 다른 기기의 토큰으로 사칭 불가
        self.assertEqual(self.raw("/api/status", headers={"X-Jarvis-Device": "main",
                                                           "X-Jarvis-Token": TOKENS["s21"]})[0], 401)
        code, _ = self.raw("/api/status", headers={"Authorization": "Bearer s21:" + TOKENS["s21"]})
        self.assertEqual(code, 200)
        self.assertEqual(self.raw("/api/ping")[0], 200)

    def test_size_limit_and_bad_json(self):
        h = {"X-Jarvis-Device": "s21", "X-Jarvis-Token": TOKENS["s21"], "Content-Type": "application/json"}
        big = {"type": "llm.chat", "payload": {"x": "a" * 300000}}
        self.assertEqual(self.raw("/api/jobs", big, h)[0], 413)
        req = urllib.request.Request(self.url + "/api/jobs", data=b"{not json", method="POST")
        for k, v in h.items():
            req.add_header(k, v)
        try:
            urllib.request.urlopen(req, timeout=5)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 400)

    def test_dashboard_served(self):
        code, body = self.raw("/")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn("<title>자비스 허브</title>", html)
        self.assertIn("/api/status", html)
        self.assertNotIn(TOKENS["s21"], html)
        self.assertEqual(self.raw("/nope")[0], 404)

    # ---- 작업 흐름 ----
    def test_submit_lease_complete_roundtrip(self):
        w, handlers = self.start_worker()
        job = self.s21.submit("test.echo", {"msg": "안녕"}, caps=["test"], notify=True, title="에코")
        self.assertEqual(job["status"], "queued")
        done = wait_for(lambda: (lambda j: j if j["status"] == "done" else None)(self.s21.job(job["id"])))
        self.assertEqual(done["result"]["echo"], "안녕")
        self.assertEqual(done["lease_owner"], None)
        # 완료 알림: say 이벤트 + notify.say 작업
        ev = self.s21.events(0)["events"]
        kinds = [e["kind"] for e in ev]
        for k in ("job.submitted", "job.leased", "job.done", "say", "device.online"):
            self.assertIn(k, kinds)
        say = [e for e in ev if e["kind"] == "say"][0]
        self.assertIn("echo 안녕", say["text"])
        self.assertTrue(any(j["type"] == "notify.say" for j in self.s21.jobs()))
        devs = {d["id"]: d for d in self.s21.status()["devices"]}
        self.assertTrue(devs["main"]["online"])
        self.assertEqual(devs["main"]["types"], ["test.echo"])

    def test_caps_routing_between_workers(self):
        a_handlers = {"test.echo": EchoHandler()}
        b_handlers = {"test.echo": EchoHandler()}
        self.start_worker("minipc", ("lan",), a_handlers)
        self.start_worker("main", ("test", "gpu"), b_handlers)
        j1 = self.s21.submit("test.echo", {"msg": 1}, caps=["gpu"])
        j2 = self.s21.submit("test.echo", {"msg": 2}, caps=["lan"])
        wait_for(lambda: all(self.s21.job(j)["status"] == "done" for j in (j1["id"], j2["id"])))
        self.assertEqual(b_handlers["test.echo"].seen, [j1["id"]])
        self.assertEqual(a_handlers["test.echo"].seen, [j2["id"]])

    def test_retry_then_success_and_permanent_failure(self):
        w, handlers = self.start_worker()
        j = self.s21.submit("test.echo", {"mode": "flaky", "msg": "x"}, caps=["test"])
        done = wait_for(lambda: (lambda x: x if x["status"] == "done" else None)(self.s21.job(j["id"])))
        self.assertEqual(done["attempts"], 2)
        p = self.s21.submit("test.echo", {"mode": "permanent"}, caps=["test"], max_attempts=5)
        failed = wait_for(lambda: (lambda x: x if x["status"] == "failed" else None)(self.s21.job(p["id"])))
        self.assertEqual(failed["attempts"], 1)
        self.assertEqual(failed["error"], "bad request")

    def test_long_poll_lease(self):
        main = HubClient(self.url, "main", TOKENS["main"])
        got = {}
        t = threading.Thread(target=lambda: got.update(job=main.lease(["test"], ["test.echo"], wait=5)))
        start = time.monotonic()
        t.start()
        time.sleep(0.2)
        j = self.s21.submit("test.echo", {"msg": "lp"})
        t.join(5)
        self.assertEqual(got["job"]["id"], j["id"])
        self.assertLess(time.monotonic() - start, 3)
        # 다른 기기가 complete 하면 409
        with self.assertRaises(HubError) as cm:
            HubClient(self.url, "minipc", TOKENS["minipc"]).complete(j["id"], {})
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(main.complete(j["id"], {"ok": 1})["status"], "done")

    def test_non_worker_cannot_lease_and_caps_enforced(self):
        with self.assertRaises(HubError) as cm:
            self.s21.lease([], ["test.echo"], wait=0)
        self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(HubError) as cm:
            HubClient(self.url, "minipc", TOKENS["minipc"]).lease(["gpu"], ["llm.chat"], wait=0)
        self.assertEqual(cm.exception.code, 403)

    def test_say_endpoint_and_approval_over_http(self):
        r = self.s21.say("자비스, 클로드한테 테스트 고쳐달라고 시켜줘")
        jid = r["jobs"][0]
        self.assertEqual(self.s21.job(jid)["status"], "waiting_approval")
        r = self.s21.say("자비스, 작업 상태 알려줘")
        self.assertIn("1개 승인 대기", r["answer"])
        self.assertTrue(self.s21.say("작업 상태")["ignored"])
        self.assertEqual(self.s21.approve(jid)["status"], "queued")
        with self.assertRaises(HubError) as cm:
            HubClient(self.url, "guest", TOKENS["guest"]).approve(jid)
        self.assertEqual(cm.exception.code, 403)

    def test_wake_flow_over_http(self):
        sent = []

        class FakeWake:
            type = "wake"

            def run(self, job):
                sent.append(job["payload"]["mac"])
                return {"sent_bytes": 102, "summary": "켜기 신호"}
        self.start_worker("minipc", ("lan",), {"wake": FakeWake()})
        r = self.s21.say("자비스 이 PDF 요약해줘")
        self.assertIn("켜는 중", r["answer"])
        wait_for(lambda: sent)
        self.assertEqual(sent, ["AA:BB:CC:DD:EE:FF"])
        summary = self.s21.job(r["jobs"][0])
        self.assertEqual(summary["status"], "queued")  # 5080 워커가 아직 없음

    def test_events_long_poll(self):
        last = self.s21.events(0)["last_id"]
        got = {}
        t = threading.Thread(target=lambda: got.update(r=self.s21.events(last, wait=5)))
        t.start()
        time.sleep(0.2)
        self.s21.submit("test.echo", {})
        t.join(5)
        self.assertEqual(got["r"]["events"][0]["kind"], "job.submitted")


class CliSayTest(unittest.TestCase):
    def test_cli_say(self):
        import contextlib
        import io
        from jarvis.cli import main
        hub = make_hub()
        with ServerThread(hub) as srv:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main(["say", "--hub", srv.url, "--device", "s21", "--token", TOKENS["s21"], "--config", "/nonexistent.json",
                           "자비스,", "작업", "상태", "알려줘"])
            self.assertEqual(rc, 0)
            self.assertIn("진행 중인 작업 없음", out.getvalue())


if __name__ == "__main__":
    unittest.main()
