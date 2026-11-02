"""재택 출근 도장: 포그라운드 앱 기준 순수 작업 시간 누적. 자정을 넘는 구간은 날짜별로 쪼갠다."""
import json
import os
from datetime import datetime, timedelta

from .model import INFO, Message, norm_proc
from .timeutil import fmt_duration, next_midnight

TIME_FMT = "%H:%M:%S"


class WorkTracker:
    name = "work"

    def __init__(self, cfg, path):
        w = cfg["work"]
        self.enabled = w["enabled"]
        self.max_gap = w["max_gap_seconds"]
        self.clockout_after = timedelta(minutes=w["clockout_after_minutes"])
        self.present_idle = cfg["present_idle_seconds"]
        self.work_apps = {norm_proc(a) for a in cfg["work_apps"]}
        self.game_apps = {norm_proc(a) for a in cfg["games"]}
        self.path = path
        self.data = self._load()
        self._last_work_at = None          # 메모리: 마지막으로 work로 분류된 시각
        self._clocked_out = False
        self.last_kind = None

    # ---- 저장 ----
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("days", {})
            return d
        except (FileNotFoundError, ValueError):
            return {"days": {}, "last_tick": None}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ---- 분류 ----
    def classify(self, snap):
        if snap.idle_s >= self.present_idle:
            return "idle"
        fg = snap.foreground or ""
        if fg in self.work_apps:
            return "work"
        if fg in self.game_apps:
            return "game"
        return "other"

    def _day(self, d):
        return self.data["days"].setdefault(d, {"work": 0.0, "game": 0.0, "other": 0.0})

    def _add_interval(self, start, end, kind):
        """[start,end)를 날짜별로 나눠 더한다. 새로 출근한 날 목록을 돌려준다."""
        new_in = []
        cur = start
        while cur < end:
            seg_end = min(end, next_midnight(cur))
            secs = (seg_end - cur).total_seconds()
            day = self._day(cur.date().isoformat())
            if kind in ("work", "game", "other"):
                day[kind] += secs
            if kind == "work":
                if "first_in" not in day:
                    day["first_in"] = cur.strftime(TIME_FMT)
                    new_in.append((cur.date(), cur == start))
                last = seg_end - timedelta(seconds=1) if seg_end == next_midnight(cur) else seg_end
                day["last_work"] = last.strftime(TIME_FMT)
            cur = seg_end
        return new_in

    # ---- 틱 ----
    def tick(self, snap):
        if not self.enabled:
            return []
        now, msgs = snap.now, []
        kind = self.classify(snap)
        prev_kind, self.last_kind = self.last_kind, kind
        prev = self.data.get("last_tick")
        prev = datetime.fromisoformat(prev) if prev else None
        self.data["last_tick"] = now.isoformat()
        if prev is not None and now > prev:
            gap = (now - prev).total_seconds()
            if gap <= self.max_gap:      # 절전/데몬 중단 구간은 일하지 않은 것으로 본다
                new_in = self._add_interval(prev, now, kind)
                for d, began_here in new_in:
                    if d == now.date() and began_here and prev_kind != "work":
                        msgs.append(Message("출근 도장 쾅! %s 출근. 오늘도 순수하게 일해봅시다."
                                            % prev.strftime("%H:%M"), "work", INFO))
        if kind == "work":
            self._last_work_at = now
            self._clocked_out = False
        elif (self._last_work_at is not None and not self._clocked_out
              and now - self._last_work_at >= self.clockout_after):
            self._clocked_out = True
            day = self.data["days"].get(self._last_work_at.date().isoformat(), {})
            msgs.append(Message("퇴근 도장 쾅. %s 퇴근. 오늘 순수 작업 %s." % (
                self._last_work_at.strftime("%H:%M"), fmt_duration(day.get("work", 0))), "work", INFO))
        self._save()
        return msgs

    def today(self, now):
        return self.data["days"].get(now.date().isoformat(), {"work": 0.0, "game": 0.0, "other": 0.0})
