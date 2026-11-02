"""쿨링오프 + 후회 일기의 도메인 로직.

시간이 필요한 함수는 모두 호출자가 넘긴 now / today 를 기준으로 계산합니다.
그래서 테스트는 가짜 시각을 넘겨 쿨링오프 종료, 30일 뒤 확인, 구독 갱신을 재현합니다.
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import List, Optional, Tuple

DEFAULT_COOL_DAYS = 3
LATER_HOURS = 24  # "later"를 고르면 이만큼 뒤에 다시 물어봅니다.
FOLLOWUP_DAYS = 30  # 구매 후 사후 확인까지 걸리는 날수
RENEWAL_WINDOW_DAYS = 3  # 갱신 며칠 전부터 알릴지
REGRET_MAX_SATISFACTION = 2  # 이 값 이하이면 후회 목록
CHICKEN_PRICE = 20000  # 구독료 총합을 치킨 몇 마리로 환산할 때 기준 (원)
PER_MAN_WON = 10000  # 원당 만족도는 1만원당 점수로 보여 줍니다.

STATUS_COOLING = "cooling"
STATUS_APPROVED = "approved"
STATUS_SKIPPED = "skipped"
STATUS_BOUGHT = "bought"
STATUS_LABELS = {
    STATUS_COOLING: "쿨링오프 중",
    STATUS_APPROVED: "구매 허가",
    STATUS_SKIPPED: "참은 것",
    STATUS_BOUGHT: "구매함",
}

USAGE_DAILY = "매일"
USAGE_WEEKLY = "주1-2"
USAGE_RARE = "거의 안 씀"
_USAGE_ALIASES = {
    "매일": USAGE_DAILY, "1": USAGE_DAILY,
    "주1-2": USAGE_WEEKLY, "주1-2회": USAGE_WEEKLY, "주12": USAGE_WEEKLY, "2": USAGE_WEEKLY,
    "거의안씀": USAGE_RARE, "거의": USAGE_RARE, "안씀": USAGE_RARE, "3": USAGE_RARE,
}

CYCLE_MONTHLY = "monthly"
CYCLE_YEARLY = "yearly"
CYCLES = (CYCLE_MONTHLY, CYCLE_YEARLY)


class LedgerError(ValueError):
    """사용자 입력이나 저장 데이터 문제로 작업을 끝낼 수 없을 때 쓰는 오류."""


def empty_state() -> dict:
    return {
        "version": 1,
        "next_id": 1,
        "wishes": [],
        "purchases": [],
        "subscriptions": [],
        "notified": {},
    }


# ---------------------------------------------------------------- 입력 해석

def parse_money(text) -> int:
    cleaned = str(text).strip().replace(",", "").replace("원", "").replace("₩", "").strip()
    if not cleaned.isdigit():
        raise LedgerError(f"금액은 숫자로 적어 주세요: {text!r}")
    value = int(cleaned)
    if value <= 0:
        raise LedgerError("금액은 0보다 커야 해요.")
    return value


def parse_satisfaction(text) -> int:
    try:
        value = int(str(text).strip())
    except ValueError:
        raise LedgerError("만족도는 1~5 숫자로 적어 주세요.")
    if not 1 <= value <= 5:
        raise LedgerError("만족도는 1~5 사이여야 해요.")
    return value


def normalize_usage(text) -> str:
    key = "".join(str(text).split()).replace("~", "-")
    if key in _USAGE_ALIASES:
        return _USAGE_ALIASES[key]
    raise LedgerError("사용 빈도는 매일 / 주1-2 / 거의 안 씀 중에서 골라 주세요.")


def parse_day(text, today: dt.date) -> dt.date:
    value = str(text).strip().lower()
    if value in ("", "오늘", "today"):
        return today
    if value in ("어제", "yesterday"):
        return today - dt.timedelta(days=1)
    try:
        day = dt.date.fromisoformat(value)
    except ValueError:
        raise LedgerError("날짜는 2026-11-02 형식이나 오늘/어제로 적어 주세요.")
    if day > today:
        raise LedgerError("미래 날짜는 기록할 수 없어요.")
    return day


def _clean_name(name) -> str:
    cleaned = " ".join(str(name).split())
    if not cleaned:
        raise LedgerError("이름을 적어 주세요.")
    return cleaned


def _stamp(moment: dt.datetime) -> str:
    return moment.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def _moment(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


# ---------------------------------------------------------------- 날짜 계산

def followup_date(bought_on: dt.date) -> dt.date:
    return bought_on + dt.timedelta(days=FOLLOWUP_DAYS)


def occurrence(year: int, month: int, day: int) -> dt.date:
    """해당 달에 day일이 없으면 말일로 당깁니다 (예: 31일 -> 2월 28일)."""
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(day, last))


def next_renewal(sub: dict, today: dt.date) -> dt.date:
    """today 이후(당일 포함) 가장 가까운 갱신일."""
    if sub["cycle"] == CYCLE_MONTHLY:
        candidate = occurrence(today.year, today.month, sub["day"])
        if candidate < today:
            year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            candidate = occurrence(year, month, sub["day"])
        return candidate
    candidate = occurrence(today.year, sub["month"], sub["day"])
    if candidate < today:
        candidate = occurrence(today.year + 1, sub["month"], sub["day"])
    return candidate


def yearly_cost(sub: dict) -> int:
    return sub["price"] * 12 if sub["cycle"] == CYCLE_MONTHLY else sub["price"]


def describe_schedule(sub: dict) -> str:
    if sub["cycle"] == CYCLE_MONTHLY:
        return f"매달 {sub['day']}일"
    return f"매년 {sub['month']}월 {sub['day']}일"


def korean_won(amount: int) -> str:
    """324000 -> '32만4천원' 처럼 말로 읽기 좋게 줄입니다."""
    amount = int(amount)
    if amount <= 0:
        return "0원"
    eok, rest = divmod(amount, 100_000_000)
    man, rest = divmod(rest, 10_000)
    cheon, rest = divmod(rest, 1000)
    baek, rest = divmod(rest, 100)
    out = ""
    if eok:
        out += f"{eok:,}억"
    if man:
        out += f"{man:,}만"
    if cheon:
        out += f"{cheon}천"
    if baek:
        out += f"{baek}백"
    if rest:
        out += f"{rest}"
    return out + "원"


def value_per_man(purchase: dict) -> float:
    """1만원당 만족도 점수. 만족도가 기록된 구매에만 씁니다."""
    return purchase["satisfaction"] * PER_MAN_WON / purchase["price"]


# ---------------------------------------------------------------- 장부

class Ledger:
    """상태(dict)를 감싸고 규칙을 적용합니다. 저장은 호출자가 Store로 합니다."""

    def __init__(self, state: Optional[dict] = None):
        self.state = state if state is not None else empty_state()

    def _new_id(self) -> int:
        value = self.state["next_id"]
        self.state["next_id"] = value + 1
        return value

    # -- 담아 두기 / 쿨링오프

    def add_wish(self, name, price, now: dt.datetime, url: str = "", why: str = "",
                 days: int = DEFAULT_COOL_DAYS) -> dict:
        name = _clean_name(name)
        price = int(price)
        if price <= 0:
            raise LedgerError("금액은 0보다 커야 해요.")
        if days < 0:
            raise LedgerError("쿨링오프 기간은 0일 이상이어야 해요.")
        wish = {
            "id": self._new_id(),
            "name": name,
            "price": price,
            "url": url or "",
            "why": why or "",
            "status": STATUS_COOLING,
            "created_at": _stamp(now),
            "cool_until": _stamp(now + dt.timedelta(days=days)),
            "postponed": 0,
            "decided_at": None,
            "purchase_id": None,
        }
        self.state["wishes"].append(wish)
        return wish

    def wishes(self, status: Optional[str] = None) -> List[dict]:
        return [w for w in self.state["wishes"] if status is None or w["status"] == status]

    def due_wishes(self, now: dt.datetime) -> List[dict]:
        due = [w for w in self.wishes(STATUS_COOLING) if _moment(w["cool_until"]) <= now]
        return sorted(due, key=lambda w: w["cool_until"])

    def approved_wishes(self) -> List[dict]:
        return self.wishes(STATUS_APPROVED)

    def decide_wish(self, wish: dict, answer: str, now: dt.datetime) -> str:
        """answer: 'y' 구매 허가 / 'n' 참음 / 'later' 하루 뒤에 다시 묻기."""
        if answer == "y":
            wish["status"] = STATUS_APPROVED
            wish["decided_at"] = _stamp(now)
        elif answer == "n":
            wish["status"] = STATUS_SKIPPED
            wish["decided_at"] = _stamp(now)
        elif answer == "later":
            wish["cool_until"] = _stamp(now + dt.timedelta(hours=LATER_HOURS))
            wish["postponed"] += 1
        else:
            raise LedgerError(f"알 수 없는 답: {answer}")
        return wish["status"]

    def saved_total(self) -> Tuple[int, int]:
        """(참은 것 건수, 아낀 돈 합계)."""
        skipped = self.wishes(STATUS_SKIPPED)
        return len(skipped), sum(w["price"] for w in skipped)

    # -- 구매 기록 / 사후 확인

    def record_purchase(self, name, price: Optional[int], bought_on: dt.date,
                        now: Optional[dt.datetime] = None) -> Tuple[dict, Optional[dict]]:
        name = _clean_name(name)
        candidates = [
            w for w in self.state["wishes"]
            if w["name"].casefold() == name.casefold() and w["status"] in (STATUS_APPROVED, STATUS_COOLING)
        ]
        candidates.sort(key=lambda w: 0 if w["status"] == STATUS_APPROVED else 1)
        wish = candidates[0] if candidates else None
        if price is None:
            if wish is None:
                raise LedgerError("담아 둔 적 없는 것은 --price로 가격을 알려 주세요.")
            price = wish["price"]
        price = int(price)
        if price <= 0:
            raise LedgerError("금액은 0보다 커야 해요.")
        purchase = {
            "id": self._new_id(),
            "name": name,
            "price": price,
            "date": bought_on.isoformat(),
            "wish_id": wish["id"] if wish else None,
            "during_cooling": self._still_cooling(wish, now),
            "satisfaction": None,
            "usage": None,
            "answered_at": None,
        }
        self.state["purchases"].append(purchase)
        if wish is not None:
            wish["status"] = STATUS_BOUGHT
            wish["purchase_id"] = purchase["id"]
        return purchase, wish

    @staticmethod
    def _still_cooling(wish: Optional[dict], now: Optional[dt.datetime]) -> bool:
        if wish is None or wish["status"] != STATUS_COOLING:
            return False
        if now is None:
            return True
        return now < _moment(wish["cool_until"])

    def due_followups(self, today: dt.date) -> List[dict]:
        due = [
            p for p in self.state["purchases"]
            if p["satisfaction"] is None and p.get("source", "manual") == "manual"
            and followup_date(dt.date.fromisoformat(p["date"])) <= today
        ]
        return sorted(due, key=lambda p: p["date"])

    def record_followup(self, purchase: dict, satisfaction, usage, now: dt.datetime) -> None:
        if not isinstance(satisfaction, int) or not 1 <= satisfaction <= 5:
            raise LedgerError("만족도는 1~5 사이여야 해요.")
        usage = normalize_usage(usage)  # 검증이 끝난 뒤에만 기록합니다.
        purchase["satisfaction"] = satisfaction
        purchase["usage"] = usage
        purchase["answered_at"] = _stamp(now)

    # -- 구독

    def add_subscription(self, name, price, cycle: str, day: Optional[int] = None,
                         month: Optional[int] = None, today: Optional[dt.date] = None) -> dict:
        if today is None:
            raise LedgerError("기준 날짜가 없어요.")
        name = _clean_name(name)
        price = int(price)
        if price <= 0:
            raise LedgerError("금액은 0보다 커야 해요.")
        if cycle not in CYCLES:
            raise LedgerError("주기는 monthly 또는 yearly 만 가능해요.")
        day = today.day if day is None else day
        if not 1 <= day <= 31:
            raise LedgerError("결제일은 1~31 사이여야 해요.")
        if cycle == CYCLE_YEARLY:
            month = today.month if month is None else month
            if not 1 <= month <= 12:
                raise LedgerError("결제 월은 1~12 사이여야 해요.")
        else:
            month = None
        sub = {
            "id": self._new_id(),
            "name": name,
            "price": price,
            "cycle": cycle,
            "day": day,
            "month": month,
            "created_at": today.isoformat(),
        }
        self.state["subscriptions"].append(sub)
        return sub

    def remove_subscription(self, key: str) -> dict:
        subs = self.state["subscriptions"]
        key = str(key).strip()
        matches = [s for s in subs if str(s["id"]) == key]
        if not matches:
            matches = [s for s in subs if s["name"].casefold() == key.casefold()]
        if not matches:
            raise LedgerError(f"구독을 찾지 못했어요: {key}")
        if len(matches) > 1:
            raise LedgerError("같은 이름의 구독이 여럿이라 번호로 지정해 주세요.")
        subs.remove(matches[0])
        return matches[0]

    def subscription_totals(self) -> dict:
        yearly = sum(yearly_cost(s) for s in self.state["subscriptions"])
        monthly = (yearly + 6) // 12  # 반올림(0.5 이상 올림)
        return {"monthly": monthly, "yearly": yearly, "chickens": yearly // CHICKEN_PRICE}

    def subscription_funeral(self, sub: dict, today: dt.date) -> dict:
        """삭제한 구독의 결제 횟수와 낸 돈. 등록일(created_at)부터 오늘까지의 결제일을 셉니다."""
        start = dt.date.fromisoformat(sub["created_at"])
        count = 0
        if sub["cycle"] == CYCLE_MONTHLY:
            y, m = start.year, start.month
            while (y, m) <= (today.year, today.month):
                if start <= occurrence(y, m, sub["day"]) <= today:
                    count += 1
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        else:
            for y in range(start.year, today.year + 1):
                if start <= occurrence(y, sub["month"], sub["day"]) <= today:
                    count += 1
        months = (today.year - start.year) * 12 + today.month - start.month
        return {"payments": count, "total": count * sub["price"], "months": max(months, 0),
                "from_year": start.year, "to_year": today.year}

    def due_renewals(self, today: dt.date, window: int = RENEWAL_WINDOW_DAYS) -> List[Tuple[dict, dt.date, int]]:
        due = []
        for sub in self.state["subscriptions"]:
            renewal = next_renewal(sub, today)
            left = (renewal - today).days
            if 0 <= left <= window:
                due.append((sub, renewal, left))
        return sorted(due, key=lambda t: t[1])

    # -- 알림 중복 방지

    def is_notified(self, key: str) -> bool:
        return key in self.state["notified"]

    def mark_notified(self, key: str, now: dt.datetime) -> None:
        self.state["notified"][key] = _stamp(now)

    # -- 리포트

    def report(self) -> dict:
        purchases = self.state["purchases"]
        rated = [p for p in purchases if p["satisfaction"] is not None]
        avg = sum(p["satisfaction"] for p in rated) / len(rated) if rated else None
        regrets = [
            p for p in rated
            if p["satisfaction"] <= REGRET_MAX_SATISFACTION or p["usage"] == USAGE_RARE
        ]
        ranking = sorted(rated, key=lambda p: (-value_per_man(p), p["date"]))
        monthly = {}
        for p in purchases:
            month = p["date"][:7]
            monthly[month] = monthly.get(month, 0) + p["price"]
        saved_count, saved_total = self.saved_total()
        sources = {}
        for p in purchases:
            entry = sources.setdefault(p.get("source") or "manual", {"count": 0, "total": 0, "scores": []})
            entry["count"] += 1
            entry["total"] += p["price"]
            if p["satisfaction"] is not None:
                entry["scores"].append(p["satisfaction"])
        by_source = [
            {"source": src, "count": e["count"], "total": e["total"], "rated": len(e["scores"]),
             "avg": sum(e["scores"]) / len(e["scores"]) if e["scores"] else None}
            for src, e in sources.items()
        ]
        return {
            "by_source": by_source,
            "saved_count": saved_count,
            "saved_total": saved_total,
            "purchase_count": len(purchases),
            "spent_total": sum(p["price"] for p in purchases),
            "rated_count": len(rated),
            "avg_satisfaction": avg,
            "regrets": sorted(regrets, key=lambda p: p["date"]),
            "ranking": ranking,
            "monthly": sorted(monthly.items()),
        }
