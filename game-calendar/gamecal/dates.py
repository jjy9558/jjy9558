"""본문에서 날짜/시각을 휴리스틱으로 추출하고 KST로 변환."""
import datetime as dt
import re
from dataclasses import dataclass
from typing import List, Optional

UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
           "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MON = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
        r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_TZ = r"(?:UTC|GMT|KST|JST|PST|PDT|EST|EDT|CST|CDT|CET|CEST|PT|ET|Z)"
_AP = r"[ap]\.?m\.?(?![a-z])"

_FIXED = {"UTC": 0, "GMT": 0, "Z": 0, "KST": 9, "JST": 9, "PST": -8, "PDT": -7, "EST": -5,
          "EDT": -4, "CST": -6, "CDT": -5, "CET": 1, "CEST": 2}


def us_dst(d):
    """미국 서머타임 여부 (3월 둘째 일요일 ~ 11월 첫째 일요일, 날짜 단위 근사)."""
    def nth_sunday(y, m, n):
        first = dt.date(y, m, 1)
        off = (6 - first.weekday()) % 7
        return first + dt.timedelta(days=off + 7 * (n - 1))
    return nth_sunday(d.year, 3, 2) <= d < nth_sunday(d.year, 11, 1)


def tz_offset_hours(tz, d):
    tz = tz.upper()
    if tz in _FIXED:
        return _FIXED[tz]
    if tz == "PT":
        return -7 if us_dst(d) else -8
    if tz == "ET":
        return -4 if us_dst(d) else -5
    raise ValueError(tz)


@dataclass
class Found:
    date: dt.date                      # 시각 있으면 KST 날짜, 없으면 적힌 날짜
    has_time: bool = False
    start: Optional[dt.datetime] = None  # UTC aware
    end: Optional[dt.datetime] = None
    pos: int = 0


_P_ISO = re.compile(r"(?<![\d.])(?P<y>20\d{2})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})(?!\d)(?:T(?=\d))?")
_P_KO = re.compile(r"(?:(?P<y>20\d{2})\s*년\s*)?(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일")
_P_MDY = re.compile(r"(?<![A-Za-z])(?P<mon>%s)\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?(?![\d:])"
                    r"(?:\s*,?\s*(?P<y>20\d{2})(?![\d:]))?" % _MON, re.I)
_P_DMY = re.compile(r"(?<![\w])(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>%s)\b\.?"
                    r"(?:,?\s*(?P<y>20\d{2})(?![\d:]))?" % _MON, re.I)
_P_SLASH = re.compile(r"(?<![\d./\w-])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{4}|\d{2}))?(?![\d/])")

_TIME_BODY = r"(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?(?::\d{2})?\s*(?P<ap>%s)?\s*\(?(?P<tz>%s\b)?\)?" % (_AP, _TZ)
_FWD = re.compile(r"^[\s,]*(?:at|@|from|starting|beginning|by)?\s*" + _TIME_BODY, re.I)
_FWD_KO = re.compile(r"^\s*(?:\([^)]*\))?\s*(?:(?P<ap>오전|오후)\s*)?(?P<h>\d{1,2})"
                     r"(?:\s*:\s*(?P<mi>\d{2})|\s*시(?:\s*(?P<mi2>\d{1,2})\s*분)?)\s*\(?(?P<tz>%s\b)?\)?" % _TZ)
_END = re.compile(r"^\s*(?:-|–|—|~|to|until)\s*" + _TIME_BODY, re.I)
_BACK = re.compile(r"(?<![\d:.])" + _TIME_BODY + r"\s*(?:on|,|-|–)?\s*"
                   r"(?:(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\.?,?\s*)?$", re.I)


def _read_time(m, korean=False):
    """-> (h, mi, tz or None) 또는 None. 시각으로 보기에 근거(콜론/ampm/tz)가 부족하면 None."""
    gd = m.groupdict()
    h = int(gd["h"])
    mi = int(gd.get("mi") or gd.get("mi2") or 0)
    ap = (gd.get("ap") or "").lower().replace(".", "")
    tz = gd.get("tz")
    if tz is not None and not tz.isupper():
        tz = None
    if korean:
        if ap == "오후" and h < 12:
            h += 12
        elif ap == "오전" and h == 12:
            h = 0
        tz = tz or "KST"
    else:
        if not (gd.get("mi") or ap or tz):
            return None
        if ap:
            if not 1 <= h <= 12:
                return None
            if ap.startswith("p") and h < 12:
                h += 12
            elif ap.startswith("a") and h == 12:
                h = 0
    if h > 23 or mi > 59:
        return None
    return h, mi, tz


def _infer_year(m, d, post_date):
    try:
        cand = dt.date(post_date.year, m, d)
    except ValueError:
        return None
    if cand < post_date - dt.timedelta(days=60):
        return post_date.year + 1
    return post_date.year


def _raw_dates(text, post_date):
    """[(start, end, y, m, d, kind)] 겹침 제거, 위치순."""
    raw = []
    for kind, rx in (("iso", _P_ISO), ("ko", _P_KO), ("mdy", _P_MDY), ("dmy", _P_DMY), ("slash", _P_SLASH)):
        for m in rx.finditer(text):
            gd = m.groupdict()
            if "mon" in gd:
                mo = _MONTHS[gd["mon"][:3].lower()]
            else:
                mo = int(gd["m"])
            d = int(gd["d"])
            y = gd.get("y")
            if y:
                y = int(y)
                if y < 100:
                    y += 2000
            else:
                y = _infer_year(mo, d, post_date) if 1 <= mo <= 12 else None
            if y is None:
                continue
            try:
                dt.date(y, mo, d)
            except ValueError:
                continue
            raw.append((m.start(), m.end(), y, mo, d, kind))
    raw.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    out = []
    last_end = -1
    for r in raw:
        if r[0] < last_end:
            continue
        out.append(r)
        last_end = r[1]
    return out


def _to_utc(y, mo, d, h, mi, tz, default_tz):
    base = dt.date(y, mo, d)
    off = tz_offset_hours(tz or default_tz, base)
    local = dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone(dt.timedelta(hours=off)))
    return local.astimezone(UTC)


def extract_datetimes(text, post_date, default_tz="UTC"):
    """텍스트 속 날짜(+시각)를 위치순으로 반환. post_date는 연도 추정용 KST 날짜."""
    found = []
    prev_end = 0
    for (s, e, y, mo, d, kind) in _raw_dates(text, post_date):
        tail = text[e:e + 60]
        tm = None
        end_i = e
        korean = kind == "ko"
        m = (_FWD_KO.match(tail) if korean else None) or _FWD.match(tail)
        if m:
            tm = _read_time(m, korean=m.re is _FWD_KO)
            if tm:
                end_i = e + m.end()
        if not tm:
            window_start = max(prev_end, s - 30)
            bm = _BACK.search(text[window_start:s])
            if bm:
                tm = _read_time(bm)
        if not tm:
            found.append(Found(date=dt.date(y, mo, d), pos=s))
            prev_end = max(prev_end, e)
            continue
        h, mi, tz = tm
        end_utc = None
        em = _END.match(text[end_i:end_i + 40])
        if em:
            et = _read_time(em)
            if et:
                tz = tz or et[2]
                end_utc = (et[0], et[1], et[2] or tz)
                end_i += em.end()
        start_utc = _to_utc(y, mo, d, h, mi, tz, default_tz)
        end_dt = None
        if end_utc:
            end_dt = _to_utc(y, mo, d, end_utc[0], end_utc[1], end_utc[2], default_tz)
            if end_dt <= start_utc:
                end_dt += dt.timedelta(days=1)
        found.append(Found(date=start_utc.astimezone(KST).date(), has_time=True,
                           start=start_utc, end=end_dt, pos=s))
        prev_end = max(prev_end, end_i)
    return found
