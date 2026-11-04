"""`check --json` 출력의 계약(스키마)과 검증.

다른 도구가 이 출력을 읽으므로, 필드를 바꾸거나 뜻을 바꿀 때는 SCHEMA_VERSION을 올립니다.
출력은 데이터 파일을 읽기만 해서 만들고, 쓰거나 알림을 보내지 않습니다.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict

from . import core
from .core import Ledger

SCHEMA_VERSION = 1

TOP_KEYS = ("version", "generated_for", "cooling_ended", "followups_due", "renewals", "approved_not_bought")

# 각 목록 항목의 필드와 자료형. "date"는 YYYY-MM-DD 문자열입니다.
LIST_FIELDS: Dict[str, Dict[str, Any]] = {
    "cooling_ended": {"id": str, "name": str, "price": int, "ended": "date"},
    "followups_due": {"id": str, "name": str, "bought": "date"},
    "renewals": {"name": str, "amount": int, "date": "date", "days_left": int},
    "approved_not_bought": {"id": str, "name": str, "price": int},
}

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ContractError(ValueError):
    """출력이 스키마와 맞지 않을 때."""


def build(ledger: Ledger, now: dt.datetime) -> dict:
    """장부 상태를 스키마 모양의 dict로 만듭니다. 장부를 바꾸지 않습니다."""
    today = now.date()
    return {
        "version": SCHEMA_VERSION,
        "generated_for": today.isoformat(),
        "cooling_ended": [
            {
                "id": str(wish["id"]),
                "name": wish["name"],
                "price": wish["price"],
                "ended": dt.datetime.fromisoformat(wish["cool_until"]).date().isoformat(),
            }
            for wish in ledger.due_wishes(now)
        ],
        "followups_due": [
            {"id": str(p["id"]), "name": p["name"], "bought": p["date"]}
            for p in ledger.due_followups(today)
        ],
        "renewals": [
            {
                "name": sub["name"],
                "amount": sub["price"],
                "date": renewal.isoformat(),
                "days_left": left,
            }
            for sub, renewal, left in ledger.due_renewals(today)
        ],
        "approved_not_bought": [
            {"id": str(wish["id"]), "name": wish["name"], "price": wish["price"]}
            for wish in ledger.approved_wishes()
        ],
    }


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_date(value, where: str) -> None:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ContractError(f"{where}: YYYY-MM-DD 날짜여야 해요: {value!r}")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise ContractError(f"{where}: 없는 날짜예요: {value!r}")
    if parsed.isoformat() != value:
        raise ContractError(f"{where}: 날짜 형식이 맞지 않아요: {value!r}")


def validate(payload) -> dict:
    """스키마에 맞으면 payload를 그대로 돌려주고, 아니면 ContractError를 던집니다."""
    if not isinstance(payload, dict):
        raise ContractError("최상위 값은 객체여야 해요.")
    if set(payload) != set(TOP_KEYS):
        raise ContractError(f"최상위 키가 맞지 않아요: {sorted(set(payload) ^ set(TOP_KEYS))}")
    if not _is_int(payload["version"]) or payload["version"] != SCHEMA_VERSION:
        raise ContractError(f"version은 {SCHEMA_VERSION}이어야 해요: {payload['version']!r}")
    _check_date(payload["generated_for"], "generated_for")

    for key, fields in LIST_FIELDS.items():
        items = payload[key]
        if not isinstance(items, list):
            raise ContractError(f"{key}은 목록이어야 해요.")
        for index, item in enumerate(items):
            where = f"{key}[{index}]"
            if not isinstance(item, dict):
                raise ContractError(f"{where}은 객체여야 해요.")
            if set(item) != set(fields):
                raise ContractError(f"{where} 키가 맞지 않아요: {sorted(set(item) ^ set(fields))}")
            for field, kind in fields.items():
                value = item[field]
                if kind == "date":
                    _check_date(value, f"{where}.{field}")
                elif kind is int:
                    if not _is_int(value):
                        raise ContractError(f"{where}.{field}은 정수여야 해요: {value!r}")
                elif not isinstance(value, kind):
                    raise ContractError(f"{where}.{field}은 {kind.__name__}이어야 해요: {value!r}")
            if key == "renewals" and not 0 <= item["days_left"] <= core.RENEWAL_WINDOW_DAYS:
                raise ContractError(f"{where}.days_left 범위를 벗어났어요: {item['days_left']}")
    return payload
