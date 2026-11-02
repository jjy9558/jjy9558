import io
import json
import os
import tempfile
import threading
import time
import unittest

from helpers import FakeMono, LiveServer, config, free_port
from sidekick import hook
from sidekick.approval import ApprovalBoard, parse_voice_decision, summarize
from sidekick.client import ServerError, ServerUnavailable, SidekickClient
from sidekick.events import EventQueue
from sidekick.server import Sidekick


def bash(cmd, **extra):
    return json.dumps(dict({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                            "tool_input": {"command": cmd, "description": "빌드 폴더 정리"}, "cwd": "/repo"}, **extra))


class PatternTests(unittest.TestCase):
    """원문 텍스트 기준 매칭. 따옴표 안(echo "rm -rf")도 위험으로 본다 — 오탐은 한 번 더 묻는 것뿐."""

    @classmethod
    def setUpClass(cls):
        cls.p = hook.compile_patterns(config()["approval"])

    def risky(self, cmd):
        return hook.match_risky(cmd, self.p)

    def test_risky(self):
        for cmd, label in [
            ("rm -rf ./build", "강제 재귀 삭제"), ("rm -fr x", "강제 재귀 삭제"), ("rm -r -f x", "강제 재귀 삭제"),
            ("sudo /bin/rm -Rf /", "강제 재귀 삭제"), ("rm --recursive x", "강제 재귀 삭제"),
            ("cd build; rm -rf *", "강제 재귀 삭제"), ("rm \\\n  -rf x", "강제 재귀 삭제"),
            ("bash -c 'rm -rf ~/x'", "강제 재귀 삭제"),
            ('echo "rm -rf"', "강제 재귀 삭제"),  # 문서화된 선택: 따옴표 안도 걸림 (bash -c / eval 을 놓치지 않으려고)
            ("git push --force", "git 강제 푸시"), ("git push -f origin main", "git 강제 푸시"),
            ("git push --force-with-lease", "git 강제 푸시"), ("git push origin +main", "git 강제 푸시"),
            ("git status && git push -uf origin x", "git 강제 푸시"),
            ("git reset --hard HEAD~3", "git reset --hard"), ("git clean -fdx", "git clean"),
            ("psql -c 'DROP TABLE users;'", "DB 테이블 삭제"), ("sqlite3 a.db 'drop database x'", "DB 테이블 삭제"),
            ("truncate table logs", "DB 테이블 삭제"),
            ("del /s /q build", "윈도우 강제 삭제"), ("rmdir /S /Q C:\\tmp", "윈도우 강제 삭제"),
            ("Remove-Item -Recurse -Force .\\dist", "PowerShell 재귀 삭제"), ("ri dist -r", "PowerShell 재귀 삭제"),
            ("find . -name '*.o' -delete", "find 삭제"), ("python -c 'import shutil; shutil.rmtree(\"x\")'", "rmtree"),
            ("mkfs.ext4 /dev/sdb1", "디스크 포맷"), ("format D: /q", "디스크 포맷"),
            ("dd if=/dev/zero of=/dev/sda bs=1M", "디스크 덮어쓰기"),
        ]:
            self.assertIn(label, self.risky(cmd), cmd)

    def test_safe(self):
        for cmd in ["rm file.txt", "rm -f old.log", "ls -R", "grep -r TODO .", "npm run rm-rf-cache", "firm -rf",
                    "rm x && ls -R", "git push", "git push -u origin feature", "git push --follow-tags",
                    "git reset --soft HEAD~1", "git clean -n", "echo dropped tables", "Remove-Item a.txt",
                    "find . -name '*.py'", "truncate -s 0 log.txt", "python -m unittest", "", "del a.txt"]:
            self.assertEqual(self.risky(cmd), [], cmd)

    def test_custom_patterns(self):
        warns = []
        p = hook.compile_patterns({"risky_patterns": [{"pattern": r"\bkubectl\s+delete\b", "label": "k8s 삭제"}],
                                   "extra_patterns": [r"\bterraform\s+destroy\b", {"pattern": "(", "label": "깨짐"}]},
                                  warn=warns.append)
        self.assertEqual(hook.match_risky("kubectl delete ns prod", p), ["k8s 삭제"])
        self.assertEqual(hook.match_risky("terraform destroy", p), ["위험 명령"])
        self.assertEqual(hook.match_risky("rm -rf /", p), [])  # risky_patterns는 기본 목록을 교체
        self.assertEqual(len(warns), 1)

    def test_extract_command(self):
        tools = ["Bash", "PowerShell"]
        self.assertEqual(hook.extract_command(json.loads(bash("ls")), tools), "ls")
        self.assertIsNone(hook.extract_command({"tool_name": "Write", "tool_input": {"command": "rm -rf /"}}, tools))
        self.assertIsNone(hook.extract_command({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                                "tool_input": {"command": "rm -rf /"}}, tools))
        self.assertEqual(hook.extract_command({"tool_name": "PowerShell", "tool_input": {"command": "ri x -r"}}, tools),
                         "ri x -r")
        self.assertIsNone(hook.extract_command(["not", "a", "dict"], tools))


class FakeClient:
    def __init__(self, statuses=("allow",), fail_request=None, fail_wait=None):
        self.statuses = list(statuses)
        self.fail_request = fail_request
        self.fail_wait = fail_wait
        self.requests = []
        self.waits = []

    def request_approval(self, payload):
        self.requests.append(payload)
        if self.fail_request:
            raise self.fail_request
        return {"id": "abc", "status": "pending"}

    def wait_approval(self, rid, wait):
        self.waits.append(wait)
        if self.fail_wait:
            raise self.fail_wait
        return {"id": rid, "status": self.statuses.pop(0) if self.statuses else "pending"}


class Clock:
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


class HookDecisionTests(unittest.TestCase):
    def run_hook(self, raw, client=None, clock=None, **acfg):
        cfg = config(approval=acfg)
        out, err = io.StringIO(), io.StringIO()
        made = []

        def factory(c, a):
            made.append(1)
            return client
        code = hook.run(raw, lambda: cfg, factory, out, err, clock=clock or Clock(0.001))
        self.made = bool(made)
        return code, out.getvalue(), err.getvalue()

    def test_non_risky_passes_instantly_without_server(self):
        self.assertEqual(self.run_hook(bash("ls -la"), client=None), (0, "", ""))
        self.assertFalse(self.made)
        self.assertEqual(self.run_hook(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}})), (0, "", ""))
        self.assertEqual(self.run_hook(bash("rm -rf /"), enabled=False), (0, "", ""))

    def test_allow_json_output(self):
        c = FakeClient(["pending", "allow"])
        code, out, err = self.run_hook(bash("rm -rf ./build"), client=c)
        self.assertEqual(code, 0)
        o = json.loads(out)["hookSpecificOutput"]
        self.assertEqual((o["hookEventName"], o["permissionDecision"]), ("PreToolUse", "allow"))
        self.assertIn("승인", o["permissionDecisionReason"])
        req = c.requests[0]
        self.assertEqual((req["command"], req["label"], req["description"], req["tool"], req["cwd"]),
                         ("rm -rf ./build", "강제 재귀 삭제", "빌드 폴더 정리", "Bash", "/repo"))
        self.assertEqual(self.run_hook(bash("rm -rf x"), client=FakeClient(["allow"]), allow_style="passthrough"),
                         (0, "", ""))

    def test_deny_exit2_default_and_json_style(self):
        code, out, err = self.run_hook(bash("git push --force"), client=FakeClient(["deny"]))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("반려", err)
        code, out, err = self.run_hook(bash("git push --force"), client=FakeClient(["deny"]), deny_style="json")
        self.assertEqual((code, json.loads(out)["hookSpecificOutput"]["permissionDecision"]), (0, "deny"))

    def test_timeout_default_deny_and_ask(self):
        c = FakeClient([])  # 계속 pending
        code, out, err = self.run_hook(bash("git reset --hard"), client=c, clock=Clock(10), timeout_seconds=45)
        self.assertEqual(code, 2)
        self.assertIn("45초", err)
        self.assertTrue(c.waits and all(1 <= w <= 25 for w in c.waits))
        code, out, _ = self.run_hook(bash("git reset --hard"), client=FakeClient(["expired"]), on_timeout="ask")
        self.assertEqual((code, json.loads(out)["hookSpecificOutput"]["permissionDecision"]), (0, "ask"))

    def test_unreachable_server(self):
        down = FakeClient(fail_request=ServerUnavailable("connection refused"))
        code, out, err = self.run_hook(bash("rm -rf ./build"), client=down)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("연결할 수 없어", err)
        code, out, _ = self.run_hook(bash("rm -rf ./build"), client=down, fail_open=True)
        self.assertEqual((code, json.loads(out)["hookSpecificOutput"]["permissionDecision"]), (0, "ask"))
        code, _, err = self.run_hook(bash("rm -rf x"), client=FakeClient(fail_request=ServerError(401, {})))
        self.assertEqual(code, 2)
        self.assertIn("토큰", err)
        code, _, err = self.run_hook(bash("rm -rf x"), client=FakeClient(fail_request=ServerError(429, {"error": "많음"})))
        self.assertEqual(code, 2)

    def test_server_dies_mid_wait(self):
        hook_sleep = []
        c = FakeClient(fail_wait=ServerUnavailable("gone"))
        decision = hook.evaluate(json.loads(bash("rm -rf x")), config()["approval"], c, ["x"], "rm -rf x",
                                 clock=Clock(0.01), sleep=hook_sleep.append)
        self.assertEqual(decision.action, "deny")
        self.assertEqual(len(c.waits), 3)

    def test_broken_input_and_crash_fail_closed(self):
        self.assertEqual(self.run_hook("{not json rm -rf /")[0], 2)
        self.assertEqual(self.run_hook("{not json ls")[0], 0)

        class Boom(FakeClient):
            def request_approval(self, payload):
                raise RuntimeError("bug")
        code, _, err = self.run_hook(bash("rm -rf x"), client=Boom())
        self.assertEqual(code, 2)
        self.assertIn("RuntimeError", err)

    def test_main_reads_config_file_and_stdin(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"server": {"token": "t"}, "approval": {"url": "http://127.0.0.1:%d" % free_port()}}, f)
        try:
            out, err = io.StringIO(), io.StringIO()
            self.assertEqual(hook.main(path, io.StringIO(bash("rm -rf ./build")), out, err), 2)
            self.assertIn("연결할 수 없어", err.getvalue())
            self.assertEqual(hook.main(path, io.StringIO(bash("echo hi")), out, err), 0)
        finally:
            os.unlink(path)


class VoiceTests(unittest.TestCase):
    def test_parse_voice_decision(self):
        for t in ["ㅇㅇ", "응", "응!", "해", "승인", "승인합니다", "그래 해", "네 해주세요", "오케이", "OK", "좋아", "해도 돼"]:
            self.assertEqual(parse_voice_decision(t), "allow", t)
        for t in ["안돼", "안 돼!", "아니", "아니요", "반려", "하지 마", "안 해", "노", "ㄴㄴ", "응 아니 안돼", "그래 하지마"]:
            self.assertEqual(parse_voice_decision(t), "deny", t)
        for t in ["", "음...", "잠깐만", "뭐라고"]:
            self.assertIsNone(parse_voice_decision(t), t)

    def test_summarize(self):
        self.assertEqual(summarize("강제 재귀 삭제", "rm  -rf\n./build"), "강제 재귀 삭제: rm -rf ./build")
        self.assertTrue(summarize("x", "a" * 500).endswith("…"))


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.mono = FakeMono()
        self.events = EventQueue()
        self.board = ApprovalBoard(config(approval={"max_pending": 2})["approval"], self.events, clock=self.mono)

    def test_create_event_decide_first_wins(self):
        r = self.board.create("rm -rf ./build", label="강제 재귀 삭제", timeout=30)
        self.assertEqual(r["say"], "사장님, 강제 재귀 삭제: rm -rf ./build 해도 되겠습니까?")
        ev = self.events.since(0)[-1]
        self.assertEqual((ev["kind"], ev["request_id"], ev["text"]), ("approval", r["id"], r["say"]))
        self.assertEqual(self.board.decide(r["id"], utterance="음...")["understood"], False)
        self.assertEqual(self.board.get(r["id"])["status"], "pending")
        self.assertEqual(self.board.decide(r["id"], utterance="아니 안 돼")["status"], "deny")
        self.assertEqual(self.board.decide(r["id"], decision="allow")["status"], "deny")  # 먼저 온 결정만
        done = self.events.since(0)[-1]
        self.assertEqual((done["kind"], done["request_id"], done["status"]), ("approval_done", r["id"], "deny"))
        with self.assertRaises(KeyError):
            self.board.decide("nope", decision="allow")
        with self.assertRaises(ValueError):
            self.board.decide(r["id"], decision="maybe")
        with self.assertRaises(ValueError):
            self.board.create("   ")

    def test_wait_wakes_on_decision_and_expiry(self):
        r = self.board.create("git push -f", timeout=30)
        threading.Timer(0.2, lambda: self.board.decide(r["id"], decision="allow")).start()
        t0 = time.time()
        self.assertEqual(self.board.wait(r["id"], 5)["status"], "allow")
        self.assertLess(time.time() - t0, 3)
        r2 = self.board.create("git push -f", timeout=30)
        self.assertEqual(self.board.wait(r2["id"], 0)["status"], "pending")
        self.mono.t += 31
        self.assertEqual(self.board.wait(r2["id"], 0)["status"], "expired")
        self.assertEqual(self.events.since(0)[-1]["status"], "expired")
        self.assertEqual(self.board.decide(r2["id"], decision="allow")["status"], "expired")
        self.assertIsNone(self.board.wait("nope", 0))

    def test_max_pending(self):
        self.board.create("rm -rf a")
        self.board.create("rm -rf b")
        with self.assertRaises(OverflowError):
            self.board.create("rm -rf c")
        self.assertEqual(len(self.board.pending()), 2)


class ApprovalHttpFlowTests(unittest.TestCase):
    def setUp(self):
        self.app = Sidekick(config(), spawn=lambda fn, *a: fn(*a))
        self.live = LiveServer(self.app)

    def tearDown(self):
        self.live.close()

    def start_hook(self, cmd, **acfg):
        cfg = config(approval=acfg)
        result = {}

        def go():
            out, err = io.StringIO(), io.StringIO()
            result["code"] = hook.run(bash(cmd), lambda: cfg,
                                      lambda c, a: SidekickClient(self.live.base, "secret-token"), out, err)
            result["out"], result["err"] = out.getvalue(), err.getvalue()
        t = threading.Thread(target=go, daemon=True)
        t.start()
        return t, result

    def pending(self):
        for _ in range(100):
            code, r = self.live.call("/api/approval/pending")
            if r["pending"]:
                return r["pending"][0]
            time.sleep(0.05)
        self.fail("결재 요청이 안 올라옴")

    def test_button_approve_flow(self):
        t, res = self.start_hook("rm -rf ./build")
        p = self.pending()
        self.assertEqual(p["label"], "강제 재귀 삭제")
        self.assertEqual(self.live.call("/api/approval/decide", {"id": p["id"], "decision": "allow"}, token="bad")[0], 401)
        code, r = self.live.call("/api/approval/decide", {"id": p["id"], "decision": "allow"})
        self.assertEqual((code, r["status"]), (200, "allow"))
        t.join(10)
        self.assertEqual(res["code"], 0)
        self.assertEqual(json.loads(res["out"])["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_voice_deny_flow(self):
        t, res = self.start_hook("git push --force origin main")
        p = self.pending()
        _, r = self.live.call("/api/approval/decide", {"id": p["id"], "utterance": "어..."})
        self.assertEqual((r["understood"], r["status"]), (False, "pending"))
        _, r = self.live.call("/api/approval/decide", {"id": p["id"], "utterance": "아니 안 돼"})
        self.assertEqual(r["status"], "deny")
        t.join(10)
        self.assertEqual(res["code"], 2)
        self.assertIn("반려", res["err"])

    def test_timeout_against_real_server(self):
        t, res = self.start_hook("git reset --hard", timeout_seconds=1)
        self.pending()
        t.join(10)
        self.assertEqual(res["code"], 2)
        self.assertIn("1초", res["err"])

    def test_routes_validation(self):
        self.assertEqual(self.live.call("/api/approval/request", {"command": ""})[0], 400)
        self.assertEqual(self.live.call("/api/approval/decide", {"id": "nope", "decision": "allow"})[0], 404)
        self.assertEqual(self.live.call("/api/approval/wait?id=nope&wait=0")[0], 404)
        _, r = self.live.call("/api/approval/request", {"command": "rm -rf x", "timeout_seconds": 30})
        self.assertEqual(self.live.call("/api/approval/decide", {"id": r["id"], "decision": "sure"})[0], 400)
        for i in range(9):
            self.live.call("/api/approval/request", {"command": "rm -rf %d" % i})
        self.assertEqual(self.live.call("/api/approval/request", {"command": "rm -rf z"})[0], 429)

    def test_unreachable_real_port(self):
        out, err = io.StringIO(), io.StringIO()
        code = hook.run(bash("rm -rf ./build"), lambda: config(),
                        lambda c, a: SidekickClient("http://127.0.0.1:%d" % free_port(), "secret-token", timeout=2),
                        out, err)
        self.assertEqual(code, 2)
        self.assertIn("연결할 수 없어", err.getvalue())


if __name__ == "__main__":
    unittest.main()
