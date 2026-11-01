"""RFC 5545 iCalendar 생성 (VTIMEZONE 없음: UTC 시각 또는 DATE)."""
import datetime as dt

from . import classify as C
from .dates import UTC

CRLF = "\r\n"


def escape_text(s):
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n"))


def fold(line):
    """75 옥텟 초과 시 CRLF+공백으로 접기 (UTF-8 문자 중간에서 자르지 않음)."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts, cur, cur_len, limit = [], "", 0, 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if cur_len + n > limit:
            parts.append(cur)
            cur, cur_len, limit = ch, n, 74
        else:
            cur += ch
            cur_len += n
    parts.append(cur)
    return (CRLF + " ").join(parts)


def _utc(d):
    return d.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _date(d):
    return d.strftime("%Y%m%d")


def vevent(e):
    L = ["BEGIN:VEVENT",
         "UID:%s@gamecal" % e.gid.replace(" ", "_"),
         "DTSTAMP:" + _utc(dt.datetime.fromtimestamp(e.post_ts, UTC))]
    if e.all_day:
        end = (e.end or e.start) + dt.timedelta(days=1)  # DTEND 배타적
        L += ["DTSTART;VALUE=DATE:" + _date(e.start), "DTEND;VALUE=DATE:" + _date(end),
              "TRANSP:TRANSPARENT"]
    else:
        end = e.end or e.start + dt.timedelta(hours=1)
        L += ["DTSTART:" + _utc(e.start), "DTEND:" + _utc(end)]
    label = "[%s] %s: %s" % (e.category, e.game, e.title)
    L.append("SUMMARY:" + escape_text(label))
    desc = []
    if e.is_notice:
        desc.append("(일정 미확인 - 게시일 공지)")
    desc += ["- " + s for s in e.summary]
    if e.url:
        desc.append("원문: " + e.url)
    L.append("DESCRIPTION:" + escape_text("\n".join(desc)))
    if e.url:
        L.append("URL:" + e.url)
    L.append("CATEGORIES:" + escape_text(e.category))
    if e.category in C.ALARM_CATEGORIES:
        L += ["BEGIN:VALARM", "ACTION:DISPLAY",
              "DESCRIPTION:" + escape_text("내일: %s %s" % (e.game, e.category)),
              "TRIGGER:-P1D", "END:VALARM"]
    L.append("END:VEVENT")
    return L


def build_ics(entries, name="내 게임 캘린더"):
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//gamecal//KO//", "CALSCALE:GREGORIAN",
         "METHOD:PUBLISH", "X-WR-CALNAME:" + escape_text(name), "X-WR-TIMEZONE:UTC",
         "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for e in sorted(entries, key=lambda x: (x.start_date_kst(), x.gid)):
        L += vevent(e)
    L.append("END:VCALENDAR")
    return CRLF.join(fold(x) for x in L) + CRLF
