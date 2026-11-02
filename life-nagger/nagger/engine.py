import json
import os
import time

from .config import state_path
from .counters import Counters
from .fakes import SystemClock
from .model import Action, Message, Snapshot, norm_proc
from .outputs import build_outputs
from .rules import DishNag, MomBot, Sitting
from .worktime import WorkTracker

CANCEL_FLAG = "cancel_sleep.flag"


class Nagger:
    def __init__(self, cfg, platform, clock=None, notifier=None, dry_run=False, write=print):
        self.cfg, self.platform = cfg, platform
        self.clock = clock or SystemClock()
        self.dry_run = dry_run
        self.notifier = notifier or build_outputs(cfg, platform, write)
        self.cancel_path = state_path(cfg, CANCEL_FLAG)
        self.status_path = state_path(cfg, "status.json")
        self.counters = Counters(state_path(cfg, "counters.jsonl"), cfg["counters"])
        self.mombot = MomBot(cfg, self._cancel_requested, self._clear_cancel)
        self.sitting = Sitting(cfg)
        self.work = WorkTracker(cfg, state_path(cfg, "work.json"))
        self.dishes = DishNag(cfg, self.counters)
        self.rules = [self.mombot, self.sitting, self.work, self.dishes]
        self.errors = []

    # ---- 취소 깃발 (python -m nagger cancel-sleep 이 만든다) ----
    def _cancel_requested(self):
        if os.path.exists(self.cancel_path):
            self._clear_cancel()
            return True
        return False

    def _clear_cancel(self):
        try:
            os.remove(self.cancel_path)
        except FileNotFoundError:
            pass

    def _err(self, what, e):
        self.errors.append("%s: %s" % (what, e))
        del self.errors[:-20]

    def snapshot(self):
        p, now = self.platform, self.clock.now()
        try:
            idle = p.idle_seconds()
        except Exception as e:
            self._err("idle", e)
            idle = 1e9           # 모르면 '자리에 없음'으로 간주 (잔소리/근무 기록을 하지 않는 쪽이 안전)
        try:
            fg = p.foreground_process()
        except Exception as e:
            self._err("foreground", e)
            fg = None
        try:
            procs = frozenset(norm_proc(x) for x in p.running_processes())
        except Exception as e:
            self._err("processes", e)
            procs = frozenset()
        return Snapshot(now, idle, fg, procs)

    def _execute(self, action):
        # 이중 안전장치: 설정이 true(불리언)가 아니면 어떤 규칙이 요청해도 재우지 않는다.
        if action.kind != "suspend" or self.cfg["mombot"]["allow_sleep"] is not True:
            return
        if self.dry_run:
            self.notifier.send(Message("(dry-run) 여기서 PC를 재웠을 겁니다.", "mombot"), self.clock.now())
            return
        try:
            self.platform.suspend(self.cfg["mombot"]["sleep_method"])
        except Exception as e:
            self._err("suspend", e)

    def tick(self):
        snap = self.snapshot()
        for rule in self.rules:
            try:
                items = rule.tick(snap)
            except Exception as e:
                self._err(rule.name, e)
                continue
            for it in items:
                if isinstance(it, Action):
                    self._execute(it)
                else:
                    self.notifier.send(it, snap.now)
        self._write_status(snap)
        return snap

    def _write_status(self, snap):
        now = snap.now
        st = {"updated": now.isoformat(timespec="seconds"), "poll_seconds": self.cfg["poll_seconds"],
              "foreground": snap.foreground, "idle_seconds": int(min(snap.idle_s, 10 ** 7)),
              "mombot": {"state": self.mombot.state, "stage": self.mombot.stage,
                         "allow_sleep": self.mombot.allow_sleep},
              "sitting_minutes": int(self.sitting.streak(now).total_seconds() // 60),
              "work_kind": self.work.last_kind, "work_today": self.work.today(now),
              "dish_days": self.counters.dish_days(now), "errors": self.errors[-5:]}
        tmp = self.status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, self.status_path)

    def poll_interval(self):
        base = self.cfg["poll_seconds"]
        return min(base, 5) if self.mombot.fast_poll else base

    def run(self, max_ticks=None, sleep=time.sleep):
        n = 0
        while max_ticks is None or n < max_ticks:
            self.tick()
            n += 1
            if max_ticks is not None and n >= max_ticks:
                break
            sleep(self.poll_interval())
