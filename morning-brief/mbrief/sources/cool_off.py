"""cool-off 어댑터: data.json 에서 구독 갱신, 쿨링오프 만료, 사후 확인, 구매 허가 후 미구매.

형식(cool-off/cool_off/core.py): {version, next_id, wishes[], purchases[], subscriptions[], notified{}}.
wish: {id, name, price, status 'cooling'|'approved'|'skipped'|'bought', cool_until 'YYYY-MM-DDTHH:MM', why}.
purchase: {id, name, price, date, satisfaction(None=미응답), source(없으면 'manual')}.
subscription: {id, name, price, cycle 'monthly'|'yearly', day, month}.
데이터는 COOLOFF_HOME(기본 ~/.cool-off)/data.json. 읽기만 하고 notified 는 건드리지 않는다.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json

from ..model import Context, Item, SourceMissing
from ..speech import ko_won, when_phrase, won_text
from . import _contract

NAME = "cool_off"
SECTION = "소비·구독"
DEFAULTS = {"enabled": True, "data": "", "renewal_days": 3, "followup_days": 30, "show_approved": True,
            **_contract.JSON_DEFAULTS}


def _occurrence(year: int, month: int, day: int) -> dt.date:
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def next_renewal(sub: dict, today: dt.date) -> dt.date:
    """core.next_renewal 과 같은 규칙: today 이후(당일 포함) 가장 가까운 갱신일, 없는 날은 말일."""
    if sub["cycle"] == "monthly":
        cand = _occurrence(today.year, today.month, int(sub["day"]))
        if cand < today:
            y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            cand = _occurrence(y, m, int(sub["day"]))
        return cand
    cand = _occurrence(today.year, int(sub["month"]), int(sub["day"]))
    if cand < today:
        cand = _occurrence(today.year + 1, int(sub["month"]), int(sub["day"]))
    return cand


def collect(cfg: dict, ctx: Context) -> list[Item]:
    return _contract.collect_with_mode(NAME, cfg, ctx, lambda: _collect_json(cfg, ctx),
                                       lambda: _collect_direct(cfg, ctx))


def _renewal_item(name: str, price: int, when: dt.date, today: dt.date) -> Item:
    left = (when - today).days
    word = when_phrase(when, today)
    return Item(NAME, "subscription", "%s 갱신 %s (%s)" % (name, won_text(price), word),
                "다음 결제일 %s. 안 쓰면 지금 해지를 검토하세요." % when.isoformat(),
                {0: 80, 1: 72, 2: 66}.get(left, 60), "구독 갱신: %s %s, %s" % (name, word, ko_won(price)),
                "%s 갱신 %s" % (name, word), when.isoformat(), ("해지 검토: 구독 서비스에서 직접 해지",))


def _cooloff_item(name: str, price: int, why: str, due: str) -> Item:
    return Item(NAME, "cooloff-ended", "쿨링오프 끝: %s %s" % (name, won_text(price)), why, 40,
                "쿨링오프 끝: %s %s, 아직 갖고 싶은지 정하세요" % (name, ko_won(price)), "쿨링오프 끝 %s" % name, due,
                ("결정하기: python -m cool_off check",))


def _followup_item(name: str, bought: str, due: dt.date) -> Item:
    return Item(NAME, "followup", "사후 확인: %s" % name, "%s 구매, 아직 쓰는지 만족도 1~5" % bought, 35,
                "사후 확인: %s 아직 쓰나요" % name, "", due.isoformat(), ("답하기: python -m cool_off check",))


def _approved_item(name: str, price) -> Item:
    return Item(NAME, "approved", "구매 허가됨(아직 안 삼): %s %s" % (name, won_text(price or 0)),
                "", 14, "", "", None, ())


def _collect_json(cfg: dict, ctx: Context) -> list[Item]:
    """`cool_off check --json` (비대화형, 읽기 전용, schema v1)."""
    today = ctx.today
    env = {"COOLOFF_HOME": str(ctx.path(cfg["data"]).parent)} if cfg.get("data") else {}
    obj = _contract.run_json(cfg, ctx, project="cool-off", pkg="cool_off", args=["check", "--json"], env_extra=env)
    items: list[Item] = []
    for r in sorted(obj["renewals"], key=lambda r: (r["date"], str(r["name"]))):
        when = dt.date.fromisoformat(r["date"])
        if (when - today).days > int(cfg["renewal_days"]):
            continue
        items.append(_renewal_item(ctx.red(r["name"] or "구독"), int(r["amount"] or 0), when, today))
    for w in obj["cooling_ended"]:
        items.append(_cooloff_item(ctx.red(w["name"] or "물건"), int(w["price"] or 0), "", str(w["ended"])))
    for p in obj["followups_due"]:
        due = dt.date.fromisoformat(p["bought"]) + dt.timedelta(days=int(cfg["followup_days"]))
        if due > today:
            continue
        items.append(_followup_item(ctx.red(p["name"] or "구매"), p["bought"], due))
    if cfg.get("show_approved"):
        for w in obj["approved_not_bought"]:
            items.append(_approved_item(ctx.red(w["name"] or "물건"), int(w["price"] or 0)))
    return items


def _collect_direct(cfg: dict, ctx: Context) -> list[Item]:
    path = ctx.path(cfg["data"]) if cfg.get("data") else ctx.home_path("COOLOFF_HOME", "~/.cool-off") / "data.json"
    if not path.is_file():
        raise SourceMissing("cool-off data.json 이 없어요: %s" % path.name)
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("data.json 형식이 올바르지 않아요")
    today = ctx.today
    now_naive = ctx.now.replace(tzinfo=None)
    items: list[Item] = []

    renewals = []
    for sub in state.get("subscriptions") or []:
        try:
            when = next_renewal(sub, today)
            renewals.append((sub, when, (when - today).days))
        except (KeyError, ValueError, TypeError):
            continue
    for sub, when, left in sorted(renewals, key=lambda t: (t[1], str(t[0].get("name")))):
        if left > int(cfg["renewal_days"]):
            continue
        items.append(_renewal_item(ctx.red(sub.get("name") or "구독"), int(sub.get("price") or 0), when, today))

    for w in sorted((w for w in state.get("wishes") or [] if w.get("status") == "cooling"),
                    key=lambda w: str(w.get("cool_until"))):
        try:
            if dt.datetime.fromisoformat(w["cool_until"]) > now_naive:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        items.append(_cooloff_item(ctx.red(w.get("name") or "물건"), int(w.get("price") or 0),
                                   str(w.get("why") or ""), str(w["cool_until"])[:16]))

    for p in sorted((p for p in state.get("purchases") or []
                     if p.get("satisfaction") is None and (p.get("source") or "manual") == "manual"),
                    key=lambda p: str(p.get("date"))):
        try:
            due = dt.date.fromisoformat(p["date"]) + dt.timedelta(days=int(cfg["followup_days"]))
        except (KeyError, ValueError, TypeError):
            continue
        if due > today:
            continue
        items.append(_followup_item(ctx.red(p.get("name") or "구매"), str(p.get("date")), due))

    if cfg.get("show_approved"):
        for w in state.get("wishes") or []:
            if w.get("status") == "approved":
                items.append(_approved_item(ctx.red(w.get("name") or "물건"), w.get("price")))
    return items
