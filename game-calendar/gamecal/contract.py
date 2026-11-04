"""`alerts --json` 출력 스키마 (다른 도구 연동용, 버전 1).

출력 형태:
  {"version": 1, "generated_for": "YYYY-MM-DD",
   "items": [{"kind", "game", "appid", "title", "start", "end", "all_day", "d_day", "ends_in_days",
             "ongoing", "url"}, ...]}

- start/end: +09:00 오프셋 ISO8601 문자열 또는 null.
  all_day 항목은 해당 날짜의 00:00:00+09:00이며, end는 배타적(다음 날짜 00:00:00+09:00)입니다.
- d_day: generated_for 기준 start 날짜까지의 일수 (진행 중 항목은 음수, start가 없으면 null).
- ends_in_days: end 날짜에서 generated_for를 뺀 일수 (end가 없으면 null).
- ongoing: start 날짜가 generated_for보다 앞이면 true (이 경우 end는 generated_for 00:00 KST보다 뒤여야 함).
- items: start 오름차순, start가 null인 항목은 맨 뒤.
"""
import datetime as dt

SCHEMA_VERSION = 1
KINDS = ("maintenance", "event", "shutdown", "patch")
TOP_KEYS = frozenset(("version", "generated_for", "items"))
ITEM_KEYS = frozenset(("kind", "game", "appid", "title", "start", "end", "all_day", "d_day", "ends_in_days",
                       "ongoing", "url"))
KST_OFFSET = dt.timedelta(hours=9)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_kst(v, where):
    """+09:00 ISO8601 문자열 -> aware datetime, null이면 None. 아니면 ValueError."""
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("%s: 문자열 또는 null이어야 합니다" % where)
    try:
        d = dt.datetime.fromisoformat(v)
    except ValueError:
        raise ValueError("%s: ISO8601 형식이 아닙니다: %r" % (where, v))
    if d.tzinfo is None or d.utcoffset() != KST_OFFSET:
        raise ValueError("%s: +09:00 오프셋이 필요합니다: %r" % (where, v))
    return d


def validate(obj):
    """스키마를 위반하면 ValueError를 발생시킵니다. 통과하면 None을 반환합니다."""
    if not isinstance(obj, dict):
        raise ValueError("최상위는 객체여야 합니다")
    if set(obj) != TOP_KEYS:
        raise ValueError("최상위 키가 맞지 않습니다: %s" % sorted(set(obj) ^ TOP_KEYS))
    if not _is_int(obj["version"]) or obj["version"] != SCHEMA_VERSION:
        raise ValueError("version은 %d여야 합니다" % SCHEMA_VERSION)
    gf = obj["generated_for"]
    if not isinstance(gf, str):
        raise ValueError("generated_for는 문자열이어야 합니다")
    try:
        today = dt.date.fromisoformat(gf)
    except ValueError:
        raise ValueError("generated_for는 YYYY-MM-DD 형식이어야 합니다: %r" % gf)
    if not isinstance(obj["items"], list):
        raise ValueError("items는 배열이어야 합니다")

    prev_key = None
    for i, it in enumerate(obj["items"]):
        where = "items[%d]" % i
        if not isinstance(it, dict):
            raise ValueError("%s: 객체여야 합니다" % where)
        if set(it) != ITEM_KEYS:
            raise ValueError("%s: 키가 맞지 않습니다: %s" % (where, sorted(set(it) ^ ITEM_KEYS)))
        if it["kind"] not in KINDS:
            raise ValueError("%s: kind는 %s 중 하나여야 합니다" % (where, "/".join(KINDS)))
        for k in ("game", "title"):
            if not isinstance(it[k], str):
                raise ValueError("%s.%s는 문자열이어야 합니다" % (where, k))
        if not _is_int(it["appid"]):
            raise ValueError("%s.appid는 정수여야 합니다" % where)
        if not isinstance(it["all_day"], bool):
            raise ValueError("%s.all_day는 불리언이어야 합니다" % where)
        if not isinstance(it["ongoing"], bool):
            raise ValueError("%s.ongoing은 불리언이어야 합니다" % where)
        if it["url"] is not None and not isinstance(it["url"], str):
            raise ValueError("%s.url은 문자열 또는 null이어야 합니다" % where)

        start = _parse_kst(it["start"], where + ".start")
        end = _parse_kst(it["end"], where + ".end")
        if start is None:
            if end is not None or it["d_day"] is not None or it["ends_in_days"] is not None or it["ongoing"]:
                raise ValueError("%s: start가 null이면 end, d_day, ends_in_days가 null이고 ongoing은 false여야 합니다"
                                 % where)
            key = (1, None)
        else:
            if it["all_day"] and (start.hour, start.minute, start.second) != (0, 0, 0):
                raise ValueError("%s: all_day 항목의 start는 00:00:00+09:00이어야 합니다" % where)
            if end is not None and end < start:
                raise ValueError("%s: end가 start보다 빠릅니다" % where)
            expected = (start.date() - today).days
            if not _is_int(it["d_day"]) or it["d_day"] != expected:
                raise ValueError("%s.d_day는 %d여야 합니다" % (where, expected))
            ends = None if end is None else (end.date() - today).days
            got = it["ends_in_days"]
            if (ends is None and got is not None) or (ends is not None and (not _is_int(got) or got != ends)):
                raise ValueError("%s.ends_in_days는 %s여야 합니다" % (where, ends))
            ongoing = start.date() < today
            if it["ongoing"] is not ongoing:
                raise ValueError("%s.ongoing은 %s여야 합니다" % (where, ongoing))
            midnight = dt.datetime(today.year, today.month, today.day, tzinfo=dt.timezone(KST_OFFSET))
            if ongoing and (end is None or end <= midnight):
                raise ValueError("%s: 진행 중 항목은 generated_for 00:00 KST 이후에 끝나야 합니다" % where)
            key = (0, start)
        if prev_key is not None and key < prev_key:
            raise ValueError("%s: start 오름차순 정렬이 아닙니다 (null은 마지막)" % where)
        prev_key = key
