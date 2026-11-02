import datetime as dt
import os
import tempfile
import unittest

from helpers import FakeClock, FakeMono, LiveServer, config
from sidekick import butler
from sidekick.departure import Departure
from sidekick.server import Sidekick
from test_butler import ICS

MON_8AM = dt.datetime(2026, 11, 2, 8, 0)  # 월요일


class ProviderTests(unittest.TestCase):
    def setUp(self):
        fd, self.ics = tempfile.mkstemp(suffix=".ics")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ICS)

    def tearDown(self):
        os.unlink(self.ics)

    def test_calendar_next(self):
        self.assertEqual(butler.calendar_next({"path": self.ics}, MON_8AM),
                         "첫 일정은 09시 30분 스탠드업, 팀. 종일 일정: 엄마 생신.")
        self.assertEqual(butler.calendar_next({"path": self.ics}, MON_8AM.replace(hour=12)),
                         "첫 일정은 19시 헬스장. 종일 일정: 엄마 생신.")
        self.assertEqual(butler.calendar_next({"path": self.ics}, MON_8AM.replace(hour=21)), "종일 일정: 엄마 생신.")
        self.assertIsNone(butler.calendar_next({"path": ""}, MON_8AM))

    def test_reminders_weekdays(self):
        opts = {"items": ["지갑", {"text": "분리수거", "weekdays": [0]}, {"text": "헬스 가방", "weekdays": [2]}, "  "]}
        self.assertEqual(butler.reminders(opts, MON_8AM), "챙기세요: 지갑, 분리수거.")
        self.assertEqual(butler.reminders(opts, MON_8AM + dt.timedelta(days=2)), "챙기세요: 지갑, 헬스 가방.")
        self.assertIsNone(butler.reminders({"items": []}, MON_8AM))

    def test_open_meteo_disabled_by_default_and_fallbacks(self):
        calls = []

        def fetch(url, timeout):
            calls.append((url, timeout))
            return {"current": {"temperature_2m": 11.6},
                    "daily": {"temperature_2m_max": [18.2], "temperature_2m_min": [8.9],
                              "precipitation_probability_max": [70]}}
        self.assertIsNone(butler.open_meteo({"latitude": 37.5, "longitude": 127}, MON_8AM, fetch=fetch))
        self.assertEqual(calls, [])  # 꺼져 있으면 네트워크를 건드리지 않는다
        on = {"enabled": True, "latitude": 37.5, "longitude": 127.0}
        self.assertEqual(butler.open_meteo(on, MON_8AM, fetch=fetch),
                         "지금 12도, 오늘 최고 18도, 최저 9도. 강수확률 70%, 우산 챙기세요.")
        self.assertIn("latitude=37.5", calls[0][0])
        self.assertIn("precipitation_probability_max", calls[0][0])
        dry = lambda u, t: {"daily": {"precipitation_probability_max": [10]}}  # noqa: E731
        self.assertEqual(butler.open_meteo(on, MON_8AM, fetch=dry), "강수확률 10%.")

        def down(u, t):
            raise OSError("no network")
        self.assertIsNone(butler.open_meteo(on, MON_8AM, fetch=down))
        self.assertIsNone(butler.open_meteo(on, MON_8AM, fetch=lambda u, t: {"weird": 1}))
        self.assertIsNone(butler.open_meteo({"enabled": True}, MON_8AM, fetch=fetch))  # 좌표 없음

    def test_gpu_busy(self):
        ran = []

        def runner(argv, cwd=None, timeout=None):
            ran.append(argv)
            return 0, "NVIDIA GeForce RTX 5080, 87\nIntegrated, 3\n"
        self.assertIsNone(butler.gpu_busy({}, MON_8AM, runner=runner))  # 기본 꺼짐
        self.assertEqual(ran, [])
        self.assertEqual(butler.gpu_busy({"enabled": True}, MON_8AM, runner=runner),
                         "PC가 아직 일하는 중이에요: NVIDIA GeForce RTX 5080 사용률 87%. 켜 두고 갈 건지 확인하세요.")
        self.assertEqual(ran[0][0], "nvidia-smi")
        self.assertIsInstance(ran[0], list)
        self.assertIsNone(butler.gpu_busy({"enabled": True, "threshold": 90}, MON_8AM, runner=runner))
        self.assertIsNone(butler.gpu_busy({"enabled": True}, MON_8AM,
                                          runner=lambda a, cwd=None, timeout=None: (127, "명령을 찾을 수 없습니다")))


class DepartureTests(unittest.TestCase):
    def make(self, providers, registry=None, **kw):
        self.mono = FakeMono()
        cfg = config(departure=dict({"providers": providers}, **kw))["departure"]
        return Departure(cfg, clock=FakeClock(MON_8AM), monotonic=self.mono, registry=registry)

    def test_checklist_assembly_with_fake_providers(self):
        def boom(o, n):
            raise RuntimeError("x")
        reg = {"weather": lambda o, n: "강수확률 80%, 우산 챙기세요.",
               "cal": lambda o, n: "첫 일정은 09시 30분 스탠드업.",
               "nothing": lambda o, n: None, "boom": boom,
               "fixed": lambda o, n: "챙기세요: %s." % ", ".join(o["items"]),
               "pc": lambda o, n: "PC가 아직 일하는 중이에요."}
        d = self.make([{"name": "weather"}, {"name": "nothing"}, {"name": "boom"}, {"name": "cal"},
                       {"name": "fixed", "items": ["지갑", "에어컨 끄기"]}, {"name": "pc", "enabled": False},
                       {"name": "unknown"}], registry=reg)
        r = d.leave("button")
        self.assertEqual(r["items"], ["강수확률 80%, 우산 챙기세요.", "첫 일정은 09시 30분 스탠드업.",
                                      "챙기세요: 지갑, 에어컨 끄기."])
        self.assertEqual(r["say"], "출발 체크할게요. 강수확률 80%, 우산 챙기세요. 첫 일정은 09시 30분 스탠드업. "
                                   "챙기세요: 지갑, 에어컨 끄기. 다녀오세요!")

    def test_defaults_work_offline(self):
        r = self.make(config()["departure"]["providers"]).leave("button")  # 날씨·GPU 꺼짐, 캘린더 경로 없음
        self.assertEqual(r["say"], "출발 체크할게요. 챙기세요: 지갑, 열쇠, 휴대폰 충전기. 다녀오세요!")

    def test_cooldown_motion_vs_button_and_disabled(self):
        d = self.make([], intro="", outro="잘 다녀와.")
        self.assertEqual(d.leave("motion")["say"], "잘 다녀와.")
        self.assertTrue(d.leave("motion")["cooldown"])
        self.assertTrue(d.leave("button")["ok"])
        self.assertTrue(d.leave("motion")["cooldown"])  # 버튼 직후 모션도 막힘
        self.mono.t += 301
        self.assertTrue(d.leave("motion")["ok"])
        self.assertFalse(self.make([], enabled=False).leave("button")["ok"])

    def test_http_route_speaks_and_gpu_uses_injected_runner(self):
        ran = []

        def runner(argv, cwd=None, timeout=None):
            ran.append(argv)
            return 0, "RTX 5080, 99"
        cfg = config(departure={"providers": [{"name": "gpu_busy", "enabled": True},
                                              {"name": "reminders", "items": ["우산"]}]})
        app = Sidekick(cfg, clock=FakeClock(MON_8AM), runner=runner, spawn=lambda fn, *a: fn(*a))
        live = LiveServer(app)
        try:
            self.assertEqual(live.call("/api/departure", {"source": "button"}, token=None)[0], 401)
            code, r = live.call("/api/departure", {"source": "button"})
            self.assertEqual(code, 200)
            self.assertEqual(len(r["items"]), 2)
            self.assertIn("RTX 5080 사용률 99%", r["say"])
            self.assertEqual(app.events.since(0)[-1]["text"], r["say"])
            self.assertEqual(ran[0][0], "nvidia-smi")
        finally:
            live.close()


if __name__ == "__main__":
    unittest.main()
