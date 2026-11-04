"""시나리오 4: jarvis-hub 허브 + 워커, 허용 목록 명령(shell.allowed) → 완료 → notify.say 작업
→ 워커가 진짜 s21-sidekick /api/say 로 전달 → sidekick 이벤트 큐에 say.

덤: (a) morning-brief jarvis 어댑터가 같은 허브의 /api/status 를 읽어 jobs-done 카드,
    (b) 승인이 필요한 작업이 phone-home 책상에 뜨고 책상 승인으로 진행되는지.
"""
import json
import sys
import unittest

from homelabe2e import services as S
from homelabe2e.case import E2ECase
from homelabe2e.harness import have, http_json, import_from, wait_until


class JarvisJobToSidekick(E2ECase):
    needs = ("jarvis-hub", "s21-sidekick")

    def setUp(self):
        super().setUp()
        st = self.stack
        self.sk = S.sidekick(st)
        self.hub = S.jarvis_hub(st, extra={"approval": {"required_types": ["claude.task", "shell.allowed"]}})
        handlers = {
            "shell.allowed": {"commands": {
                "hello": {"argv": [sys.executable, "-c", "print('e2e-hello')"], "timeout": 20}}},
            "notify.say": {"enabled": True, "sidekick_url": self.sk.url, "token": self.sk.token, "path": "/api/say",
                           "timeout": 5},
        }
        self.worker = S.jarvis_worker(st, self.hub, handlers)

    def job(self, jid, device="cli"):
        code, data = http_json("GET", self.hub.url + "/api/jobs/%d" % jid, headers=S.jarvis_headers(self.hub, device))
        self.assertEqual(code, 200, data)
        return data.get("job", data)

    def wait_job(self, jid, status="done", timeout=30):
        try:
            return wait_until(lambda: (lambda j: j if j["status"] == status else None)(self.job(jid)), timeout, 0.2,
                              "작업 #%d %s" % (jid, status))
        except AssertionError as e:
            raise AssertionError("%s\n작업: %s\n워커 로그:\n%s" % (e, self.job(jid), self.worker.log()))

    def test_cli_submit_runs_and_phone_hears_it(self):
        st = self.stack
        client = st.write_json("jarvis/client.json", {"hub": self.hub.url, "device": "cli",
                                                      "token": self.hub.extra["tokens"]["cli"]})
        p = self.run_cli("jarvis-hub", ["submit", "shell.allowed", "--payload", json.dumps({"name": "hello"}),
                                        "--notify", "--title", "인사", "--config", client])
        job = json.loads(p.stdout)
        job = job.get("job", job)
        done = self.wait_job(int(job["id"]))
        self.assertEqual(done["result"]["returncode"], 0, done)
        self.assertIn("e2e-hello", done["result"]["stdout"])
        ev = S.wait_sidekick_say(self.sk, lambda e: e.get("module") == "jarvis", timeout=30,
                                 what="jarvis 완료 알림이 sidekick 에 도착")
        self.assertIn("작업 %s번" % job["id"], ev["text"])
        self.assertIn("인사", ev["text"])
        # notify.say 작업 자체도 delivered=True 로 끝나야 함
        code, data = http_json("GET", self.hub.url + "/api/jobs?status=done&limit=50",
                               headers=S.jarvis_headers(self.hub))
        says = [j for j in data["jobs"] if j["type"] == "notify.say"]
        self.assertTrue(says, data)
        self.assertTrue(self.job(says[0]["id"])["result"].get("delivered"), says)

        # morning-brief jarvis 어댑터: 같은 허브에서 완료 카드
        cfg = self.mbrief_config(jarvis={"url": self.hub.url, "device": "cli",
                                         "token": self.hub.extra["tokens"]["cli"], "token_env": ""})
        self.run_cli("morning-brief", ["run", "--config", cfg, "--date", self.today.isoformat()])
        out = cfg.parent / "out"
        brief, cards = self.load_json(out / "brief-latest.json"), self.load_json(out / "cards-latest.json")
        contract = import_from("morning-brief", "mbrief.contract")
        self.assertEqual(contract.validate_brief(brief) + contract.validate_cards(cards), [])
        self.assertEqual(brief["sources"]["jarvis"]["status"], "ok", brief["sources"])
        done_cards = [c for c in cards if c["kind"] == "jobs-done"]
        self.assertTrue(done_cards, cards)
        self.assertIn("인사", done_cards[0]["detail"])

    def test_phone_submitted_job_waits_for_desk_approval(self):
        if have("phone-home"):
            self.skipTest("phone-home 없음")
        st = self.stack
        code, r = http_json("POST", self.hub.url + "/api/jobs",
                            {"type": "shell.allowed", "payload": {"name": "hello"}, "notify": True, "title": "폰 작업"},
                            S.jarvis_headers(self.hub, "s21"))
        self.assertIn(code, (200, 201), r)
        jid = int(r.get("job", r)["id"])
        self.assertEqual(self.job(jid)["status"], "waiting_approval")
        ph = S.phone_home(st, {"jarvis": {"url": self.hub.url, "token": self.hub.extra["tokens"]["s21"],
                                          "headers": {"X-Jarvis-Device": "s21"}}},
                          st.path("cards-latest.json"), desk={"approvals": {"jarvis": "jarvis"}})
        H = {"X-Phone-Token": ph.token}
        code, snap = http_json("GET", ph.url + "/desk/api/items", headers=H)
        self.assertEqual(code, 200, snap)
        self.assertEqual(snap["errors"], {})
        mine = [a for a in snap["approvals"] if a["id"] == str(jid)]
        self.assertTrue(mine and not mine[0]["link_only"], snap)
        code, r = http_json("POST", ph.url + "/desk/api/decide", {"source": "jarvis", "id": str(jid),
                                                                  "decision": "allow"}, H)
        self.assertEqual(code, 200, r)
        self.wait_job(jid)
        S.wait_sidekick_say(self.sk, lambda e: "작업 %d번" % jid in e["text"], timeout=30)


if __name__ == "__main__":
    unittest.main()
