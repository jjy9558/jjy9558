"""플랫폼별 규칙과 린트 규칙의 기본값. 전부 '근사치'이며 JSON 파일로 덮어쓸 수 있다.

``sell-studio config dump markets > my_markets.json`` 으로 꺼내 고친 뒤
``--markets my_markets.json`` (또는 환경변수 SELLSTUDIO_MARKETS)으로 쓴다. 린트도 같다(``--rules``, SELLSTUDIO_RULES).
앱의 실제 제한은 수시로 바뀐다. 아래 숫자는 보수적으로 잡은 값이니 올리기 전에 앱에서 직접 확인한다.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

APPROX_NOTE = "근사치입니다. 플랫폼 정책은 수시로 바뀌므로 등록 화면의 실제 제한을 확인하세요."

DEFAULT_MARKETS: dict = {
    "_note": APPROX_NOTE,
    "daangn": {
        "label": "당근",
        "title_max": 40,
        "desc_max": 1000,
        "fee_rate": 0.0,
        "fee_note": "직거래 기준 수수료 없음으로 가정(근사치). 당근페이 등 결제 방식에 따라 다릅니다",
        "title_parts": ["name", "spec", "grade"],
        "desc_sections": ["summary", "condition", "defects", "included", "trade", "notes"],
        "fields": {
            "카테고리": "[앱에서 선택]",
            "가격 제안 받기": "[받기/안 받기 선택]",
            "거래 희망 장소": "[직접 입력]",
        },
        "approximate": True,
    },
    "bunjang": {
        "label": "번개장터",
        "title_max": 40,
        "desc_max": 2000,
        "fee_rate": 0.035,
        "fee_note": "안전결제 수수료를 3.5%로 가정(근사치). 구매자/판매자 부담은 직접 확인",
        "title_parts": ["name", "spec", "grade", "used"],
        "desc_sections": ["summary", "condition", "defects", "included", "trade", "notes"],
        "fields": {
            "카테고리": "[앱에서 선택]",
            "상태": "중고상품",
            "배송비": "[포함/별도 선택]",
            "직거래": "[가능/불가 선택]",
        },
        "approximate": True,
    },
    "smartstore": {
        "label": "스마트스토어",
        "title_max": 50,
        "desc_max": None,
        "fee_rate": 0.055,
        "fee_note": "결제+매출연동 수수료 합계를 5.5%로 가정(근사치). 등급·결제수단에 따라 다릅니다",
        "title_parts": ["name", "spec", "used", "grade"],
        "desc_sections": ["summary", "condition", "defects", "included", "trade", "notes"],
        "fields": {
            "카테고리": "[스토어에서 선택]",
            "상품상태": "중고상품",
            "배송방법": "[택배/직접 선택]",
            "상세설명": "detail.html 사용 (sell-studio page)",
        },
        "approximate": True,
    },
}

# severity: error(고치고 올리세요) / warn(확인하세요)
DEFAULT_RULES: dict = {
    "_note": "금칙어·과장 표현은 일반적인 예시입니다. 플랫폼 정책 전체가 아닙니다.",
    "banned": {
        "최저가": "가격 비교 단정 표현",
        "최저가격": "가격 비교 단정 표현",
        "가장 싼": "가격 비교 단정 표현",
        "전국 최저": "가격 비교 단정 표현",
        "초특가": "가격 과장",
        "선입금": "선입금 요구는 사기 의심 신호로 보입니다",
        "계좌로 입금": "플랫폼 밖 결제 유도",
        "카톡 주세요": "플랫폼 밖 연락 유도",
        "카카오톡 아이디": "플랫폼 밖 연락 유도",
        "오픈채팅": "플랫폼 밖 연락 유도",
        "텔레그램": "플랫폼 밖 연락 유도",
    },
    "exaggeration": {
        "무조건": "단정 표현",
        "100%": "단정 표현",
        "완벽": "단정 표현",
        "절대": "단정 표현",
        "역대급": "과장 표현",
        "대박": "과장 표현",
        "미친 가격": "과장 표현",
        "새것같": "상태 과장 가능성(하자가 있다면 정확히 적기)",
        "새제품급": "상태 과장 가능성",
        "정품 보장": "보증할 수 없는 단정 표현",
        "고장 없": "보증할 수 없는 단정 표현",
        "하자 없": "보증할 수 없는 단정 표현(확인한 범위로 적기)",
    },
    "max_exclaim": 2,
    "pii_rules": ["phone", "account", "email", "rrn", "card", "ipv4", "ipv6", "id_number", "path",
                  "api_key", "secret_kv", "bearer", "jwt"],
}


def _read_json(path: str | os.PathLike) -> dict:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError("설정 파일을 읽지 못했습니다 (%s): %s" % (p, e)) from e
    if not isinstance(data, dict):
        raise ValueError("설정 파일의 최상위는 JSON 객체여야 합니다: %s" % p)
    return data


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k in ("banned", "exaggeration", "fields"):
            out[k].update(v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_markets(path: str | None = None) -> dict:
    """기본 시장 설정에 사용자 JSON을 덮어 합친다. 새 시장 이름을 추가해도 된다."""
    path = path or os.environ.get("SELLSTUDIO_MARKETS")
    markets = copy.deepcopy(DEFAULT_MARKETS)
    if path:
        for name, conf in _read_json(path).items():
            if name.startswith("_"):
                continue
            if not isinstance(conf, dict):
                raise ValueError("시장 설정 '%s' 은 객체여야 합니다" % name)
            markets[name] = _merge(markets.get(name, {"label": name, "title_max": 40, "desc_max": None,
                                                     "fee_rate": 0.0, "title_parts": ["name", "spec", "grade"],
                                                     "desc_sections": DEFAULT_MARKETS["daangn"]["desc_sections"],
                                                     "fields": {}}), conf)
    for name, conf in markets.items():
        if name.startswith("_"):
            continue
        tm = conf.get("title_max")
        if not isinstance(tm, int) or tm < 5:
            raise ValueError("시장 '%s' 의 title_max 는 5 이상의 정수여야 합니다" % name)
    return markets


def market_names(markets: dict) -> list[str]:
    return [k for k in markets if not k.startswith("_")]


def load_rules(path: str | None = None) -> dict:
    path = path or os.environ.get("SELLSTUDIO_RULES")
    rules = copy.deepcopy(DEFAULT_RULES)
    if path:
        rules = _merge(rules, _read_json(path))
    return rules
