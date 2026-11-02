"""RFC 5545 iCalendar 쓰기와 읽기 (VEVENT만, VTIMEZONE 없음).

기준 구현: game-calendar/gamecal/ics.py, paper-butler/paperbutler/ics.py(쓰기),
s21-sidekick/sidekick/butler.py ``parse_ics_events``(읽기).

- 쓰기: 시각은 UTC(``...Z``), 종일 일정은 ``VALUE=DATE``(DTEND는 다음 날, 배타적).
  naive datetime은 ``default_tz``(기본 KST)로 본다. 75옥텟 줄 접기(UTF-8 글자 중간에서 안 자름).
- 읽기: 줄 펴기, 이스케이프 해제, ``DTSTART``/``DTEND``(Z, TZID, 날짜, 떠 있는 시각),
  ``VALARM`` 안의 속성은 무시. 반복 일정(RRULE)은 펼치지 않고 ``Event.rrule``에 원문만 남긴다.
"""
import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Iterable

__all__ = ["Event", "KST", "escape_text", "unescape_text", "fold_line", "unfold",
           "build_calendar", "parse_calendar"]

CRLF = "\r\n"
KST = dt.timezone(dt.timedelta(hours=9), "KST")
_FIXED_TZ = {"asia/seoul": KST, "utc": dt.timezone.utc, "etc/utc": dt.timezone.utc,
             "gmt": dt.timezone.utc, "asia/tokyo": dt.timezone(dt.timedelta(hours=9))}


@dataclass
class Event:
    uid: str
    summary: str
    start: dt.date | dt.datetime
    end: dt.date | dt.datetime | None = None   # None이면 시각 일정은 +duration, 종일은 다음 날
    description: str = ""
    location: str = ""
    url: str = ""
    categories: list[str] = field(default_factory=list)
    alarm_before: dt.timedelta | None = None    # 예: timedelta(days=1) -> TRIGGER:-P1D
    transparent: bool = False                   # True면 '바쁨'으로 표시하지 않음
    duration: dt.timedelta = dt.timedelta(hours=1)
    rrule: str = ""                             # 읽기 전용 정보(쓰기 시에도 그대로 출력)

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, dt.datetime)


# --------------------------------------------------------------------------
def escape_text(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def unescape_text(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def fold_line(line: str, limit: int = 75) -> str:
    """``limit`` 옥텟 기준으로 접는다. 이어지는 줄은 공백 1옥텟으로 시작한다."""
    if len(line.encode("utf-8")) <= limit:
        return line
    parts, cur, cur_len, cap = [], "", 0, limit
    for ch in line:
        n = len(ch.encode("utf-8"))
        if cur_len + n > cap:
            parts.append(cur)
            cur, cur_len, cap = "", 0, limit - 1
        cur += ch
        cur_len += n
    if cur:
        parts.append(cur)
    return (CRLF + " ").join(parts)


def unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def _utc(d: dt.datetime, default_tz: dt.tzinfo) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=default_tz)
    return d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _date(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def _uid(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9@._-]", "-", s) or "event"


def _duration(td: dt.timedelta) -> str:
    secs = int(abs(td.total_seconds()))
    days, rem = divmod(secs, 86400)
    if rem == 0:
        return "P%dD" % days
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    out = "P%dD" % days if days else "P"
    out += "T"
    if h:
        out += "%dH" % h
    if m:
        out += "%dM" % m
    if s:
        out += "%dS" % s
    return out


def build_calendar(events: Iterable[Event], *, name: str = "", prodid: str = "-//jjkit//KO",
                   now: dt.datetime | None = None, default_tz: dt.tzinfo = KST,
                   refresh: str = "") -> str:
    """VCALENDAR 문자열(CRLF 줄끝). ``refresh="PT6H"``면 구독 새로고침 힌트를 넣는다."""
    stamp = _utc(now or dt.datetime.now(dt.timezone.utc), default_tz)
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:" + prodid, "CALSCALE:GREGORIAN",
         "METHOD:PUBLISH"]
    if name:
        L.append("X-WR-CALNAME:" + escape_text(name))
    if refresh:
        L += ["REFRESH-INTERVAL;VALUE=DURATION:" + refresh, "X-PUBLISHED-TTL:" + refresh]
    for e in events:
        L += ["BEGIN:VEVENT", "UID:" + _uid(e.uid), "DTSTAMP:" + stamp]
        if e.all_day:
            start = e.start if not isinstance(e.start, dt.datetime) else e.start.date()
            end = e.end or start + dt.timedelta(days=1)
            if isinstance(end, dt.datetime):
                end = end.date()
            L += ["DTSTART;VALUE=DATE:" + _date(start), "DTEND;VALUE=DATE:" + _date(end)]
        else:
            end = e.end or e.start + e.duration
            if not isinstance(end, dt.datetime):
                end = dt.datetime.combine(end, dt.time.min)
            L += ["DTSTART:" + _utc(e.start, default_tz), "DTEND:" + _utc(end, default_tz)]
        if e.rrule:
            L.append("RRULE:" + e.rrule)
        L.append("SUMMARY:" + escape_text(e.summary))
        if e.description:
            L.append("DESCRIPTION:" + escape_text(e.description))
        if e.location:
            L.append("LOCATION:" + escape_text(e.location))
        if e.url:
            L.append("URL:" + e.url)
        if e.categories:
            L.append("CATEGORIES:" + ",".join(escape_text(c) for c in e.categories))
        if e.transparent:
            L.append("TRANSP:TRANSPARENT")
        if e.alarm_before is not None:
            L += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + escape_text(e.summary),
                  "TRIGGER:-" + _duration(e.alarm_before), "END:VALARM"]
        L.append("END:VEVENT")
    L.append("END:VCALENDAR")
    return CRLF.join(fold_line(x) for x in L) + CRLF


# --------------------------------------------------------------------------
def _split_prop(line: str) -> tuple[str, dict[str, str], str]:
    """'DTSTART;TZID=Asia/Seoul:2026...' -> ('DTSTART', {'TZID': 'Asia/Seoul'}, '2026...')."""
    in_q = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == ":" and not in_q:
            head, val = line[:i], line[i + 1:]
            break
    else:
        return line.upper(), {}, ""
    bits = head.split(";")
    params = {}
    for b in bits[1:]:
        k, _, v = b.partition("=")
        params[k.upper()] = v.strip('"')
    return bits[0].upper(), params, val


def _tz(name: str, default_tz: dt.tzinfo | None) -> dt.tzinfo | None:
    key = name.strip().lower()
    if key in _FIXED_TZ:
        return _FIXED_TZ[key]
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name.strip())
    except Exception:  # tzdata가 없는 Windows 등
        return default_tz


def _parse_dt(val: str, params: dict[str, str],
              default_tz: dt.tzinfo | None) -> dt.date | dt.datetime:
    v = val.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", v):
        return dt.datetime.strptime(v[:8], "%Y%m%d").date()
    m = re.fullmatch(r"(\d{8}T\d{4})(\d{2})?(Z)?", v)
    if not m:
        raise ValueError("날짜 형식 오류: %r" % v)
    d = dt.datetime.strptime(m.group(1) + (m.group(2) or "00"), "%Y%m%dT%H%M%S")
    if m.group(3):
        return d.replace(tzinfo=dt.timezone.utc)
    tz = _tz(params["TZID"], default_tz) if "TZID" in params else default_tz
    return d.replace(tzinfo=tz) if tz is not None else d


def parse_calendar(text: str, *, default_tz: dt.tzinfo | None = KST) -> list[Event]:
    """VEVENT 목록. 날짜 형식이 깨진 일정은 건너뛴다. ``default_tz=None``이면 떠 있는 시각은 naive."""
    events: list[Event] = []
    cur: dict | None = None
    depth = 0  # VEVENT 안의 하위 컴포넌트(VALARM 등)
    for line in unfold(text):
        name, params, val = _split_prop(line)
        if name == "BEGIN":
            if val.upper() == "VEVENT":
                cur, depth = {"categories": []}, 0
            elif cur is not None:
                depth += 1
            continue
        if name == "END":
            if val.upper() == "VEVENT" and cur is not None:
                if "start" in cur:
                    events.append(Event(
                        uid=cur.get("uid", ""), summary=cur.get("summary", ""), start=cur["start"],
                        end=cur.get("end"), description=cur.get("description", ""),
                        location=cur.get("location", ""), url=cur.get("url", ""),
                        categories=cur["categories"], alarm_before=cur.get("alarm"),
                        transparent=cur.get("transparent", False), rrule=cur.get("rrule", "")))
                cur = None
            elif cur is not None and depth:
                depth -= 1
            continue
        if cur is None:
            continue
        if depth:
            if name == "TRIGGER" and "alarm" not in cur:
                cur["alarm"] = _parse_trigger(val)
            continue
        try:
            match name:
                case "UID":
                    cur["uid"] = val.strip()
                case "SUMMARY" | "DESCRIPTION" | "LOCATION":
                    cur[name.lower()] = unescape_text(val)
                case "URL":
                    cur["url"] = val.strip()
                case "CATEGORIES":
                    cur["categories"] += [unescape_text(c).strip()
                                          for c in re.split(r"(?<!\\),", val) if c.strip()]
                case "DTSTART":
                    cur["start"] = _parse_dt(val, params, default_tz)
                case "DTEND":
                    cur["end"] = _parse_dt(val, params, default_tz)
                case "TRANSP":
                    cur["transparent"] = val.strip().upper() == "TRANSPARENT"
                case "RRULE":
                    cur["rrule"] = val.strip()
        except ValueError:
            if name == "DTSTART":
                cur.pop("start", None)
    return events


def _parse_trigger(val: str) -> dt.timedelta | None:
    m = re.fullmatch(r"-?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", val.strip())
    if not m or not val.strip().startswith("-"):
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    return dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
