"""현관 출발 체크: 나가기 직전에 날씨·첫 일정·고정 체크 항목·PC 상태를 읽어준다.

귀가 집사(butler)의 provider 구조를 그대로 쓴다: fn(options, now) -> str | None.
"""
import datetime as dt

from . import butler
from .relay import clean_say_text
from .triggers import Cooldown


class Departure:
    def __init__(self, cfg, clock=None, monotonic=None, registry=None, runner=None,
                 max_chars=None):
        self.cfg = cfg
        self.max_chars = max_chars  # say.max_chars: 넘치면 뒤쪽(낮은 우선순위) 항목부터 통째로 뺀다
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
        items, dropped = self._fit(items)
        say = " ".join(x for x in [self.cfg.get("intro"), *items, self.cfg.get("outro")] if x)
        out = {"ok": True, "say": say, "items": items}
        if dropped:
            out["dropped"] = dropped
        return out

    def _fit(self, items):
        """provider 순서 = 우선순위. 글자 수 한도를 넘으면 뒤에서부터 항목을 통째로 뺀다(문장 중간을 자르지 않음).
        맨 앞 항목 하나가 혼자 한도를 넘을 때만 그 항목을 잘라서 남긴다."""
        limit = self.max_chars
        if not limit:
            return items, 0
        fixed = [x for x in (self.cfg.get("intro"), self.cfg.get("outro")) if x]
        used = sum(len(x) + 1 for x in fixed)
        kept = []
        for it in items:
            if used + len(it) + 1 > limit + 1:
                break
            kept.append(it)
            used += len(it) + 1
        if not kept and items:
            kept = [clean_say_text(items[0], max(1, limit - used))]
        return kept, len(items) - len(kept)
