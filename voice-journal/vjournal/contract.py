# -*- coding: utf-8 -*-
"""`morning --json` 출력 계약 (다른 도구 연동용). 표준 라이브러리만.

스키마를 바꾸면 SCHEMA_VERSION 을 올린다. 키 집합은 정확히 일치해야 한다(추가/누락 모두 오류).
"""
import re
from datetime import date, datetime

SCHEMA_VERSION = 1

RESULTS = ("done", "partial", "missed", "dropped")

TOP_KEYS = ("version", "date", "first_task", "goals_yesterday", "goals_due_soon", "flags")
YESTERDAY_KEYS = ("id", "text", "result", "progress")
DUE_KEYS = ("id", "text", "deadline", "days_left")
FLAG_KEYS = ("id", "text", "created")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _fail(path, msg):
    raise ValueError("%s: %s" % (path, msg))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _check_keys(obj, keys, path):
    if not isinstance(obj, dict):
        _fail(path, "객체여야 합니다")
    if set(obj) != set(keys):
        _fail(path, "키는 %s 이어야 합니다 (실제 %s)" % (sorted(keys), sorted(obj)))


def _check_date(v, path):
    if not isinstance(v, str) or not _DATE_RE.match(v):
        _fail(path, "YYYY-MM-DD 문자열이어야 합니다 (%r)" % (v,))
    try:
        date.fromisoformat(v)
    except ValueError:
        _fail(path, "존재하지 않는 날짜 (%r)" % (v,))


def _check_text(v, path):
    if not isinstance(v, str):
        _fail(path, "문자열이어야 합니다")


def _check_list(v, path):
    if not isinstance(v, list):
        _fail(path, "배열이어야 합니다")
    return v


def validate(payload):
    """payload 가 스키마 v1 을 만족하면 그대로 돌려주고, 아니면 ValueError."""
    _check_keys(payload, TOP_KEYS, "root")
    if not (_is_int(payload["version"]) and payload["version"] == SCHEMA_VERSION):
        _fail("version", "%d 이어야 합니다" % SCHEMA_VERSION)
    _check_date(payload["date"], "date")

    ft = payload["first_task"]
    if ft is not None:
        _check_text(ft, "first_task")

    for i, g in enumerate(_check_list(payload["goals_yesterday"], "goals_yesterday")):
        p = "goals_yesterday[%d]" % i
        _check_keys(g, YESTERDAY_KEYS, p)
        if not _is_int(g["id"]):
            _fail(p + ".id", "정수여야 합니다")
        _check_text(g["text"], p + ".text")
        if g["result"] not in RESULTS:
            _fail(p + ".result", "%s 중 하나여야 합니다" % "|".join(RESULTS))
        _check_text(g["progress"], p + ".progress")

    for i, g in enumerate(_check_list(payload["goals_due_soon"], "goals_due_soon")):
        p = "goals_due_soon[%d]" % i
        _check_keys(g, DUE_KEYS, p)
        if not _is_int(g["id"]):
            _fail(p + ".id", "정수여야 합니다")
        _check_text(g["text"], p + ".text")
        _check_date(g["deadline"], p + ".deadline")
        if not _is_int(g["days_left"]):
            _fail(p + ".days_left", "정수여야 합니다")

    for i, f in enumerate(_check_list(payload["flags"], "flags")):
        p = "flags[%d]" % i
        _check_keys(f, FLAG_KEYS, p)
        if not _is_int(f["id"]):
            _fail(p + ".id", "정수여야 합니다")
        _check_text(f["text"], p + ".text")
        if not isinstance(f["created"], str):
            _fail(p + ".created", "ISO 8601 문자열이어야 합니다")
        try:
            datetime.fromisoformat(f["created"])
        except ValueError:
            _fail(p + ".created", "ISO 8601 이 아닙니다 (%r)" % f["created"])
    return payload
