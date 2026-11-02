"""연말결산과 경력 증명서에 쓰는 합계. 입력(steam 기록)과 합계가 어긋나지 않게 한 곳에서만 센다."""
from datetime import datetime, timedelta

from .importers import parse_iso, watch_count
from .render import rank_for

PLAYED_MIN = 6  # 0.1시간 미만이면 안 켜 본 것으로 본다 (steam-buddy와 같은 기준)


def _bought(g):
    d = parse_iso(g.get("purchased_at"))
    return d.replace(tzinfo=None) if d else None


def wrapped_stats(store, year=None, now=None):
    now = now or datetime.now()
    asof = now
    if year:
        asof = min(now, datetime(year, 12, 31, 23, 59, 59))
    games = []
    for g in store.steam.values():
        b = _bought(g)
        if year and (b is None or b.year != year):
            continue
        games.append((g, b))
    minutes = sum(int(g.get("minutes", 0)) for g, _ in games)
    spend = sum(g.get("price_paid", 0) for g, _ in games)
    rows = []
    for g, b in games:
        h = g.get("minutes", 0) / 60.0
        rows.append({"name": g["name"], "price": g.get("price_paid", 0), "hours": h,
                     "per_hour": g.get("price_paid", 0) / h if h >= 0.1 else None, "bought": b,
                     "rating": g.get("rating")})
    priced = [r for r in rows if r["per_hour"] is not None and r["price"] > 0]
    best = min(priced, key=lambda r: (r["per_hour"], r["name"])) if priced else None
    worst = max(priced, key=lambda r: (r["per_hour"], r["name"])) if priced else None
    if best is worst:
        worst = None
    unplayed = [r for r in rows if r["hours"] * 60 < PLAYED_MIN]
    shelved = None
    dated = [r for r in unplayed if r["bought"] is not None and r["bought"] <= asof]
    if dated:
        r = min(dated, key=lambda r: (r["bought"], r["name"]))
        shelved = {"name": r["name"], "days": (asof - r["bought"]).days}
    top = max(rows, key=lambda r: (r["hours"], r["name"]), default=None)
    rated = [r["rating"] for r in rows if r["rating"]]
    out = {"year": year, "count": len(games), "minutes": minutes, "hours": minutes / 60.0, "spend": spend,
           "best": best, "worst": worst, "shelved": shelved,
           "top": top if top and top["hours"] > 0 else None,
           "avg_rating": sum(rated) / len(rated) if rated else None, "rated": len(rated),
           "unplayed": len(unplayed), "videos_watched": None, "videos_distinct": None}
    if store.youtube:
        n = d = 0
        for rec in store.youtube.values():
            times = [parse_iso(t) for t in rec.get("times", [])]
            c = sum(1 for t in times if t and (not year or t.year == year))
            if not year and not times:
                c = watch_count(rec)
            if c:
                n += c
                d += 1
        out["videos_watched"], out["videos_distinct"] = n, d
    return out


def career_stats(store, units, tagdict, name="무명 게이머", now=None):
    now = now or datetime.now()
    games = [(g, _bought(g)) for g in store.steam.values()]
    minutes = sum(int(g.get("minutes", 0)) for g, _ in games)
    dates = sorted(b for _, b in games if b)
    if dates:
        a, z = dates[0], dates[-1]
        months = (z.year - a.year) * 12 + z.month - a.month + 1
        period = f"{a:%Y.%m} ~ {z:%Y.%m} ({months // 12}년 {months % 12}개월)"
    else:
        period = "기록 없음"
    hours = minutes / 60.0
    projects = sorted(({"name": g["name"], "hours": g.get("minutes", 0) / 60.0, "rating": g.get("rating")}
                       for g, _ in games), key=lambda p: (-p["hours"], p["name"]))
    projects = [p for p in projects if p["hours"] > 0][:5]
    # 전문 분야: 플레이 시간으로 가중한 태그 (영상 전용 태그 제외)
    acc = {}
    for u in units:
        if u.kind == "game" and u.hours > 0:
            for t, w in u.vec.items():
                if tagdict.tags.get(t, {}).get("group") != "video":
                    acc[t] = acc.get(t, 0.0) + w * u.hours
    fields = [tagdict.label(t) for t, _ in sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    return {"name": (name or "무명 게이머")[:20], "hours": hours, "titles": len(games), "period": period,
            "rank": rank_for(hours), "projects": projects, "fields": fields,
            "five_star": sum(1 for g, _ in games if g.get("rating") == 5),
            "issued": f"{now.year}년 {now.month}월 {now.day}일"}
