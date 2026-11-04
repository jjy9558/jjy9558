"""시나리오 5: life-nagger 의 sidekick 출력 채널(POST /api/say + X-Sidekick-Token)이 진짜 sidekick 에서 통하는지.

life-nagger 테스트처럼 채널/수신기 클래스는 이 프로세스에서 import 하고(설정은 load_config 로),
sidekick 은 하위 프로세스로 띄운다. 덤: sidekick forward_events → life-nagger 이벤트 수신기(한숨 카운터).
"""
import datetime as dt
import json
import os
import unittest
from unittest import mock

from homelabe2e import services as S
from homelabe2e.case import E2ECase
from homelabe2e.harness import http_json, import_from, token, wait_until


class NaggerSaysThroughSidekick(E2ECase):
    needs = ("life-nagger", "s21-sidekick")

    def setUp(self):
        super().setUp()
        # 채널은 환경변수 SIDEKICK_TOKEN 을 설정값보다 먼저 본다: 이 프로세스 환경이 끼어들지 않게
        env_patch = mock.patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("SIDEKICK_TOKEN", None)
        self.cfgmod = import_from("life-nagger", "nagger.config")
        self.outputs = import_from("life-nagger", "nagger.outputs")
        self.model = import_from("life-nagger", "nagger.model")

    def nagger_cfg(self, sk_url, sk_token):
        return self.cfgmod.load_config(None, overrides={
            "state_dir": str(self.stack.path("nagger-state/.keep").parent),
            "outputs": {"console": {"enabled": False}, "toast": {"enabled": False},
                        "sidekick": {"enabled": True, "base_url": sk_url, "token": sk_token, "min_level": "warn"}}})

    def test_sidekick_channel_say(self):
        sk = S.sidekick(self.stack)
        cfg = self.nagger_cfg(sk.url, sk.token)
        notifier = self.outputs.build_outputs(cfg, platform=None)
        ch = notifier.outputs[0]
        self.assertIsInstance(ch, self.outputs.SidekickSay)
        msg = self.model.Message("새벽 2시야. 게임 끄고 자자.", "mombot", self.model.URGENT)
        notifier.send(msg, dt.datetime.now())
        self.assertEqual(notifier.errors, [])
        self.assertFalse(ch.unsupported)                 # 404 로 스스로 꺼지지 않았음
        ev = S.wait_sidekick_say(sk, lambda e: e.get("module") == "nagger-mombot")
        self.assertEqual(ev["text"], "새벽 2시야. 게임 끄고 자자.")
        # info 는 min_level(warn) 아래라 안 감
        notifier.send(self.model.Message("물 한 잔", "mombot", self.model.INFO), dt.datetime.now())
        self.assertFalse([e for e in S.sidekick_events(sk) if e.get("text") == "물 한 잔"])

    def test_wrong_token_is_reported_not_silent(self):
        sk = S.sidekick(self.stack)
        cfg = self.nagger_cfg(sk.url, "wrong-" + token())
        notifier = self.outputs.build_outputs(cfg, platform=None)
        notifier.send(self.model.Message("엉덩이 경보", "sitting", self.model.WARN), dt.datetime.now())
        self.assertEqual(len(notifier.errors), 1)
        self.assertIn("401", notifier.errors[0])
        self.assertFalse(notifier.outputs[0].unsupported)

    def test_phone_sigh_button_reaches_nagger_counter(self):
        st = self.stack
        es_mod = import_from("life-nagger", "nagger.events_server")
        counters_mod = import_from("life-nagger", "nagger.counters")
        cfg = self.nagger_cfg("http://127.0.0.1:9", "")
        ev_token = token("ng")
        counters = counters_mod.Counters(str(st.path("nagger-state/counters.jsonl")), cfg["counters"])
        got = []

        def on_event(kind, n):
            got.append((kind, n))
            counters.add(kind, dt.datetime.now(), n)
        server = es_mod.EventServer("127.0.0.1", 0, ev_token, cfg["counters"], on_event).start()
        self.addCleanup(server.stop)
        sk = S.sidekick(st, {"forward_events": [
            {"url": "http://127.0.0.1:%d/api/event" % server.port, "token": ev_token, "types": ["sigh"]}]})
        code, conf = http_json("GET", sk.url + "/api/config", headers={"X-Sidekick-Token": sk.token})
        self.assertIn("sigh", json.dumps(conf.get("forward_types"), ensure_ascii=False))
        code, r = http_json("POST", sk.url + "/api/phone_event", {"type": "sigh"}, {"X-Sidekick-Token": sk.token})
        self.assertEqual(code, 200, r)
        self.assertEqual(r.get("forwarded"), 1, r)
        wait_until(lambda: got, 10, 0.1, "nagger 수신기가 한숨을 받음")
        self.assertEqual(got[0], ("sigh", 1))
        # nagger 자기 CLI 로 확인 (같은 상태 폴더)
        ncfg = st.write_json("nagger/config.json", {"state_dir": str(st.path("nagger-state/.keep").parent),
                                                   "outputs": {"toast": {"enabled": False}}})
        p = self.run_cli("life-nagger", ["--config", ncfg, "report", "--week"])
        self.assertIn("한숨", p.stdout)


if __name__ == "__main__":
    unittest.main()
