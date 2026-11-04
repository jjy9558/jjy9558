# -*- coding: utf-8 -*-
"""Claude Code 트랜스크립트(JSONL) 읽기와 날짜 필터링."""
import os
import re
from collections import namedtuple
from datetime import datetime, time, timedelta, timezone

from ._jjkit import claude_logs
from ._jjkit.claude_logs import clean_text, parse_timestamp

# role: "user"(사람이 입력한 프롬프트) | "assistant"
Entry = namedtuple("Entry", "ts role cwd session_id texts tools")


def default_projects_dir():
    return str(claude_logs.default_projects_dir())


def parse_tz_offset(value):
    """'+09:00', '9', '-5', '+0530', 'UTC' -> timezone. None/'' -> None(시스템 로컬)."""
    if value is None or str(value).strip() == "":
        return None
    v = str(value).strip()
    if v.upper() in ("Z", "UTC"):
        return timezone.utc
    m = re.match(r"^([+-]?)(\d{1,2})(?::?(\d{2}))?$", v)
    if not m:
        raise ValueError("잘못된 --tz-offset 값: %r (예: +09:00, 9, -5)" % value)
    hours = int(m.group(2))
    minutes = int(m.group(3) or 0)
    if hours > 14 or minutes > 59:
        raise ValueError("잘못된 --tz-offset 값: %r" % value)
    delta = timedelta(hours=hours, minutes=minutes)
    if m.group(1) == "-":
        delta = -delta
    return timezone(delta)


def to_local(dt, tz=None):
    """tz가 None이면 시스템 로컬 타임존으로 변환."""
    return dt.astimezone(tz) if tz is not None else dt.astimezone()


def local_today(tz=None):
    return to_local(datetime.now(timezone.utc), tz).date()


def _day_start_epoch(day, tz):
    start = datetime.combine(day, time.min)
    if tz is not None:
        start = start.replace(tzinfo=tz)
    return start.timestamp()  # naive 는 시스템 로컬로 해석된다


def load_entries(projects_dir, day, tz=None, stats=None):
    """``day``(로컬 날짜)에 해당하는 user/assistant 항목을 시간순으로 반환."""
    if stats is None:
        stats = {}
    for k in ("files", "lines", "malformed", "entries"):
        stats.setdefault(k, 0)
    try:
        cutoff = _day_start_epoch(day, tz) - 86400
    except (OverflowError, OSError, ValueError):
        cutoff = None
    entries = []
    for path, obj in claude_logs.iter_records(projects_dir, stats=stats, modified_after=cutoff):
        ts = parse_timestamp(obj.get("timestamp"))
        if ts is None or to_local(ts, tz).date() != day:
            continue
        got = claude_logs.extract_message(obj)
        if got is None:
            continue
        role, texts, tools = got
        cwd = obj.get("cwd") if isinstance(obj.get("cwd"), str) else ""
        sid = obj.get("sessionId")
        if not isinstance(sid, str) or not sid:
            sid = path.stem
        entries.append(Entry(ts, role, cwd, sid, texts, tools))
    entries.sort(key=lambda e: e.ts)
    stats["entries"] = len(entries)
    return entries


def parse_date(value):
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()
