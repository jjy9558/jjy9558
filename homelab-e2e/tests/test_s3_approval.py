"""시나리오 3: s21-sidekick 서버 + phone-home 프록시.

- 런처 건강 점: 살아 있음(초록) / 토큰 거절(노랑) / 꺼짐(빨강)
- /s/sidekick/api/ping 프록시 (클라이언트 토큰은 뒤로 안 넘어가고 서버가 sidekick 토큰을 붙임)
- sidekick 훅 스크립트(hooks/claude_pretooluse.py)가 위험 명령에서 결재를 올림 → /desk 에 보임
  → 책상에서 승인 → 훅이 allow JSON 을 출력. rm -rf 는 책상에서 버튼 없이 링크만(403).
"""
import json
import subprocess
import unittest

from homelabe2e import services as S
from homelabe2e.case import E2ECase
from homelabe2e.harness import free_port, http, http_json, wait_until

HOOK = "hooks/claude_pretooluse.py"


def hook_input(command: str) -> bytes:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": command}, "cwd": "/tmp/project"}).encode("utf-8")


class SidekickBehindPhoneHome(E2ECase):
    needs = ("s21-sidekick", "phone-home", "object-memory")

    def setUp(self):
        super().setUp()
        st = self.stack
        self.sk = S.sidekick(st)
        self.om = S.object_memory(st, st.fake_llm().url)
        services = {
            "sidekick": {"url": self.sk.url, "token": self.sk.token},
            # 일부러 틀린 토큰: 노란 점이 나와야 함
            "object-memory": {"url": self.om.url, "token": "wrong-" + self.om.token[:6]},
            # 아무도 안 듣는 포트: 빨간 점
            "jarvis": {"url": "http://127.0.0.1:%d" % free_port(), "token": "x" * 20},
        }
        self.ph = S.phone_home(st, services, st.path("cards-latest.json"),
                               desk={"approvals": {"sidekick": "sidekick"}})
        self.H = {"X-Phone-Token": self.ph.token}

    def hook(self, command: str) -> subprocess.Popen:
        # 훅 입력은 파일로 넘긴다 (닫힌 stdin 파이프 + communicate 는 3.12 에서 ValueError)
        src = self.stack.path("hook-input-%d.json" % len(self.stack.services))
        src.write_bytes(hook_input(command))
        fh = open(src, "rb")
        self.addCleanup(fh.close)
        return self.stack.popen("s21-sidekick", ["--config", str(self.sk.config)], script=HOOK, stdin=fh)

    def pending(self, needle: str):
        code, data = http_json("GET", self.sk.url + "/api/approval/pending",
                               headers={"X-Sidekick-Token": self.sk.token})
        self.assertEqual(code, 200, data)
        return next((x for x in data["pending"] if needle in x["command"]), None)

    def test_launcher_health_and_proxy(self):
        code, data = http_json("GET", self.ph.url + "/api/health", headers=self.H)
        self.assertEqual(code, 200, data)
        sv = data["services"]
        self.assertEqual(sv["sidekick"]["state"], "up", sv)
        self.assertEqual(sv["object-memory"]["state"], "auth", sv)
        self.assertEqual(sv["jarvis"]["state"], "down", sv)
        self.assertEqual(http_json("GET", self.ph.url + "/api/health")[0], 401)
        # 런처 페이지에 타일
        code, _, page = http("GET", self.ph.url + "/", headers=self.H)
        self.assertEqual(code, 200)
        self.assertIn("사이드킥", page.decode("utf-8"))
        # 프록시: 클라이언트 토큰으로 sidekick API 를 부르면 서버가 sidekick 토큰을 붙인다
        code, ping = http_json("GET", self.ph.url + "/s/sidekick/api/ping", headers=self.H)
        self.assertEqual((code, ping), (200, {"ok": True}))
        # 클라이언트가 직접 sidekick 토큰 헤더를 넣어도 버려진다(틀린 값을 넣어도 통과 = 서버 토큰으로 교체)
        code, ping = http_json("GET", self.ph.url + "/s/sidekick/api/ping",
                               headers=dict(self.H, **{"X-Sidekick-Token": "bogus"}))
        self.assertEqual(code, 200, ping)
        # 프록시를 거친 /api/say → 진짜 이벤트 큐
        code, r = http_json("POST", self.ph.url + "/s/sidekick/api/say", {"text": "프록시 통과", "module": "e2e"},
                            self.H)
        self.assertEqual(code, 200, r)
        S.wait_sidekick_say(self.sk, lambda e: e["text"] == "프록시 통과")
        # 폰 페이지 HTML 의 /api/ 경로가 /s/sidekick/api/ 로 고쳐 쓰인다
        code, _, page = http("GET", self.ph.url + "/s/sidekick/", headers=self.H)
        self.assertEqual(code, 200)
        self.assertIn("/s/sidekick/api/", page.decode("utf-8"))
        # 경로 탈출 막힘
        self.assertEqual(http("GET", self.ph.url + "/s/sidekick/%2e%2e/api/ping", headers=self.H)[0], 404)

    def test_hook_request_appears_on_desk_and_desk_approves(self):
        cmd = "git push --force origin main"
        proc = self.hook(cmd)
        item = wait_until(lambda: self.pending("git push"), 15, 0.2, "sidekick 결재 대기")
        self.assertEqual(item["tool"], "Bash")
        # 책상 화면과 JSON 에 결재가 보인다
        code, _, page = http("GET", self.ph.url + "/desk", headers=self.H)
        self.assertEqual(code, 200)
        self.assertIn("git push --force", page.decode("utf-8"))
        code, snap = http_json("GET", self.ph.url + "/desk/api/items", headers=self.H)
        self.assertEqual(code, 200, snap)
        self.assertEqual(snap["errors"], {})
        mine = [a for a in snap["approvals"] if a["id"] == item["id"]]
        self.assertEqual(len(mine), 1, snap)
        self.assertFalse(mine[0]["link_only"])
        # 책상에서 승인 → sidekick API 로만 전달 → 훅이 allow 출력
        code, r = http_json("POST", self.ph.url + "/desk/api/decide",
                            {"source": "sidekick", "id": item["id"], "decision": "allow"}, self.H)
        self.assertEqual(code, 200, r)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err.decode("utf-8", "replace"))
        res = json.loads(out.decode("utf-8"))
        self.assertEqual(res["hookSpecificOutput"]["permissionDecision"], "allow", res)
        self.assertEqual(res["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        # 결정 기록이 desk-log 에
        log = self.stack.path("phone-home/desk-log.jsonl").read_text(encoding="utf-8")
        self.assertIn('"decision": "allow"', log)

    def test_rm_rf_is_link_only_on_desk_and_phone_denies(self):
        proc = self.hook("rm -rf ./build")
        item = wait_until(lambda: self.pending("rm -rf"), 15, 0.2, "rm -rf 결재 대기")
        code, snap = http_json("GET", self.ph.url + "/desk/api/items", headers=self.H)
        mine = [a for a in snap["approvals"] if a["id"] == item["id"]]
        self.assertTrue(mine and mine[0]["link_only"], snap)
        code, r = http_json("POST", self.ph.url + "/desk/api/decide",
                            {"source": "sidekick", "id": item["id"], "decision": "allow"}, self.H)
        self.assertEqual(code, 403, r)
        self.assertIsNotNone(self.pending("rm -rf"))      # 책상은 손대지 못했음
        # 폰(sidekick 화면)에서 반려 → 훅 exit 2
        code, r = http_json("POST", self.sk.url + "/api/approval/decide", {"id": item["id"], "decision": "deny"},
                            {"X-Sidekick-Token": self.sk.token})
        self.assertEqual(code, 200, r)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 2, (out, err))
        self.assertIn("반려", err.decode("utf-8", "replace"))

    def test_harmless_command_passes_without_server_call(self):
        proc = self.hook("ls -la")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, out.strip()), (0, b""), err)
        self.assertIsNone(self.pending("ls -la"))


if __name__ == "__main__":
    unittest.main()
