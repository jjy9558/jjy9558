import unittest

from helpers import FakeClock
from jarvis.store import Conflict, NotFound, Store, caps_match


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.s = Store(":memory:", clock=self.clock)

    def sub(self, type="llm.chat", caps=(), priority=0, **kw):
        return self.s.submit(type, {"x": 1}, caps, priority, "s21", **kw)

    def test_caps_match(self):
        self.assertTrue(caps_match([], ["gpu"]))
        self.assertTrue(caps_match(["gpu", "llm"], ["gpu", "llm", "windows"]))
        self.assertFalse(caps_match(["gpu", "stt"], ["gpu", "llm"]))

    def test_submit_fields(self):
        j = self.sub(caps=["llm", "gpu", "gpu"], priority=5, notify=True, title="t")
        self.assertEqual(j["status"], "queued")
        self.assertEqual(j["caps"], ["gpu", "llm"])
        self.assertEqual(j["payload"], {"x": 1})
        self.assertEqual(j["created_by"], "s21")
        self.assertTrue(j["notify"])
        self.assertEqual(j["attempts"], 0)

    def test_lease_requires_caps_and_type(self):
        j = self.sub(caps=["gpu"])
        self.assertIsNone(self.s.lease("minipc", ["lan"], ["llm.chat"]))
        self.assertIsNone(self.s.lease("main", ["gpu"], ["shell.allowed"]))
        got = self.s.lease("main", ["gpu", "llm"], ["llm.chat"])
        self.assertEqual(got["id"], j["id"])
        self.assertEqual(got["status"], "leased")
        self.assertEqual(got["lease_owner"], "main")
        self.assertEqual(got["attempts"], 1)
        self.assertIsNone(self.s.lease("main", ["gpu"], ["llm.chat"]))  # 이미 빌려감

    def test_priority_then_fifo(self):
        a = self.sub(priority=0)
        b = self.sub(priority=10)
        c = self.sub(priority=10)
        d = self.sub(priority=-5)
        order = [self.s.lease("w", [], ["llm.chat"])["id"] for _ in range(4)]
        self.assertEqual(order, [b["id"], c["id"], a["id"], d["id"]])

    def test_lease_skips_unmatched_high_priority(self):
        self.sub(caps=["gpu"], priority=50)
        low = self.sub(caps=[], priority=0)
        self.assertEqual(self.s.lease("minipc", ["lan"], ["llm.chat"])["id"], low["id"])

    def test_waiting_approval_not_leased(self):
        self.sub(needs_approval=True)
        self.assertIsNone(self.s.lease("w", [], ["llm.chat"]))

    def test_complete_only_by_owner(self):
        j = self.sub()
        self.s.lease("main", [], ["llm.chat"])
        with self.assertRaises(Conflict):
            self.s.complete(j["id"], "minipc", {})
        done = self.s.complete(j["id"], "main", {"text": "ok"})
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"], {"text": "ok"})
        self.assertIsNotNone(done["finished_at"])
        with self.assertRaises(Conflict):
            self.s.complete(j["id"], "main", {})
        with self.assertRaises(NotFound):
            self.s.get(999)

    def test_fail_retry_until_max_attempts(self):
        j = self.sub(max_attempts=2)
        self.s.lease("w", [], ["llm.chat"])
        job, final = self.s.fail(j["id"], "w", "boom", retry=True)
        self.assertFalse(final)
        self.assertEqual(job["status"], "queued")
        self.s.lease("w", [], ["llm.chat"])
        job, final = self.s.fail(j["id"], "w", "boom2", retry=True)
        self.assertTrue(final)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "boom2")
        self.assertEqual(job["attempts"], 2)

    def test_fail_no_retry(self):
        j = self.sub(max_attempts=5)
        self.s.lease("w", [], ["llm.chat"])
        job, final = self.s.fail(j["id"], "w", "nope", retry=False)
        self.assertTrue(final)
        self.assertEqual(job["status"], "failed")

    def test_retry_delay(self):
        j = self.sub(max_attempts=3)
        self.s.lease("w", [], ["llm.chat"])
        self.s.fail(j["id"], "w", "x", retry=True, retry_delay=10)
        self.assertIsNone(self.s.lease("w", [], ["llm.chat"]))
        self.clock.advance(11)
        self.assertEqual(self.s.lease("w", [], ["llm.chat"])["id"], j["id"])

    def test_lease_expiry_requeue_then_fail(self):
        j = self.sub(max_attempts=2, lease_timeout=60)
        self.s.lease("w1", [], ["llm.chat"])
        self.clock.advance(30)
        self.assertEqual(self.s.reap(), [])
        self.clock.advance(31)
        out = self.s.reap()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0]["status"], "queued")
        self.assertFalse(out[0][1])
        # 다른 워커가 이어받음
        self.assertEqual(self.s.lease("w2", [], ["llm.chat"])["lease_owner"], "w2")
        # 늦게 끝낸 원래 워커는 거부
        with self.assertRaises(Conflict):
            self.s.complete(j["id"], "w1", {})
        self.clock.advance(61)
        out = self.s.reap()
        self.assertEqual(out[0][0]["status"], "failed")
        self.assertTrue(out[0][1])
        self.assertIn("임대 시간 초과", out[0][0]["error"])

    def test_extend_lease(self):
        j = self.sub(lease_timeout=60)
        self.s.lease("w", [], ["llm.chat"])
        self.clock.advance(50)
        self.assertEqual(self.s.extend("w", [j["id"]]), [j["id"]])
        self.assertEqual(self.s.extend("other", [j["id"]]), [])
        self.clock.advance(50)
        self.assertEqual(self.s.reap(), [])
        self.assertEqual(self.s.get(j["id"])["status"], "leased")

    def test_set_status_and_counts(self):
        j = self.sub(needs_approval=True)
        self.assertEqual(self.s.counts()["waiting_approval"], 1)
        self.s.set_status(j["id"], ("waiting_approval",), "queued")
        with self.assertRaises(Conflict):
            self.s.set_status(j["id"], ("waiting_approval",), "queued")
        c = self.s.counts()
        self.assertEqual((c["queued"], c["waiting_approval"]), (1, 0))

    def test_wait_lease_wakes_on_submit(self):
        import threading
        import time
        got = {}

        def waiter():
            got["job"] = self.s.wait_lease("w", [], ["llm.chat"], 5)
        t = threading.Thread(target=waiter)
        start = time.monotonic()
        t.start()
        time.sleep(0.1)
        j = self.sub()
        t.join(3)
        self.assertEqual(got["job"]["id"], j["id"])
        self.assertLess(time.monotonic() - start, 2.5)

    def test_wait_lease_timeout(self):
        self.assertIsNone(self.s.wait_lease("w", [], ["llm.chat"], 0.2))

    def test_events(self):
        self.s.add_event("a", "one")
        e2 = self.s.add_event("b", "two", job=3)
        self.assertEqual([e["kind"] for e in self.s.events_since(0)], ["a", "b"])
        self.assertEqual(self.s.events_since(e2["id"] - 1)[0]["data"], {"job": 3})
        self.assertEqual(self.s.events_since(e2["id"], wait=0.1), [])
        self.assertEqual(self.s.last_event_id(), e2["id"])


if __name__ == "__main__":
    unittest.main()
