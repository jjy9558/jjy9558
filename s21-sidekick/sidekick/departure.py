"""현관 출발 체크: 나가기 직전에 날씨·첫 일정·고정 체크 항목·PC 상태를 읽어준다.

귀가 집사(butler)의 provider 구조를 그대로 쓴다: fn(options, now) -> str | None.
"""
import datetime as dt

from . import butler
from .triggers import Cooldown


class Departure:
    def __init__(self, cfg, clock=None, monotonic=None, registry=None, runner=None):
        self.cfg = cfg
        self.clock = clock or dt.datetime.now
        if registry is None:
            registry = dict(butler.PROVIDERS)
            if runner is not None:  # gpu_busy가 주입된 runner를 쓰게 (테스트/커스텀 실행기)
                registry["gpu_busy"] = lambda o, n: butler.gpu_busy(o, n, runner=runner)
        self.registry = registry
        self.cooldown = Cooldown(cfg["cooldown_seconds"], **({"clock": monotonic} if monotonic else {}))

    def checklist(self):
        return butler.assemble_parts(self.cfg["providers"], self.clock(), self.registry)

    def leave(self, source="button"):
        if not self.cfg.get("enabled", True):
            return {"ok": False, "say": "", "items": []}
        if source == "button":
            self.cooldown.touch("departure")  # 버튼은 항상, 대신 직후 모션 재트리거를 막는다
        elif not self.cooldown.try_acquire("departure"):
            return {"ok": False, "cooldown": True, "say": "", "items": []}
        items = self.checklist()
        say = " ".join(x for x in [self.cfg.get("intro"), *items, self.cfg.get("outro")] if x)
        return {"ok": True, "say": say, "items": items}
