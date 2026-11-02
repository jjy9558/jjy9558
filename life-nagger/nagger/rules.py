"""엄마봇 / 엉덩이 경보 / 설거지 잔소리. 규칙은 Snapshot을 받아 Message·Action 목록을 돌려주는 작은 상태기계."""
from datetime import timedelta

from .counters import dish_level, dish_line
from .model import INFO, URGENT, WARN, Action, Message, norm_proc
from .timeutil import fmt_duration, in_window


class MomBot:
    """새벽 게임 엄마봇. 게임을 죽이지 않는다. 잠자기는 allow_sleep: true 일 때만."""
    name = "mombot"

    def __init__(self, cfg, cancel_requested, clear_cancel):
        m = cfg["mombot"]
        self.enabled = m["enabled"]
        self.start, self.end = m["start"], m["end"]
        self.interval = timedelta(minutes=m["interval_minutes"])
        self.allow_sleep = m["allow_sleep"] is True
        self.grace = timedelta(minutes=m["save_grace_minutes"])
        self.countdown = timedelta(seconds=m["countdown_seconds"])
        self.method = m["sleep_method"]
        self.lines = m["lines"][:3]
        self.games = {norm_proc(g) for g in cfg["games"]}
        self.cancel_requested, self.clear_cancel = cancel_requested, clear_cancel
        self.state, self.stage = "idle", 0
        self.next_at = self.deadline = None
        self._cd_sent = set()
        self.fast_poll = False

    def _line(self, i, snap, game):
        return self.lines[i].replace("{game}", game).replace("{time}", snap.now.strftime("%H:%M"))

    def _reset(self):
        self.state, self.stage, self.next_at, self.deadline = "idle", 0, None, None
        self._cd_sent = set()
        self.fast_poll = False

    def tick(self, snap):
        if not self.enabled:
            return []
        now = snap.now
        running = sorted(snap.processes & self.games)
        active = bool(running) and in_window(now, self.start, self.end)
        game = running[0] if running else "게임"
        out = []
        if not active:
            if self.state == "countdown":
                out.append(Message("게임이 꺼졌네. 잠자기 취소. 오늘은 봐준다.", "mombot", INFO))
            self._reset()
            return out
        if self.state == "idle":
            self.state, self.stage, self.next_at = "warning", 1, now + self.interval
            return [Message(self._line(0, snap, game), "mombot", WARN)]
        if self.state == "warning" and now >= self.next_at:
            if self.stage < 3:
                self.stage += 1
                self.next_at = now + (self.interval if self.stage < 3 else self.grace)
                lvl = WARN if self.stage == 2 else URGENT
                msg = Message(self._line(self.stage - 1, snap, game), "mombot", lvl)
                if self.stage == 3 and not self.allow_sleep:
                    self.state = "exhausted"
                return [msg]
            # stage 3 이후 저장 유예 종료
            if self.allow_sleep:
                self.clear_cancel()                      # 예전 취소 깃발이 남아 있으면 안 됨
                self.state, self.deadline = "countdown", now + self.countdown
                self.fast_poll = True
                self._cd_sent = set()
                return [Message("%d초 뒤 PC를 재웁니다. 취소: python -m nagger cancel-sleep (또는 게임 종료)"
                                % int(self.countdown.total_seconds()), "mombot", URGENT)]
            self.state = "exhausted"
            return []
        if self.state == "countdown":
            if self.cancel_requested():
                self.state, self.fast_poll = "cancelled", False
                return [Message("알겠다, 이번만 봐준다. 대신 곧 끈다?", "mombot", INFO)]
            if now >= self.deadline:
                self.state, self.fast_poll = "done", False
                return [Message("잘 자라, 우리 아들/딸.", "mombot", URGENT),
                        Action("suspend", "mombot countdown finished")]
            remaining = (self.deadline - now).total_seconds()
            for mark in (30, 10):
                if remaining <= mark and mark not in self._cd_sent:
                    self._cd_sent.add(mark)
                    return [Message("%d초 남았다. 저장해라." % mark, "mombot", URGENT)]
        return out


class Sitting:
    name = "sitting"

    def __init__(self, cfg):
        s = cfg["sitting"]
        self.enabled = s["enabled"]
        self.limit = timedelta(minutes=s["sit_limit_minutes"])
        self.break_s = s["break_minutes"] * 60
        self.repeat = timedelta(minutes=s["repeat_minutes"])
        self.text, self.praise = s["message"], s["praise"]
        self.streak_start = self.last_alert = self.last_tick = None

    def streak(self, now):
        return (now - self.streak_start) if self.streak_start else timedelta(0)

    def tick(self, snap):
        if not self.enabled:
            return []
        now, out = snap.now, []
        gap = (now - self.last_tick).total_seconds() if self.last_tick else 0
        self.last_tick = now
        if snap.idle_s >= self.break_s or gap >= self.break_s:      # 휴식 감지 (데몬 중단/절전 포함)
            if self.last_alert is not None:
                out.append(Message(self.praise, "sitting", INFO))
            self.streak_start = self.last_alert = None
        if snap.idle_s < self.break_s and self.streak_start is None:
            self.streak_start = now
        if self.streak_start is not None and now - self.streak_start >= self.limit:
            if self.last_alert is None or now - self.last_alert >= self.repeat:
                self.last_alert = now
                out.append(Message("%s (벌써 %s째)" % (self.text, fmt_duration(self.streak(now).total_seconds())),
                                   "sitting", WARN))
        return out


class DishNag:
    name = "dishes"

    def __init__(self, cfg, counters):
        self.enabled = cfg["dishes"]["enabled"]
        self.hour = cfg["dishes"]["remind_hour"]
        self.counters = counters
        self.last_nag_date = None

    def tick(self, snap):
        if not self.enabled or snap.now.hour < self.hour or self.last_nag_date == snap.now.date():
            return []
        days = self.counters.dish_days(snap.now)
        if days is None or days < 1:
            return []
        self.last_nag_date = snap.now.date()
        return [Message(dish_line(days), "dishes", dish_level(days))]
