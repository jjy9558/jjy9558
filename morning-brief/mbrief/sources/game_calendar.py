"""game-calendar 어댑터: state.json 의 entries 에서 점검, 서비스 종료, 이벤트 마감을 읽는다.

형식(game-calendar/gamecal/models.py ``Entry.to_dict``): ``state["entries"]`` 는 {gid: {game, title, url,
category("점검"|"서비스 종료"|"이벤트"|"패치노트"|"기타"), start, end, all_day, is_notice, ...}}.
all_day 이면 start/end 가 ``YYYY-MM-DD``(end 는 포함), 아니면 UTC ``YYYY-MM-DDTHH:MM:SSZ``.
일정을 못 뽑은 공지(is_notice)는 게시일일 뿐이라 건너뛴다.
"""
from __future__ import annotations

import datetime as dt
import json

from ..model import Context, Item, SourceMissing, iso_minutes
from ..speech import ko_time, when_phrase

NAME = "game_calendar"
SECTION = "게임"
DEFAULTS = {"enabled": True, "state": "", "maint_days": 2, "shutdown_days": 30, "event_end_days": 2}

MAINT, SHUTDOWN, EVENT = "점검", "서비스 종료", "이벤트"


def _parse(value, tz):
    """(date, datetime|None). datetime 은 로컬 시간대."""
    if not value:
        return None, None
    s = str(value)
    if "T" in s:
        when = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).astimezone(tz)
        return when.date(), when
    return dt.date.fromisoformat(s), None


def collect(cfg: dict, ctx: Context) -> list[Item]:
    if not cfg.get("state"):
        raise SourceMissing("sources.game_calendar.state (state.json 경로)를 설정하세요")
    path = ctx.path(cfg["state"])
    if not path.is_file():
        raise SourceMissing("state.json 이 없어요: %s" % path.name)
    state = json.loads(path.read_text(encoding="utf-8"))
    entries = state.get("entries") or {}
    today, tz = ctx.today, ctx.tz
    items: list[Item] = []
    for gid in sorted(entries):
        e = entries[gid]
        try:
            if e.get("is_notice", True) or e.get("category") not in (MAINT, SHUTDOWN, EVENT):
                continue
            sd, st = _parse(e.get("start"), tz)
            ed, et = _parse(e.get("end"), tz)
            if sd is None:
                continue
            ed = ed or sd
            items.extend(_entry_items(e, sd, st, ed, et, ctx, cfg))
        except (ValueError, TypeError, AttributeError):
            continue  # 깨진 항목 하나가 전체를 막지 않게
    return items


def _entry_items(e, sd, st, ed, et, ctx, cfg):
    today = ctx.today
    game = str(e.get("game") or "게임")
    title = str(e.get("title") or "")
    url = str(e.get("url") or "")
    actions = ("공지 열기: " + url,) if url else ()
    cat = e["category"]
    days = (sd - today).days
    if cat == MAINT:
        started = sd < today or (sd == today and st is not None and st <= ctx.now)
        ongoing = started and ed >= today
        if ongoing and et is not None and et < ctx.now:
            return []
        if not ongoing and not 0 <= days <= int(cfg["maint_days"]):
            return []
        word = when_phrase(sd if not ongoing else today, today)
        at = ko_time(st.hour, st.minute) if (st and not ongoing) else ""
        until = ""
        if ongoing and et is not None:
            until = " %s까지" % ko_time(et.hour, et.minute)
        elif ongoing and ed > today:
            until = " %s까지" % when_phrase(ed, today)
        state = "점검 중" if ongoing else "점검"
        speak = ("%s %s: %s%s%s" % (word, state, game, (" " + at) if at else "", until)).strip()
        base = 90 if (days <= 0) else (72 if days == 1 else 48)
        due = iso_minutes(st) if st else sd.isoformat()
        end_txt = ""
        if et is not None:
            end_txt = " ~ %s" % et.strftime("%H:%M")
        shown = "%s %s%s" % (game, state, (" " + st.strftime("%H:%M") + end_txt) if st else "")
        return [Item(NAME, "maintenance", "%s (%s)" % (shown, when_phrase(sd, today)), title, base,
                     speak, "%s %s %s" % (word, game, state), due, actions)]
    if cat == SHUTDOWN:
        if not 0 <= days <= int(cfg["shutdown_days"]):
            return []
        near = days <= 7
        base = 92 if days == 0 else 86 if days == 1 else 66 if near else 28
        word = when_phrase(sd, today)
        speak = "서비스 종료%s: %s %s" % ("" if near else " 예정", game, word)
        return [Item(NAME, "shutdown", "%s 서비스 종료 %s" % (game, sd.isoformat()), title, base,
                     speak, "%s 종료 %s" % (game, word), sd.isoformat(), actions)]
    # 이벤트: 오늘 시작하거나, 곧 끝나는 것만
    left = (ed - today).days
    if sd == today and ed >= today:
        return [Item(NAME, "event", "%s 이벤트 시작" % game, title, 40,
                     "이벤트 시작: %s" % game, "", sd.isoformat() if not st else iso_minutes(st), actions)]
    if sd < today <= ed and left <= int(cfg["event_end_days"]):
        return [Item(NAME, "event", "%s 이벤트 마감 %s" % (game, ed.isoformat()), title, 42 - left,
                     "이벤트 마감: %s %s까지" % (game, when_phrase(ed, today)), "", ed.isoformat(), actions)]
    return []
