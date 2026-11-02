"""테스트/시연용 가짜 시계와 가짜 플랫폼."""
from datetime import datetime, timedelta

from .model import norm_proc
from .platform import Platform


class FakeClock:
    def __init__(self, start):
        self._now = start

    def now(self):
        return self._now

    def advance(self, seconds=0, minutes=0, hours=0):
        self._now += timedelta(seconds=seconds, minutes=minutes, hours=hours)
        return self._now

    def set(self, dt):
        self._now = dt


class SystemClock:
    def now(self):
        return datetime.now()


class FakePlatform(Platform):
    def __init__(self):
        self.idle = 0.0
        self.foreground = None
        self.processes = set()
        self.suspends = []
        self.toasts = []
        self.toast_ok = True
        self.fail = set()      # {"idle","fg","procs"} 중 실패시킬 것

    def idle_seconds(self):
        if "idle" in self.fail:
            raise OSError("idle fail")
        return self.idle

    def foreground_process(self):
        if "fg" in self.fail:
            raise OSError("fg fail")
        return norm_proc(self.foreground) if self.foreground else None

    def running_processes(self):
        if "procs" in self.fail:
            raise OSError("procs fail")
        return frozenset(norm_proc(p) for p in self.processes)

    def suspend(self, method="suspend"):
        self.suspends.append(method)

    def toast(self, title, body, method="auto"):
        self.toasts.append((title, body))
        return self.toast_ok
