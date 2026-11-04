"""기계가 읽는 JSON 출력(due --json, refunds --json)의 계약(스키마).

다른 도구가 이 출력을 파싱하므로 필드 이름·타입을 바꾸면 SCHEMA_VERSION 을 올려야 합니다.
validate() 는 표준 라이브러리만 쓰는 최소한의 검사기입니다.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

SCHEMA_VERSION = 1

KIND_BILL = "bill"        # 납부기한이 있는 문서 (고지서·안내문 등, due 목록의 문서 항목)
KIND_REFUND = "refund"    # 환불·반품 기한
KINDS = (KIND_BILL, KIND_REFUND)

TOP_KEYS = frozenset({"version", "generated_for", "items"})
ITEM_KEYS = frozenset({"kind", "title", "merchant", "amount", "due", "days_left", "auto_debit", "doc_id"})


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _date(v: Any, where: str) -> dt.date:
    if not isinstance(v, str) or len(v) != 10:
        raise ValueError("%s: YYYY-MM-DD 문자열이어야 합니다: %r" % (where, v))
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise ValueError("%s: 올바른 날짜가 아닙니다: %r" % (where, v))


def validate(obj: Any) -> Dict[str, Any]:
    """출력 객체가 스키마 v1 을 만족하는지 검사한다. 통과하면 obj 를 그대로 돌려준다.

    어긋나면 ValueError 를 던진다.
    """
    if not isinstance(obj, dict):
        raise ValueError("최상위는 객체(dict)여야 합니다")
    if set(obj) != TOP_KEYS:
        raise ValueError("최상위 키는 %s 여야 합니다: %s" % (sorted(TOP_KEYS), sorted(obj)))
    if obj["version"] != SCHEMA_VERSION or not _is_int(obj["version"]):
        raise ValueError("version 은 %d 이어야 합니다: %r" % (SCHEMA_VERSION, obj["version"]))
    today = _date(obj["generated_for"], "generated_for")
    items = obj["items"]
    if not isinstance(items, list):
        raise ValueError("items 는 배열이어야 합니다")

    last_due = None  # type: Any
    for i, it in enumerate(items):
        where = "items[%d]" % i
        if not isinstance(it, dict):
            raise ValueError("%s: 객체여야 합니다" % where)
        if set(it) != ITEM_KEYS:
            raise ValueError("%s: 키는 %s 여야 합니다: %s" % (where, sorted(ITEM_KEYS), sorted(it)))
        if it["kind"] not in KINDS:
            raise ValueError("%s.kind 는 %s 중 하나여야 합니다: %r" % (where, KINDS, it["kind"]))
        for k in ("title", "merchant"):
            if not isinstance(it[k], str):
                raise ValueError("%s.%s 는 문자열이어야 합니다" % (where, k))
        if it["amount"] is not None and not _is_int(it["amount"]):
            raise ValueError("%s.amount 는 정수 또는 null 이어야 합니다" % where)
        due = _date(it["due"], where + ".due")
        if not _is_int(it["days_left"]) or it["days_left"] != (due - today).days:
            raise ValueError("%s.days_left 는 due - generated_for 와 같아야 합니다" % where)
        if not isinstance(it["auto_debit"], bool):
            raise ValueError("%s.auto_debit 는 true/false 여야 합니다" % where)
        if not _is_int(it["doc_id"]):
            raise ValueError("%s.doc_id 는 정수여야 합니다" % where)
        if last_due is not None and due < last_due:
            raise ValueError("%s: due 기준으로 정렬되어 있어야 합니다" % where)
        last_due = due
    return obj


# -- budget alarm (--json) 페이로드: 자체 version 을 가진다. SCHEMA_VERSION 은 그대로. -----------
BUDGET_VERSION = 1
BUDGET_TOP_KEYS = frozenset({"version", "kind", "generated_for", "month", "days_left", "daily_allowance",
                             "envelopes", "alerts"})
BUDGET_ENVELOPE_KEYS = ("key", "name", "budget", "spent", "remaining", "percent", "crossed")
BUDGET_ALERT_KEYS = frozenset({"envelope", "threshold", "percent", "message", "ntfy"})
NTFY_STATES = ("off", "sent", "failed")


def validate_budget(obj: Any) -> Dict[str, Any]:
    """budget alarm --json 출력이 budget 스키마 v1 을 만족하는지 검사한다. 어긋나면 ValueError."""
    if not isinstance(obj, dict) or set(obj) != BUDGET_TOP_KEYS:
        raise ValueError("budget 최상위 키는 %s 여야 합니다" % sorted(BUDGET_TOP_KEYS))
    if not _is_int(obj["version"]) or obj["version"] != BUDGET_VERSION:
        raise ValueError("budget version 은 %d 이어야 합니다" % BUDGET_VERSION)
    if obj["kind"] != "budget":
        raise ValueError("kind 는 'budget' 이어야 합니다")
    _date(obj["generated_for"], "generated_for")
    m = obj["month"]
    if not isinstance(m, str) or len(m) != 7 or m[4] != "-" or not (m[:4] + m[5:]).isdigit():
        raise ValueError("month 는 YYYY-MM 이어야 합니다: %r" % (m,))
    for k in ("days_left", "daily_allowance"):
        if not _is_int(obj[k]) or obj[k] < 0:
            raise ValueError("%s 는 0 이상의 정수여야 합니다" % k)
    if not isinstance(obj["envelopes"], list) or not isinstance(obj["alerts"], list):
        raise ValueError("envelopes/alerts 는 배열이어야 합니다")
    for i, e in enumerate(obj["envelopes"]):
        w = "envelopes[%d]" % i
        if not isinstance(e, dict) or set(e) != set(BUDGET_ENVELOPE_KEYS):
            raise ValueError("%s: 키는 %s 여야 합니다" % (w, sorted(BUDGET_ENVELOPE_KEYS)))
        for k in ("key", "name"):
            if not isinstance(e[k], str):
                raise ValueError("%s.%s 는 문자열이어야 합니다" % (w, k))
        for k in ("budget", "spent", "remaining", "percent"):
            if not _is_int(e[k]):
                raise ValueError("%s.%s 는 정수여야 합니다" % (w, k))
        if e["remaining"] != e["budget"] - e["spent"]:
            raise ValueError("%s.remaining 은 budget - spent 와 같아야 합니다" % w)
        if not isinstance(e["crossed"], list) or not all(_is_int(x) for x in e["crossed"]):
            raise ValueError("%s.crossed 는 정수 배열이어야 합니다" % w)
    for i, a in enumerate(obj["alerts"]):
        w = "alerts[%d]" % i
        if not isinstance(a, dict) or set(a) != BUDGET_ALERT_KEYS:
            raise ValueError("%s: 키는 %s 여야 합니다" % (w, sorted(BUDGET_ALERT_KEYS)))
        if not isinstance(a["envelope"], str) or not isinstance(a["message"], str):
            raise ValueError("%s: envelope/message 는 문자열이어야 합니다" % w)
        if not _is_int(a["threshold"]) or not _is_int(a["percent"]):
            raise ValueError("%s: threshold/percent 는 정수여야 합니다" % w)
        if a["ntfy"] not in NTFY_STATES:
            raise ValueError("%s.ntfy 는 %s 중 하나여야 합니다" % (w, NTFY_STATES))
    return obj


def sort_key(item: Dict[str, Any]) -> tuple:
    """due 오름차순, 같으면 kind(bill 먼저), doc_id 순."""
    return (item["due"], KINDS.index(item["kind"]), item["doc_id"])


__all__: List[str] = ["SCHEMA_VERSION", "KIND_BILL", "KIND_REFUND", "KINDS", "validate", "sort_key", "BUDGET_VERSION", "validate_budget"]
