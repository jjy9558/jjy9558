import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import helpers as H
from helpers import rmtree

from devguard import strike
from devguard.config import DEFAULTS

CFG = DEFAULTS["strike"]
REASON = "같은 실패 3번째. 파업합니다. 접근 방식을 바꾸세요."


def bash_pre(cmd, sid="s1", **extra):
    d = {"session_id": sid, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}}
    d.update(extra)
    return d


def bash_post(cmd, exit_code=1, sid="s1", **extra):
    d = {"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Bash",
         "tool_input": {"command": cmd},
         "tool_response": {"stdout": "", "stderr": "boom", "interrupted": False, "exit_code": exit_code}}
    d.update(extra)
    return d


def edit_post(path, old, new, sid="s1"):
    return {"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": path, "old_string": old, "new_string": new},
            "tool_response": {"success": True}}


def edit_pre(path, sid="s1"):
    return {"session_id": sid, "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}


def write_post(path, content, sid="s1"):
    return {"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Write",
            "tool_input": {"file_path": path, "content": content}, "tool_response": {}}


class Base(unittest.TestCase):
    def setUp(self):
        self.sdir = tempfile.mkdtemp(prefix="dg-strike-")
        self.t = 1000.0

    def tearDown(self):
        rmtree(self.sdir)

    def h(self, payload, cfg=None, dt=1.0):
        self.t += dt
        return strike.handle(payload, dict(CFG, **(cfg or {})), self.sdir, self.t)


class Normalize(unittest.TestCase):
    def test_equalities(self):
        n = strike.normalize_command
        self.assertEqual(n("  pytest   -x \n tests/ "), n("pytest -x tests/"))
        self.assertEqual(n("pytest -x 2>&1 | tail -20"), n("pytest -x"))
        self.assertEqual(n("npm test | head -n 5"), "npm test")
        self.assertEqual(n("make build;"), "make build")
        self.assertNotEqual(n("pytest -x"), n("pytest -k foo"))
        self.assertEqual(n(None), "")

    def test_key_stable(self):
        self.assertEqual(strike.command_key("a b"), strike.command_key("a b"))
        self.assertNotEqual(strike.command_key("a b"), strike.command_key("a c"))


class FailureDetection(unittest.TestCase):
    def test_shapes(self):
        f = strike.bash_failed
        self.assertTrue(f({"exit_code": 1}))
        self.assertTrue(f({"exitCode": 2}))
        self.assertFalse(f({"exit_code": 0, "stderr": "warning"}))
        self.assertTrue(f({"is_error": True}))
        self.assertTrue(f({"success": False}))
        self.assertTrue(f({"error": "Exit code 1"}))
        self.assertTrue(f("Exit code 127\ncommand not found"))
        self.assertFalse(f("Exit code 0"))
        self.assertFalse(f({"stdout": "ok", "stderr": "", "interrupted": False}))
        self.assertFalse(f({"interrupted": True, "exit_code": 130}))
        self.assertFalse(f(None))
        self.assertFalse(f(42))


class CommandStrike(Base):
    def test_third_failure_strikes_then_denies(self):
        self.assertEqual(self.h(bash_post("pytest -x")), (0, None, ""))
        self.assertEqual(self.h(bash_post("pytest   -x")), (0, None, ""))
        code, out, err = self.h(bash_post("pytest -x 2>&1 | tail -5"))
        self.assertEqual(code, 0)
        self.assertEqual(out["decision"], "block")
        self.assertIn(REASON, out["reason"])
        # 다음 동일 시도는 PreToolUse 에서 거부
        code, out, err = self.h(bash_pre("pytest -x"))
        self.assertEqual(code, 0)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn(REASON, hso["permissionDecisionReason"])
        # 다른 명령은 통과
        self.assertEqual(self.h(bash_pre("ls -la")), (0, None, ""))

    def test_two_failures_do_not_strike(self):
        self.h(bash_post("make"))
        self.h(bash_post("make"))
        self.assertEqual(self.h(bash_pre("make")), (0, None, ""))

    def test_success_resets_streak(self):
        self.h(bash_post("make"))
        self.h(bash_post("make"))
        self.h(bash_post("make", exit_code=0))
        self.h(bash_post("make"))
        self.assertEqual(self.h(bash_post("make")), (0, None, ""))  # 연속 2번째일 뿐
        self.assertEqual(self.h(bash_pre("make")), (0, None, ""))

    def test_other_command_failures_do_not_mix(self):
        for c in ("a", "b", "a", "b"):
            self.assertEqual(self.h(bash_post(c)), (0, None, ""))

    def test_sessions_are_isolated(self):
        for _ in range(3):
            self.h(bash_post("make", sid="A"))
        self.assertEqual(self.h(bash_pre("make", sid="B")), (0, None, ""))
        self.assertEqual(self.h(bash_pre("make", sid="A"))[1]["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_subagent_isolated_by_agent_id(self):
        for _ in range(3):
            self.h(bash_post("make", agent_id="sub1"))
        self.assertEqual(self.h(bash_pre("make", agent_id="sub2")), (0, None, ""))
        self.assertEqual(self.h(bash_pre("make", agent_id="sub1"))[1]["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_edit_clears_strike(self):
        for _ in range(3):
            self.h(bash_post("make"))
        self.assertIsNotNone(self.h(bash_pre("make"))[1])
        self.h(edit_post("/x/a.py", "1", "2"))  # 접근을 바꿈
        self.assertEqual(self.h(bash_pre("make")), (0, None, ""))
        # 카운터도 초기화: 다시 3번 실패해야 파업
        self.h(bash_post("make"))
        self.assertEqual(self.h(bash_post("make")), (0, None, ""))

    def test_max_denies_releases(self):
        for _ in range(3):
            self.h(bash_post("make"))
        for _ in range(3):
            self.assertIsNotNone(self.h(bash_pre("make"), {"max_denies": 3})[1])
        self.assertEqual(self.h(bash_pre("make"), {"max_denies": 3}), (0, None, ""))
        self.assertEqual(self.h(bash_pre("make"), {"max_denies": 3}), (0, None, ""))

    def test_ttl_releases(self):
        for _ in range(3):
            self.h(bash_post("make"))
        self.assertIsNotNone(self.h(bash_pre("make"))[1])
        self.assertEqual(self.h(bash_pre("make"), dt=5000), (0, None, ""))

    def test_threshold_configurable(self):
        self.h(bash_post("x"), {"threshold": 2})
        self.assertEqual(self.h(bash_post("x"), {"threshold": 2})[1]["decision"], "block")

    def test_post_tool_use_failure_event(self):
        ev = {"session_id": "s1", "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
              "tool_input": {"command": "npm run build"}, "error": "Exit code 1\nboom", "is_interrupt": False}
        self.h(ev)
        self.h(ev)
        self.assertEqual(self.h(ev)[1]["decision"], "block")
        self.assertEqual(self.h(bash_pre("npm run build"))[1]["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_interrupt_not_counted(self):
        ev = {"session_id": "s1", "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
              "tool_input": {"command": "sleep 99"}, "error": "interrupted", "is_interrupt": True}
        for _ in range(5):
            self.assertEqual(self.h(ev), (0, None, ""))
        self.assertEqual(self.h(bash_pre("sleep 99")), (0, None, ""))

    def test_exit2_fallback(self):
        cfg = {"output": "exit2"}
        self.h(bash_post("make"), cfg)
        self.h(bash_post("make"), cfg)
        code, out, err = self.h(bash_post("make"), cfg)
        self.assertEqual((code, out), (2, None))
        self.assertIn(REASON, err)
        code, out, err = self.h(bash_pre("make"), cfg)
        self.assertEqual((code, out), (2, None))
        self.assertIn(REASON, err)

    def test_stop_action(self):
        cfg = {"action": "stop"}
        self.h(bash_post("make"), cfg)
        self.h(bash_post("make"), cfg)
        code, out, _ = self.h(bash_post("make"), cfg)
        self.assertEqual(out["continue"], False)
        self.assertIn(REASON, out["stopReason"])

    def test_ignore_regex(self):
        cfg = {"ignore_regex": ["^curl .*health"]}
        for _ in range(4):
            self.assertEqual(self.h(bash_post("curl localhost/health"), cfg), (0, None, ""))
        self.assertEqual(self.h(bash_pre("curl localhost/health"), cfg), (0, None, ""))

    def test_disabled(self):
        for _ in range(4):
            self.assertEqual(self.h(bash_post("make"), {"enabled": False}), (0, None, ""))

    def test_irrelevant_tools_ignored(self):
        self.assertEqual(self.h({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Read",
                                 "tool_input": {"file_path": "/x"}}), (0, None, ""))

    def test_event_inferred_when_missing(self):
        p = bash_post("zzz")
        del p["hook_event_name"]
        for _ in range(3):
            res = self.h(copy.deepcopy(p))
        self.assertEqual(res[1]["decision"], "block")
        pre = bash_pre("zzz")
        del pre["hook_event_name"]
        self.assertEqual(self.h(pre)[1]["hookSpecificOutput"]["permissionDecision"], "deny")


class FileFlipStrike(Base):
    def test_edit_back_and_forth(self):
        f = "/proj/a.py"
        res = [self.h(edit_post(f, "A", "B")),
               self.h(edit_post(f, "B", "A")),
               self.h(edit_post(f, "A", "B")),
               self.h(edit_post(f, "B", "A"))]
        self.assertEqual([r[1] for r in res[:3]], [None, None, None])
        self.assertEqual(res[3][1]["decision"], "block")
        self.assertIn("파업합니다", res[3][1]["reason"])
        code, out, _ = self.h(edit_pre(f))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.h(edit_pre("/proj/other.py")), (0, None, ""))

    def test_progressive_edits_do_not_strike(self):
        f = "/proj/a.py"
        for i in range(10):
            self.assertEqual(self.h(edit_post(f, "v%d" % i, "v%d" % (i + 1))), (0, None, ""))

    def test_write_content_flipping(self):
        f = "/proj/b.py"
        outs = [self.h(write_post(f, c))[1] for c in ["A", "B", "A", "B", "A"]]
        self.assertEqual(outs[:4], [None] * 4)
        self.assertEqual(outs[4]["decision"], "block")

    def test_rewriting_same_content_is_not_flip(self):
        for _ in range(6):
            self.assertEqual(self.h(write_post("/p/c.py", "same")), (0, None, ""))

    def test_multiedit(self):
        f = "/proj/m.py"
        ev = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "MultiEdit",
              "tool_input": {"file_path": f, "edits": [{"old_string": "A", "new_string": "B"},
                                                      {"old_string": "B", "new_string": "A"}]},
              "tool_response": {}}
        self.assertIsNone(self.h(ev)[1])      # 한 호출 안에서 A->B, B->A : 되돌림 1
        out = self.h(ev)[1]                   # 두 번째 호출에서 누적 3 -> 파업
        self.assertEqual(out["decision"], "block")

    def test_released_after_max_denies(self):
        f = "/proj/a.py"
        for o, n in (("A", "B"), ("B", "A"), ("A", "B"), ("B", "A")):
            self.h(edit_post(f, o, n))
        for _ in range(3):
            self.assertIsNotNone(self.h(edit_pre(f))[1])
        self.assertEqual(self.h(edit_pre(f)), (0, None, ""))

    def test_path_normalization(self):
        self.h(edit_post("/proj/./a.py", "A", "B"))
        self.h(edit_post("/proj/sub/../a.py", "B", "A"))
        self.h(edit_post("/proj/a.py", "A", "B"))
        self.assertIsNotNone(self.h(edit_post("/proj/a.py", "B", "A"))[1])


class RunHookSafety(Base):
    def run_hook(self, text, **kw):
        return strike.run_hook(text, env=kw.pop("env", {}), cfg=dict(CFG), sdir=self.sdir, **kw)

    def test_json_output_shape_is_valid_ascii_json(self):
        for _ in range(3):
            code, out, err = self.run_hook(json.dumps(bash_post("make")))
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["decision"], "block")
        self.assertTrue(out.isascii())
        code, out, err = self.run_hook(json.dumps(bash_pre("make")))
        data = json.loads(out)
        self.assertEqual(set(data), {"hookSpecificOutput"})
        self.assertEqual(set(data["hookSpecificOutput"]), {"hookEventName", "permissionDecision", "permissionDecisionReason"})

    def test_garbage_inputs_allow(self):
        for text in ("", "   ", "not json", "[1,2,3]", "null", '{"tool_name": 5}', "{" * 1000,
                     '{"tool_name": "Bash", "tool_input": "string"}',
                     '{"tool_name": "Bash", "tool_input": {"command": null}, "tool_response": [1]}'):
            self.assertEqual(self.run_hook(text), (0, "", ""), text[:30])

    def test_internal_exception_is_swallowed(self):
        with mock.patch.object(strike, "handle", side_effect=RuntimeError("boom")):
            self.assertEqual(self.run_hook(json.dumps(bash_pre("x"))), (0, "", ""))

    def test_unwritable_state_dir_is_swallowed(self):
        blocker = os.path.join(self.sdir, "file")
        with open(blocker, "w") as f:
            f.write("x")
        res = strike.run_hook(json.dumps(bash_post("x")), env={}, cfg=dict(CFG), sdir=os.path.join(blocker, "sub"))
        self.assertEqual(res, (0, "", ""))

    def test_corrupt_state_file_is_ignored(self):
        path = strike.state_path(self.sdir, "s1")
        with open(path, "w") as f:
            f.write("{broken")
        self.assertEqual(self.run_hook(json.dumps(bash_pre("x"))), (0, "", ""))

    def test_kill_switch_env(self):
        for _ in range(3):
            res = self.run_hook(json.dumps(bash_post("make")), env={"DEVGUARD_STRIKE_OFF": "1"})
        self.assertEqual(res, (0, "", ""))

    def test_exit2_via_env(self):
        env = {"DEVGUARD_STRIKE_OUTPUT": "exit2"}
        for _ in range(2):
            self.run_hook(json.dumps(bash_post("make")), env=env)
        code, out, err = self.run_hook(json.dumps(bash_post("make")), env=env)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("파업", err)

    def test_dump_env_records_payload(self):
        dump = os.path.join(self.sdir, "dump.jsonl")
        self.run_hook(json.dumps(bash_post("make")), env={"DEVGUARD_STRIKE_DUMP": dump})
        with open(dump, encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["tool_name"], "Bash")

    def test_state_file_is_json_per_session(self):
        self.run_hook(json.dumps(bash_post("make", sid="abc/../def")))
        names = os.listdir(self.sdir)
        self.assertEqual(len(names), 1)
        self.assertNotIn("/", names[0])
        with open(os.path.join(self.sdir, names[0])) as f:
            self.assertIn("cmds", json.load(f))

    def test_reset_and_status(self):
        self.run_hook(json.dumps(bash_post("make")))
        self.assertIn("make", "\n".join(strike.status(self.sdir)))
        self.assertEqual(strike.reset(self.sdir), 1)
        self.assertEqual(strike.reset(self.sdir), 0)


class CliEndToEnd(Base):
    """실제 프로세스로 훅을 호출 (stdin JSON -> stdout JSON) + 속도 확인."""

    def call(self, payload, *extra_args, env_extra=None):
        env = H.git_env({"DEVGUARD_STATE_DIR": self.sdir})
        env.update(env_extra or {})
        t0 = time.time()
        p = subprocess.run([sys.executable, os.path.join(H.ROOT, "devguard_run.py"), "strike"] + list(extra_args),
                           input=json.dumps(payload).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env, cwd=self.sdir)
        return p, time.time() - t0

    def test_full_cycle(self):
        for _ in range(3):
            p, dur = self.call(bash_post("false"))
            self.assertEqual(p.returncode, 0)
            self.assertLess(dur, 3.0)
        self.assertEqual(json.loads(p.stdout)["decision"], "block")
        p, _ = self.call(bash_pre("false"))
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_exit2_flag(self):
        for _ in range(2):
            self.call(bash_post("false"), "--exit2")
        p, _ = self.call(bash_post("false"), "--exit2")
        self.assertEqual(p.returncode, 2)
        self.assertIn("파업", p.stderr.decode("utf-8"))

    def test_garbage_stdin_via_cli(self):
        env = H.git_env({"DEVGUARD_STATE_DIR": self.sdir})
        p = subprocess.run([sys.executable, os.path.join(H.ROOT, "devguard_run.py"), "strike"],
                           input=b"\xff\xfe garbage", stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual((p.returncode, p.stdout), (0, b""))


if __name__ == "__main__":
    unittest.main()
