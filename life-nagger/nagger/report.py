"""월간 근무 리포트: Markdown + 자체 완결형 HTML(외부 리소스 없음)."""
import calendar
import html
from datetime import date

from .timeutil import fmt_duration

BAR_W = 20


def month_data(days, month, today):
    """month='YYYY-MM'. 반환: dict(rows, total_work, total_game, ...)"""
    y, m = int(month[:4]), int(month[5:7])
    last = calendar.monthrange(y, m)[1]
    if (today.year, today.month) == (y, m):
        last = today.day
    rows = []
    for d in range(1, last + 1):
        key = date(y, m, d).isoformat()
        rec = days.get(key, {})
        rows.append({"date": key, "day": d, "weekday": "월화수목금토일"[date(y, m, d).weekday()],
                     "work": float(rec.get("work", 0)), "game": float(rec.get("game", 0)),
                     "first_in": rec.get("first_in"), "last_work": rec.get("last_work")})
    worked = [r for r in rows if r["work"] > 0]
    total = sum(r["work"] for r in rows)
    return {"month": month, "year": y, "mon": m, "rows": rows, "total_work": total,
            "total_game": sum(r["game"] for r in rows), "work_days": len(worked),
            "best": max(worked, key=lambda r: r["work"]) if worked else None,
            "is_current": (today.year, today.month) == (y, m)}


def headline(data):
    label = "이번 달" if data["is_current"] else "%d년 %d월" % (data["year"], data["mon"])
    return "%s 순수 작업 시간 %s" % (label, fmt_duration(data["total_work"]))


def quip(data):
    h = data["total_work"] / 3600.0
    if h == 0:
        return "기록이 텅 비었어요. 데몬은 켜 두셨죠?"
    if h < 40:
        return "아직 워밍업 중이시군요."
    if h < 120:
        return "꾸준합니다. 칭찬해요."
    if h < 180:
        return "풀타임 직원이시네요. 월급은 받고 계신 거죠?"
    return "이건 사람이 아니라 서버입니다. 제발 쉬어요."


def _bar(sec, mx):
    n = 0 if mx <= 0 else int(round(BAR_W * sec / mx))
    if sec > 0 and n == 0:
        n = 1
    return "█" * n


def render_markdown(data):
    mx = max([r["work"] for r in data["rows"]] + [0])
    L = ["# %s" % headline(data), "", "_%s_" % quip(data), ""]
    L += ["- 일한 날: %d일" % data["work_days"]]
    if data["work_days"]:
        L.append("- 하루 평균: %s" % fmt_duration(data["total_work"] / data["work_days"]))
        b = data["best"]
        L.append("- 최고 기록: %d일(%s) %s" % (b["day"], b["weekday"], fmt_duration(b["work"])))
    L += ["- 게임 시간: %s" % fmt_duration(data["total_game"]), "", "## 일별", "", "```"]
    for r in data["rows"]:
        span = ("%s~%s" % (r["first_in"][:5], r["last_work"][:5])) if r["first_in"] else "-"
        L.append("%02d(%s) %-20s %9s  %s" % (r["day"], r["weekday"], _bar(r["work"], mx),
                                            fmt_duration(r["work"]) if r["work"] else "·", span))
    L += ["```", ""]
    return "\n".join(L)


def render_html(data):
    e = html.escape
    mx = max([r["work"] for r in data["rows"]] + [0])
    rows = []
    for r in data["rows"]:
        pct = 0 if mx <= 0 else round(100.0 * r["work"] / mx, 1)
        span = ("%s~%s" % (r["first_in"][:5], r["last_work"][:5])) if r["first_in"] else ""
        rows.append('<tr><td>%d(%s)</td><td class="b"><div style="width:%s%%"></div></td><td>%s</td><td class="s">%s</td></tr>'
                    % (r["day"], e(r["weekday"]), pct, e(fmt_duration(r["work"]) if r["work"] else "·"), e(span)))
    return """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
body{font-family:system-ui,"Malgun Gothic",sans-serif;max-width:640px;margin:2rem auto;padding:0 16px;background:#fff;color:#222}
h1{font-size:1.4rem}table{border-collapse:collapse;width:100%%}td{padding:2px 6px;font-size:.9rem}
td.b{width:55%%;background:#f1f1f1}td.b div{height:14px;background:#3b82f6;border-radius:2px}td.s{color:#777}
@media(prefers-color-scheme:dark){body{background:#161616;color:#eee}td.b{background:#2a2a2a}}
</style></head><body>
<h1>%(title)s</h1><p>%(quip)s</p>
<p>일한 날 %(days)d일 · 게임 시간 %(game)s</p>
<table>%(rows)s</table></body></html>
""" % {"title": e(headline(data)), "quip": e(quip(data)), "days": data["work_days"],
       "game": e(fmt_duration(data["total_game"])), "rows": "\n".join(rows)}
