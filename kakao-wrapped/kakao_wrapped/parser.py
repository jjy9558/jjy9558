"""KakaoTalk export parser (auto-detects PC / Android / iOS / English / CSV)."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Tuple

# message kinds
TEXT = "text"
PHOTO = "photo"
VIDEO = "video"
EMOTICON = "emoticon"
FILE = "file"
VOICE = "voice"
DELETED = "deleted"
SYSTEM = "system"

MEDIA_KINDS = (PHOTO, VIDEO, EMOTICON, FILE, VOICE)

# detected format labels
FMT_PC = "pc_txt"
FMT_ANDROID = "android_txt"
FMT_IOS = "ios_txt"
FMT_ENGLISH = "english_txt"
FMT_CSV = "csv"


class FormatError(ValueError):
    """Raised when the export format cannot be recognised."""


@dataclass
class Message:
    ts: datetime
    user: str  # "" for system messages
    text: str
    kind: str = TEXT
    count: int = 1  # number of items for media ("사진 3장" -> 3)


@dataclass
class ParseResult:
    messages: List[Message]
    fmt: str
    title: Optional[str] = None
    skipped_lines: int = 0
    system_count: int = 0


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0xFEFF], None
)


def decode_bytes(data: bytes) -> str:
    """Decode an export (UTF-8 / UTF-8 BOM / UTF-16 / CP949) and normalise newlines."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("cp949")
            except UnicodeDecodeError:
                text = data.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.translate(_INVISIBLE)


def read_export(path: str) -> str:
    with open(path, "rb") as fh:
        return decode_bytes(fh.read())


# --------------------------------------------------------------------------
# regular expressions
# --------------------------------------------------------------------------

_AP = r"오전|오후|AM|PM|am|pm|A\.M\.|P\.M\."
_TIME = (
    r"(?:(?P<ap1>" + _AP + r")\s*)?"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?"
    r"(?:\s*(?P<ap2>" + _AP + r"))?"
)
_NUMDATE = (
    r"(?P<y>\d{4})\s*[년.\-/]\s*(?P<mo>\d{1,2})\s*[월.\-/]\s*(?P<d>\d{1,2})\s*일?\.?"
)
_MONTHS = (
    "january february march april may june july august september october "
    "november december"
).split()
_MON_RE = (
    r"(?P<mon>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_ENGDATE = _MON_RE + r"\.?\s+(?P<ed>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<ey>\d{4})"
_WEEKDAY_EN = r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?,?\s+)?"
_WEEKDAY_KO = r"(?:\s*[월화수목금토일]요일)?"

# "2026년 1월 3일 오후 3:05, 철수 : 메시지" / "2026. 1. 3. 오후 3:05, ..." / ISO-ish
MOBILE_NUM_RE = re.compile(
    r"^" + _NUMDATE + r"\s*,?\s*" + _TIME + r"\s*(?P<sep>[,:])\s*(?P<rest>.*)$"
)
# "Jan 3, 2026, 3:05 PM, Cheolsu : hi" / "January 3, 2026 at 3:05 PM, ..."
MOBILE_ENG_RE = re.compile(
    r"^" + _WEEKDAY_EN + _ENGDATE + r"(?:\s*,|\s+at)?\s+" + _TIME
    + r"\s*(?P<sep>[,:])\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
# "[철수] [오후 3:05] 메시지"
PC_MSG_RE = re.compile(r"^\[(?P<user>[^\]]+)\]\s+\[" + _TIME + r"\]\s?(?P<text>.*)$")

# date separators (PC: "--------------- 2026년 1월 3일 토요일 ---------------")
DATE_NUM_LINE_RE = re.compile(r"^[-\s]*" + _NUMDATE + _WEEKDAY_KO + r"[-\s]*$")
DATE_ENG_LINE_RE = re.compile(
    r"^[-\s]*" + _WEEKDAY_EN + _ENGDATE + r"[-\s]*$", re.IGNORECASE
)

CSV_HEADER_RE = re.compile(
    r"^\s*\"?(date|datetime|날짜|일시|시간)\"?\s*,\s*\"?(user|name|사용자|이름|보낸사람)\"?\s*,"
    r"\s*\"?(message|text|메시지|내용)\"?\s*$",
    re.IGNORECASE,
)

TITLE_RES = [
    re.compile(r"^(?P<t>.+?)\s*님과\s*카카오톡\s*대화$"),
    re.compile(r"^(?P<t>.+?)\s*카카오톡\s*대화$"),
    re.compile(r"^KakaoTalk\s+Chats?\s+with\s+(?P<t>.+)$", re.IGNORECASE),
]

_SYSTEM_PHRASES = (
    "들어왔습니다",
    "나갔습니다",
    "초대했습니다",
    "내보냈습니다",
    "방장이 되었습니다",
    "강제퇴장",
    "님이 입장",
    "님이 퇴장",
    "채팅방 관리자가 메시지를 가렸습니다",
    "joined the chat",
    "left the chat",
    "was invited",
    "invited ",
    "removed ",
    "님이 입장하셨습니다",
)


def looks_like_system(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 200:
        return False
    return any(p in t for p in _SYSTEM_PHRASES)


# --------------------------------------------------------------------------
# message classification
# --------------------------------------------------------------------------

_WRAP_RE = re.compile(r"^[\(\[<【]\s*(?P<c>.*?)\s*[\)\]>】]\.?$")
_PHOTO_RE = re.compile(
    r"^(?:사진|photos?|pictures?)(?:\s*(?P<n>\d+)\s*(?:장|개)?)?$|^(?P<n2>\d+)\s*photos?$",
    re.IGNORECASE,
)
_VIDEO_RE = re.compile(r"^(?:동영상|video)(?:\s*(?P<n>\d+)\s*(?:개)?)?$", re.IGNORECASE)
_EMOTICON_RE = re.compile(
    r"^(?:이모티콘|emoticon|sticker)(?:\s*(?P<n>\d+)\s*(?:개)?)?$", re.IGNORECASE
)
_VOICE_RE = re.compile(r"^(?:음성\s?메시지|voice\s?message)$", re.IGNORECASE)
_FILE_RE = re.compile(r"^(?:파일|file)\s*(?::.*)?$", re.IGNORECASE)
_DELETED_RE = re.compile(
    r"^(?:삭제된 메시지입니다|메시지가 삭제되었습니다|채팅방 관리자가 메시지를 가렸습니다"
    r"|this message was deleted|message deleted)\.?$",
    re.IGNORECASE,
)


def classify_text(text: str) -> Tuple[str, int]:
    """Return (kind, count) for a message body."""
    t = text.strip()
    if "\n" in t or not t:
        return TEXT, 1
    wrapped = _WRAP_RE.match(t)
    core = wrapped.group("c") if wrapped else t
    core = core.strip()
    if _DELETED_RE.match(core):
        return DELETED, 1
    m = _PHOTO_RE.match(core)
    if m:
        n = m.group("n") or m.group("n2")
        return PHOTO, int(n) if n else 1
    m = _VIDEO_RE.match(core)
    if m:
        return VIDEO, int(m.group("n")) if m.group("n") else 1
    m = _EMOTICON_RE.match(core)
    if m:
        return EMOTICON, int(m.group("n")) if m.group("n") else 1
    if _VOICE_RE.match(core):
        return VOICE, 1
    if _FILE_RE.match(core):
        return FILE, 1
    return TEXT, 1


# --------------------------------------------------------------------------
# date/time helpers
# --------------------------------------------------------------------------

def _month_from_name(name: str) -> int:
    n = name.lower()[:3]
    for i, full in enumerate(_MONTHS, 1):
        if full.startswith(n):
            return i
    raise ValueError(name)


def _date_from_match(m: "re.Match[str]") -> Optional[date]:
    gd = m.groupdict()
    try:
        if gd.get("y"):
            return date(int(gd["y"]), int(gd["mo"]), int(gd["d"]))
        return date(int(gd["ey"]), _month_from_name(gd["mon"]), int(gd["ed"]))
    except (ValueError, TypeError):
        return None


def _time_from_match(m: "re.Match[str]") -> Optional[Tuple[int, int, int]]:
    gd = m.groupdict()
    h, mi = int(gd["h"]), int(gd["mi"])
    s = int(gd["s"]) if gd.get("s") else 0
    ap = (gd.get("ap1") or gd.get("ap2") or "").lower().replace(".", "")
    if ap in ("오후", "pm"):
        if h < 12:
            h += 12
    elif ap in ("오전", "am"):
        if h == 12:
            h = 0
    if h > 23 or mi > 59 or s > 59:
        return None
    return h, mi, s


def _combine(d: Optional[date], t: Optional[Tuple[int, int, int]]) -> Optional[datetime]:
    if d is None or t is None:
        return None
    return datetime(d.year, d.month, d.day, t[0], t[1], t[2])


_CSV_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
)


def _parse_csv_date(s: str) -> Optional[datetime]:
    s = s.strip()
    for f in _CSV_DATE_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    for rx in (MOBILE_NUM_RE, MOBILE_ENG_RE):
        m = rx.match(s + ", x")
        if m:
            dt = _combine(_date_from_match(m), _time_from_match(m))
            if dt:
                return dt
    return None


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def detect_format(text: str, sample_lines: int = 3000) -> str:
    """Return one of FMT_* for the given export text. Raises FormatError."""
    lines = text.split("\n")
    first = next((ln for ln in lines if ln.strip()), "")
    if CSV_HEADER_RE.match(first):
        return FMT_CSV
    pc = android = ios = eng = 0
    for ln in lines[:sample_lines]:
        if not ln:
            continue
        if PC_MSG_RE.match(ln):
            pc += 1
            continue
        m = MOBILE_NUM_RE.match(ln)
        if m:
            if "년" in ln[: m.start("mo")]:
                android += 1
            else:
                ios += 1
            continue
        if MOBILE_ENG_RE.match(ln):
            eng += 1
    mobile = android + ios + eng
    if pc == 0 and mobile == 0:
        raise FormatError(
            "카카오톡 대화 내보내기 형식을 인식하지 못했습니다. "
            "(PC txt / 안드로이드·iOS txt / CSV 지원)"
        )
    if pc >= mobile:
        return FMT_PC
    best = max((android, FMT_ANDROID), (ios, FMT_IOS), (eng, FMT_ENGLISH))
    return best[1]


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def _extract_title(lines: List[str]) -> Optional[str]:
    for ln in lines[:6]:
        s = ln.strip()
        for rx in TITLE_RES:
            m = rx.match(s)
            if m:
                return m.group("t").strip() or None
    return None


def parse_csv(text: str) -> ParseResult:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    idx = (0, 1, 2)
    start = 0
    if rows and CSV_HEADER_RE.match(",".join(rows[0])):
        start = 1
    msgs: List[Message] = []
    skipped = 0
    system = 0
    for row in rows[start:]:
        if not row or not any(c.strip() for c in row):
            continue
        if len(row) < 3:
            skipped += 1
            continue
        ts = _parse_csv_date(row[idx[0]])
        if ts is None:
            skipped += 1
            continue
        user = row[idx[1]].strip()
        body = ",".join(row[idx[2]:]) if len(row) > 3 else row[idx[2]]
        body = body.replace("\r\n", "\n").rstrip()
        if not user:
            msgs.append(Message(ts, "", body, SYSTEM))
            system += 1
            continue
        kind, n = classify_text(body)
        msgs.append(Message(ts, user, body, kind, n))
    return ParseResult(msgs, FMT_CSV, None, skipped, system)


def parse_txt(text: str, fmt: str) -> ParseResult:
    lines = text.split("\n")
    msgs: List[Message] = []
    cur: Optional[Message] = None
    cur_date: Optional[date] = None
    last_ts: Optional[datetime] = None
    skipped = 0
    system = 0

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            cur.text = cur.text.rstrip()
            cur.kind, cur.count = classify_text(cur.text)
            msgs.append(cur)
        cur = None

    def add_system(ts: datetime, body: str) -> None:
        nonlocal system, last_ts
        flush()
        msgs.append(Message(ts, "", body.strip(), SYSTEM))
        system += 1

    for line in lines:
        # 1. date separator
        dm = DATE_NUM_LINE_RE.match(line) or DATE_ENG_LINE_RE.match(line)
        if dm and line.strip():
            d = _date_from_match(dm)
            if d is not None:
                flush()
                cur_date = d
                continue
        # 2. PC message
        m = PC_MSG_RE.match(line)
        if m and cur_date is not None:
            t = _time_from_match(m)
            ts = _combine(cur_date, t)
            if ts is not None:
                flush()
                cur = Message(ts, m.group("user").strip(), m.group("text"))
                last_ts = ts
                continue
        # 3. mobile message / system line with timestamp
        m = MOBILE_NUM_RE.match(line) or MOBILE_ENG_RE.match(line)
        if m:
            ts = _combine(_date_from_match(m), _time_from_match(m))
            if ts is not None:
                rest = m.group("rest")
                um = re.match(r"^(.+?) : ?(.*)$", rest) if m.group("sep") == "," else None
                if um and not looks_like_system(um.group(1)):
                    flush()
                    cur = Message(ts, um.group(1).strip(), um.group(2))
                else:
                    add_system(ts, rest)
                last_ts = ts
                cur_date = ts.date()
                continue
        # 4. plain system line (PC)
        if cur_date is not None and looks_like_system(line) and not line.startswith("["):
            ts = last_ts if last_ts and last_ts.date() == cur_date else datetime(
                cur_date.year, cur_date.month, cur_date.day
            )
            add_system(ts, line)
            continue
        # 5. continuation
        if cur is not None:
            cur.text += "\n" + line
        elif line.strip():
            skipped += 1  # header or unknown line before the first message
    flush()
    return ParseResult(msgs, fmt, _extract_title(lines), skipped, system)


def parse_export(text: str, fmt: Optional[str] = None) -> ParseResult:
    """Parse export text; the format is auto-detected unless ``fmt`` is given."""
    fmt = fmt or detect_format(text)
    if fmt == FMT_CSV:
        return parse_csv(text)
    return parse_txt(text, fmt)


def parse_file(path: str, fmt: Optional[str] = None) -> ParseResult:
    return parse_export(read_export(path), fmt)
