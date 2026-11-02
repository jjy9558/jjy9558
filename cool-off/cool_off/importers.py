"""다른 도구(steam-buddy, paper-butler)의 기록을 읽어 와서 구매 기록으로 합칩니다.

원본 파일은 읽기만 하고 고치지 않습니다.
중복 방지 키는 (날짜, 금액, 정규화한 가맹점/이름)이라서 같은 파일을 다시 가져와도 결과가 같습니다.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import unicodedata
from pathlib import Path
from typing import List, Optional

from .core import LedgerError, Ledger, occurrence

SOURCE_MANUAL = "manual"
SOURCE_STEAM = "steam"
SOURCE_LEDGER = "ledger"
SOURCE_LABELS = {SOURCE_MANUAL: "직접 기록", SOURCE_STEAM: "스팀", SOURCE_LEDGER: "가계부"}
SOURCE_ORDER = (SOURCE_MANUAL, SOURCE_STEAM, SOURCE_LEDGER)

MIN_REPEAT_MONTHS = 3  # 같은 가맹점+금액이 이만큼 연속된 달에 나오면 구독 후보
STEAM_WORDS = ("steam", "스팀")

# paper-butler export-csv --what ledger 의 열 이름
COL_WHEN, COL_MERCHANT, COL_KIND, COL_AMOUNT, COL_COUNTED = "일시", "가맹점", "구분", "금액", "집계반영"


def normalize_merchant(name) -> str:
    """대소문자, 공백, 기호를 지워서 같은 가게를 같은 글자열로 만듭니다."""
    text = unicodedata.normalize("NFKC", str(name)).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def dedup_key(date: str, amount: int, name) -> str:
    return f"{date}|{int(amount)}|{normalize_merchant(name)}"


def source_of(purchase: dict) -> str:
    return purchase.get("source") or SOURCE_MANUAL


def _existing_keys(ledger: Ledger) -> set:
    return {dedup_key(p["date"], p["price"], p["name"]) for p in ledger.state["purchases"]}


def _add_imported(ledger: Ledger, source: str, name: str, price: int, date: str,
                  satisfaction: Optional[int] = None) -> dict:
    purchase = {
        "id": ledger._new_id(),
        "name": name,
        "price": price,
        "date": date,
        "wish_id": None,
        "during_cooling": False,
        "satisfaction": satisfaction,
        "usage": None,
        "answered_at": None,
        "source": source,
    }
    ledger.state["purchases"].append(purchase)
    return purchase


# ---------------------------------------------------------------- steam-buddy

def default_steam_path(env=None) -> Path:
    env = os.environ if env is None else env
    raw = env.get("STEAM_BUDDY_HOME")
    return (Path(raw).expanduser() if raw else Path.home() / ".steam-buddy") / "data.json"


def import_steam(ledger: Ledger, path) -> dict:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise LedgerError(f"steam-buddy 파일을 읽을 수 없어요 ({path}): {exc}")
    except ValueError as exc:
        raise LedgerError(f"steam-buddy 파일 형식이 올바르지 않아요 ({path}): {exc}")
    games = data.get("games") if isinstance(data, dict) else None
    if not isinstance(games, dict):
        raise LedgerError(f"steam-buddy 데이터가 아니에요 (games 없음): {path}")

    result = {"added": 0, "duplicate": 0, "updated": 0, "skipped": 0}
    keys = {dedup_key(p["date"], p["price"], p["name"]): p for p in ledger.state["purchases"]}
    for appid in sorted(games, key=str):
        game = games[appid]
        try:
            name = " ".join(str(game["name"]).split())
            price = int(game.get("price_paid") or 0)
            date = dt.datetime.fromisoformat(str(game["purchased_at"])).date().isoformat()
            rating = game.get("rating")
            rating = int(rating) if rating else None
        except (KeyError, TypeError, ValueError):
            result["skipped"] += 1
            continue
        if not name or price <= 0:  # 무료 게임은 1만원당 점수를 낼 수 없어서 뺍니다.
            result["skipped"] += 1
            continue
        if rating is not None and not 1 <= rating <= 5:
            rating = None  # steam-buddy 별점도 1-5라서 같은 척도, 범위 밖이면 버립니다.
        key = dedup_key(date, price, name)
        existing = keys.get(key)
        if existing is not None:
            result["duplicate"] += 1
            if source_of(existing) == SOURCE_STEAM and existing["satisfaction"] is None and rating:
                existing["satisfaction"] = rating
                result["updated"] += 1
            continue
        keys[key] = _add_imported(ledger, SOURCE_STEAM, name, price, date, rating)
        result["added"] += 1
    return result


# ---------------------------------------------------------------- paper-butler 가계부 CSV

def _clean_cell(text: str) -> str:
    text = (text or "").strip()
    # paper-butler csv_safe 는 = + - @ 로 시작하는 값 앞에 ' 를 붙입니다.
    if len(text) > 1 and text[0] == "'" and text[1] in "=+-@\t\r":
        text = text[1:]
    return text


def _is_steam(merchant: str) -> bool:
    norm = normalize_merchant(merchant)
    return any(word in norm for word in STEAM_WORDS)


def import_ledger_csv(ledger: Ledger, path) -> dict:
    path = Path(path)
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            rows = list(reader)
    except OSError as exc:
        raise LedgerError(f"CSV 파일을 읽을 수 없어요 ({path}): {exc}")
    except (UnicodeDecodeError, csv.Error) as exc:
        raise LedgerError(f"CSV 형식이 올바르지 않아요 ({path}): {exc}")
    if not fields:
        return {"added": 0, "duplicate": 0, "double": 0, "skipped": 0, "rows": 0}
    missing = [c for c in (COL_WHEN, COL_MERCHANT, COL_AMOUNT) if c not in fields]
    if missing:
        raise LedgerError("paper-butler `export-csv --what ledger` 파일이 아니에요. 없는 열: " + ", ".join(missing))

    result = {"added": 0, "duplicate": 0, "double": 0, "skipped": 0, "rows": len(rows)}
    keys = _existing_keys(ledger)
    # 이미 다른 경로로 기록된 구매(직접/스팀)의 (날짜, 금액): 같은 지출이 카드 내역에 또 나온 것
    other = {(p["date"], p["price"]) for p in ledger.state["purchases"] if source_of(p) != SOURCE_LEDGER}
    steam_days = {(p["date"], p["price"]) for p in ledger.state["purchases"] if source_of(p) == SOURCE_STEAM}
    for row in rows:
        try:
            if COL_KIND in row and _clean_cell(row[COL_KIND]) not in ("", "승인"):
                result["skipped"] += 1  # 취소 행
                continue
            if COL_COUNTED in row and _clean_cell(row[COL_COUNTED]) not in ("", "Y"):
                result["skipped"] += 1  # 취소돼서 집계에서 빠진 행
                continue
            merchant = " ".join(_clean_cell(row[COL_MERCHANT]).split())
            amount = int(_clean_cell(row[COL_AMOUNT]).replace(",", ""))
            date = dt.date.fromisoformat(_clean_cell(row[COL_WHEN])[:10]).isoformat()
        except (KeyError, TypeError, ValueError, AttributeError):
            result["skipped"] += 1
            continue
        if not merchant or amount <= 0:
            result["skipped"] += 1
            continue
        key = dedup_key(date, amount, merchant)
        if key in keys:
            result["duplicate"] += 1
            continue
        # 이중 집계: 스팀 결제는 steam 기록과, 그 밖에는 직접 기록한 구매와 날짜+금액이 같으면 같은 지출로 봅니다.
        if (date, amount) in steam_days and _is_steam(merchant) or \
                (date, amount) in other and (date, amount) not in steam_days:
            result["double"] += 1
            continue
        keys.add(key)
        _add_imported(ledger, SOURCE_LEDGER, merchant, amount, date)
        result["added"] += 1
    return result


def subscription_candidates(ledger: Ledger) -> List[dict]:
    """가계부에서 같은 가맹점+금액이 연속 3개월 이상 반복되면 구독 후보로 돌려줍니다."""
    groups = {}
    for p in ledger.state["purchases"]:
        if source_of(p) != SOURCE_LEDGER:
            continue
        g = groups.setdefault((normalize_merchant(p["name"]), p["price"]), {"name": p["name"], "rows": []})
        g["rows"].append(p["date"])
    known = [normalize_merchant(s["name"]) for s in ledger.state["subscriptions"]]
    found = []
    for (norm, price), g in groups.items():
        months = sorted({d[:7] for d in g["rows"]})
        run = best = 1
        for prev, cur in zip(months, months[1:]):
            py, pm = int(prev[:4]), int(prev[5:])
            run = run + 1 if (py * 12 + pm + 1) == (int(cur[:4]) * 12 + int(cur[5:])) else 1
            best = max(best, run)
        if best < MIN_REPEAT_MONTHS or not norm:
            continue
        if any(k and (k in norm or norm in k) for k in known):
            continue
        last = max(g["rows"])
        found.append({"name": g["name"], "price": price, "months": best,
                      "day": int(last[8:10]), "last": last})
    return sorted(found, key=lambda c: (c["name"], c["price"]))
