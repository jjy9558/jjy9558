# -*- coding: utf-8 -*-
"""Claude Code 트랜스크립트(JSONL) 읽기와 날짜 필터링."""
import json
import os
import re
from collections import namedtuple
from datetime import datetime, time, timedelta, timezone

# role: "user"(사람이 입력한 프롬프트) | "assistant"
Entry = namedtuple("Entry", "ts role cwd session_id texts tools")

_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?\s*(Z|z|[+-]\d{2}:?\d{2})?$"
)

# 사람이 쓴 게 아닌 주입 텍스트의 시작 패턴
_NOISE_PREFIXES = (
    "Caveat:",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "<user-prompt-submit-hook>",
    "<ide_opened_file>",
    "<ide_selection>",
    "[Request interrupted",
    "This session is being continued from a previous conversation",
)
_REMINDER_BLOCK = re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I)


def default_projects_dir():
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    if base:
        return os.path.join(base, "projects")
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


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


def parse_timestamp(value):
    """ISO 8601 문자열 -> aware datetime(UTC 기준). 실패 시 None."""
    if not isinstance(value, str):
        return None
    m = _TS_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
    frac = (m.group(7) or "0")[:6].ljust(6, "0")
    zone = m.group(8)
    if zone in (None, "Z", "z"):
        tz = timezone.utc
    else:
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    try:
        return datetime(y, mo, d, h, mi, s, int(frac), tzinfo=tz)
    except ValueError:
        return None


def to_local(dt, tz=None):
    """tz가 None이면 시스템 로컬 타임존으로 변환."""
    return dt.astimezone(tz) if tz is not None else dt.astimezone()


def local_today(tz=None):
    return to_local(datetime.now(timezone.utc), tz).date()


def clean_text(text):
    """주입/노이즈 텍스트면 None, 아니면 정리된 텍스트."""
    if not isinstance(text, str):
        return None
    t = text
    if "<system-reminder" in t.lower():
        t = _REMINDER_BLOCK.sub("", t)
        if "<system-reminder" in t.lower():  # 닫히지 않은 태그
            return None
    t = t.strip()
    if not t:
        return None
    for p in _NOISE_PREFIXES:
        if t.startswith(p):
            return None
    return t


def _extract(obj):
    """JSON 객체 -> (role, texts, tools) 또는 None."""
    typ = obj.get("type")
    if typ not in ("user", "assistant"):
        return None
    if obj.get("isSidechain") or obj.get("isMeta") or obj.get("isCompactSummary"):
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    if msg.get("model") == "<synthetic>":
        return None
    content = msg.get("content")
    texts, tools = [], []
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = content
    else:
        return None
    for b in blocks:
        if isinstance(b, str):
            b = {"type": "text", "text": b}
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            t = clean_text(b.get("text"))
            if t:
                texts.append(t)
        elif bt == "tool_use" and typ == "assistant":
            name = b.get("name")
            inp = b.get("input")
            if isinstance(name, str) and name:
                tools.append((name, inp if isinstance(inp, dict) else {}))
        # tool_result / thinking / image 등은 버린다
    if not texts and not tools:
        return None
    return typ, texts, tools


def iter_jsonl_files(projects_dir):
    root = str(projects_dir)
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "subagents")
        for fn in sorted(filenames):
            if fn.endswith(".jsonl"):
                yield os.path.join(dirpath, fn)


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
    entries = []
    try:
        cutoff = _day_start_epoch(day, tz) - 86400
    except (OverflowError, OSError, ValueError):
        cutoff = None
    for path in iter_jsonl_files(projects_dir):
        try:
            if cutoff is not None and os.path.getmtime(path) < cutoff:
                continue  # 그 날 이전에 마지막으로 수정된 파일은 볼 필요 없음
        except OSError:
            continue
        stats["files"] += 1
        sid_default = os.path.splitext(os.path.basename(path))[0]
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                stats["lines"] += 1
                try:
                    obj = json.loads(line)
                except ValueError:
                    stats["malformed"] += 1
                    continue
                if not isinstance(obj, dict):
                    stats["malformed"] += 1
                    continue
                ts = parse_timestamp(obj.get("timestamp"))
                if ts is None:
                    continue
                if to_local(ts, tz).date() != day:
                    continue
                got = _extract(obj)
                if got is None:
                    continue
                role, texts, tools = got
                cwd = obj.get("cwd") if isinstance(obj.get("cwd"), str) else ""
                sid = obj.get("sessionId")
                if not isinstance(sid, str) or not sid:
                    sid = sid_default
                entries.append(Entry(ts, role, cwd, sid, texts, tools))
    entries.sort(key=lambda e: e.ts)
    stats["entries"] = len(entries)
    return entries


def parse_date(value):
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()
