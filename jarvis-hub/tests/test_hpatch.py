import os
import tempfile
import unittest

import helpers  # noqa: F401
from helpers import make_hub
from jarvis.config import build_worker_config
from jarvis.handlers import HandlerError, PermanentError, build_handlers
from jarvis.handlers.hpatch import HpatchTranslateHandler
from jarvis.intents import parse


class Runner:
    def __init__(self, codes=None, out="진행 1/2\n진행 2/2\n"):
        self.calls = []
        self.codes = codes or {}
        self.out = out

    def __call__(self, argv, cwd=None, timeout=60, env=None):
        self.calls.append({"argv": list(argv), "cwd": cwd, "timeout": timeout, "env": env})
        step = argv[3] if len(argv) > 3 else ""
        return self.codes.get(step, 0), self.out, "boom" if self.codes.get(step) else ""


class HpatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d1 = os.path.join(self.tmp.name, "g1")
        self.d2 = os.path.join(self.tmp.name, "g2")
        os.makedirs(self.d1)
        os.makedirs(self.d2)
        self.cfg = {"enabled": True, "command": ["python", "-m", "hpatch"], "cwd": self.tmp.name,
                    "games": {"g1": {"path": self.d1, "allow_apply": False},
                              "g2": {"path": self.d2, "allow_apply": True, "font": "/f/n.ttf"}},
                    "default_limit": 100, "max_limit": 500}

    def tearDown(self):
        self.tmp.cleanup()

    def run_job(self, payload, runner=None):
        r = runner or Runner()
        h = HpatchTranslateHandler(self.cfg, runner=r)
        return h.run({"payload": payload}), r

    def test_registry_requires_enabled(self):
        w = build_worker_config({"device": "main", "token": "x" * 20,
                                 "handlers": {"hpatch.translate": {"games": {"g1": {"path": self.d1}}}}})
        self.assertNotIn("hpatch.translate", build_handlers(w))
        w["handlers"]["hpatch.translate"]["enabled"] = True
        self.assertIn("hpatch.translate", build_handlers(w))

    def test_unknown_key_and_paths_rejected(self):
        for payload in ({"game": "nope"}, {"game": self.d1}, {"game": "../g1"}, {}, {"game": 5},
                        {"game": "g1", "path": self.d1}, {"game": "g1", "game_dir": "/etc"},
                        {"game": "g1", "limit": 0}, {"game": "g1", "limit": "9"}, {"game": "g1", "limit": True},
                        {"game": "g1", "apply": "yes"}):
            r = Runner()
            with self.assertRaises(PermanentError, msg=str(payload)):
                self.run_job(payload, r)
            self.assertEqual(r.calls, [])

    def test_step_sequence_and_argv(self):
        res, r = self.run_job({"game": "g1", "limit": 50})
        steps = [c["argv"][3] for c in r.calls]
        self.assertEqual(steps, ["scan", "extract", "translate"])
        self.assertEqual(r.calls[2]["argv"], ["python", "-m", "hpatch", "translate", self.d1, "--limit", "50"])
        self.assertTrue(all(c["cwd"] == self.tmp.name for c in r.calls))
        self.assertEqual(r.calls[0]["env"]["PYTHONUTF8"], "1")
        self.assertFalse(res["applied"])
        self.assertIn("[translate] 진행 2/2", res["progress"])
        self.assertEqual([s["step"] for s in res["steps"]], ["scan", "extract", "translate"])

    def test_default_and_max_limit(self):
        _, r = self.run_job({"game": "g1"})
        self.assertEqual(r.calls[2]["argv"][-1], "100")
        _, r = self.run_job({"game": "g1", "limit": 99999})
        self.assertEqual(r.calls[2]["argv"][-1], "500")

    def test_exit3_is_retryable_and_stops(self):
        r = Runner({"translate": 3})
        with self.assertRaises(HandlerError) as cm:
            self.run_job({"game": "g2", "apply": True}, r)
        self.assertNotIsInstance(cm.exception, PermanentError)
        self.assertEqual([c["argv"][3] for c in r.calls], ["scan", "extract", "translate"])  # apply 안 함

    def test_other_failure_is_permanent(self):
        r = Runner({"scan": 1})
        with self.assertRaises(PermanentError):
            self.run_job({"game": "g1"}, r)
        self.assertEqual(len(r.calls), 1)

    def test_apply_gating(self):
        r = Runner()
        with self.assertRaises(PermanentError):
            self.run_job({"game": "g1", "apply": True}, r)
        self.assertEqual(r.calls, [])  # 아무것도 실행하지 않고 거부
        res, r = self.run_job({"game": "g2", "apply": True})
        self.assertEqual([c["argv"][3] for c in r.calls], ["scan", "extract", "translate", "apply"])
        self.assertEqual(r.calls[3]["argv"][-2:], ["--font", "/f/n.ttf"])
        self.assertTrue(res["applied"])
        # allow_apply 여도 payload 가 apply 를 말하지 않으면 적용 안 함
        _, r = self.run_job({"game": "g2"})
        self.assertNotIn("apply", [c["argv"][3] for c in r.calls])

    def test_timeout_is_retryable(self):
        def runner(argv, cwd=None, timeout=60, env=None):
            if argv[3] == "translate":
                raise HandlerError("시간 초과")
            return 0, "ok\n", ""
        with self.assertRaises(HandlerError):
            HpatchTranslateHandler(self.cfg, runner=runner).run({"payload": {"game": "g1"}})

    def test_missing_folder(self):
        self.cfg["games"]["g1"]["path"] = os.path.join(self.tmp.name, "gone")
        with self.assertRaises(PermanentError):
            self.run_job({"game": "g1"})

    def test_disabled(self):
        self.cfg["enabled"] = False
        with self.assertRaises(PermanentError):
            self.run_job({"game": "g1"})


class HpatchIntentTest(unittest.TestCase):
    GAMES = {"g1": ["엘든링", "엘든"]}

    def test_parse(self):
        p = parse("자비스, 엘든링 한글패치 이어서 돌려", games=self.GAMES).intents[0]
        self.assertEqual((p.name, p.args["game"], p.args["apply"]), ("hpatch", "g1", False))
        p = parse("자비스 엘든 한글패치 적용해", games=self.GAMES).intents[0]
        self.assertTrue(p.args["apply"])
        self.assertEqual(parse("자비스 한글패치 돌려", games=self.GAMES).intents[0].args["game"], None)

    def test_router_submits_waiting_approval(self):
        hub = make_hub({"router": {"games": self.GAMES}})
        r = hub.router.handle("s21", "자비스, 엘든링 한글패치 이어서 돌려")
        job = hub.store.get(r["jobs"][0])
        self.assertEqual((job["type"], job["status"]), ("hpatch.translate", "waiting_approval"))
        self.assertEqual(job["payload"]["game"], "g1")
        self.assertNotIn("apply", job["payload"])
        self.assertEqual(sorted(job["caps"]), ["gpu", "llm"])
        self.assertIn("승인", r["answer"])

    def test_router_unknown_game(self):
        hub = make_hub({"router": {"games": self.GAMES}})
        r = hub.router.handle("s21", "자비스 한글패치 돌려")
        self.assertEqual(r["jobs"], [])
        self.assertIn("어느 게임", r["answer"])


if __name__ == "__main__":
    unittest.main()
