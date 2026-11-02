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

NAME = "cool_off"
SECTION = "소비·구독"
DEFAULTS = {"enabled": True, "data": "", "renewal_days": 3, "followup_days": 30, "show_approved": True}


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
        name = ctx.red(sub.get("name") or "구독")
        price = int(sub.get("price") or 0)
        word = when_phrase(when, today)
        items.append(Item(NAME, "subscription", "%s 갱신 %s (%s)" % (name, won_text(price), word),
                          "다음 결제일 %s. 안 쓰면 지금 해지를 검토하세요." % when.isoformat(),
                          {0: 80, 1: 72, 2: 66}.get(left, 60), "구독 갱신: %s %s, %s" % (name, word, ko_won(price)),
                          "%s 갱신 %s" % (name, word), when.isoformat(),
                          ("해지 검토: 구독 서비스에서 직접 해지",)))

    for w in sorted((w for w in state.get("wishes") or [] if w.get("status") == "cooling"),
                    key=lambda w: str(w.get("cool_until"))):
        try:
            if dt.datetime.fromisoformat(w["cool_until"]) > now_naive:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        name = ctx.red(w.get("name") or "물건")
        price = int(w.get("price") or 0)
        items.append(Item(NAME, "cooloff-ended", "쿨링오프 끝: %s %s" % (name, won_text(price)),
                          str(w.get("why") or ""), 40, "쿨링오프 끝: %s %s, 아직 갖고 싶은지 정하세요" % (name, ko_won(price)),
                          "쿨링오프 끝 %s" % name, str(w["cool_until"])[:16],
                          ("결정하기: python -m cool_off check",)))

    for p in sorted((p for p in state.get("purchases") or []
                     if p.get("satisfaction") is None and (p.get("source") or "manual") == "manual"),
                    key=lambda p: str(p.get("date"))):
        try:
            due = dt.date.fromisoformat(p["date"]) + dt.timedelta(days=int(cfg["followup_days"]))
        except (KeyError, ValueError, TypeError):
            continue
        if due > today:
            continue
        name = ctx.red(p.get("name") or "구매")
        items.append(Item(NAME, "followup", "사후 확인: %s" % name, "%s 구매, 아직 쓰는지 만족도 1~5" % p.get("date"),
                          35, "사후 확인: %s 아직 쓰나요" % name, "", due.isoformat(),
                          ("답하기: python -m cool_off check",)))

    if cfg.get("show_approved"):
        for w in state.get("wishes") or []:
            if w.get("status") == "approved":
                name = ctx.red(w.get("name") or "물건")
                items.append(Item(NAME, "approved", "구매 허가됨(아직 안 삼): %s %s" % (name, won_text(w.get("price") or 0)),
                                  "", 14, "", "", None, ()))
    return items
