"""RFC 5545 iCalendar 생성 (줄 접기, 이스케이프, UTC/DATE, VALARM)."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Union

CRLF = "\r\n"
KST = dt.timezone(dt.timedelta(hours=9))
PRODID = "-//paper-butler//Korean paper assistant//KO"


def escape_text(s: str) -> str:
    """TEXT 값 이스케이프: 역슬래시, 세미콜론, 쉼표, 줄바꿈."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    return s


def fold_line(line: str, limit: int = 75) -> str:
    """75 옥텟 기준으로 접는다(UTF-8 문자 중간에서 자르지 않음). 이어지는 줄은 공백으로 시작."""
    if len(line.encode("utf-8")) <= limit:
        return line
    out: List[str] = []
    cur = ""
    cur_bytes = 0
    max_bytes = limit  # 첫 줄
    for ch in line:
        b = len(ch.encode("utf-8"))
        if cur_bytes + b > max_bytes:
            out.append(cur)
            cur, cur_bytes = "", 0
            max_bytes = limit - 1  # 선행 공백 1옥텟
        cur += ch
        cur_bytes += b
    if cur:
        out.append(cur)
    return (CRLF + " ").join(out)


def fmt_utc(d: dt.datetime) -> str:
    """datetime -> 'YYYYMMDDTHHMMSSZ' (naive 는 KST 로 간주)."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=KST)
    return d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fmt_date(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


@dataclass
class VEvent:
    uid: str
    summary: str
    start: Union[dt.date, dt.datetime]
    description: str = ""
    duration_minutes: int = 60            # datetime 이벤트일 때만
    alarm_days_before: Optional[int] = 3  # None 이면 알람 없음
    categories: str = ""


def build_calendar(events: Iterable[VEvent], now: Optional[dt.datetime] = None,
                   name: str = "서류 비서") -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = fmt_utc(now)
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:" + PRODID,
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + escape_text(name),
    ]
    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append("UID:" + re.sub(r"[^A-Za-z0-9@._-]", "-", ev.uid))
        lines.append("DTSTAMP:" + stamp)
        if isinstance(ev.start, dt.datetime):
            lines.append("DTSTART:" + fmt_utc(ev.start))
            end = ev.start + dt.timedelta(minutes=ev.duration_minutes)
            lines.append("DTEND:" + fmt_utc(end))
        else:
            lines.append("DTSTART;VALUE=DATE:" + fmt_date(ev.start))
            lines.append("DTEND;VALUE=DATE:" + fmt_date(ev.start + dt.timedelta(days=1)))
        lines.append("SUMMARY:" + escape_text(ev.summary))
        if ev.description:
            lines.append("DESCRIPTION:" + escape_text(ev.description))
        if ev.categories:
            lines.append("CATEGORIES:" + escape_text(ev.categories))
        lines.append("TRANSP:TRANSPARENT")
        if ev.alarm_days_before is not None:
            lines.append("BEGIN:VALARM")
            lines.append("ACTION:DISPLAY")
            lines.append("DESCRIPTION:" + escape_text(ev.summary))
            lines.append("TRIGGER:-P%dD" % ev.alarm_days_before)
            lines.append("END:VALARM")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return CRLF.join(fold_line(l) for l in lines) + CRLF
