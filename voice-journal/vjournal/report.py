# -*- coding: utf-8 -*-
"""주간 성적표 (Markdown + 자급자족 HTML) 와 월간 Markdown 내보내기."""
import calendar
import html
from collections import Counter
from datetime import date, datetime, timedelta

from .grading import OVERALL, grade_goal, overall_grade


def week_bounds(d):
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _in_week(g, ws, we):
    created = g["created_day"]
    if g["status"] == "dropped" and g["dropped_day"] and g["dropped_day"] < ws.isoformat():
        return False
    if g["period"] in ("day", "week"):
        if created > we.isoformat():
            return False
        if g.get("start_day") and g["start_day"] > we.isoformat():
            return False
        if g.get("deadline") and g["deadline"] < ws.isoformat():
            return False
        return True
    if g["deadline"]:
        return ws.isoformat() <= g["deadline"] <= we.isoformat()
    return ws.isoformat() <= created <= we.isoformat()


def build_scorecard(journal, on_day=None):
    today = journal.today()
    ref = on_day or today
    ws, we = week_bounds(ref)
    results, upcoming = [], []
    for g in journal.goals():
        if _in_week(g, ws, we):
            results.append(grade_goal(g, journal.goal_events(g["id"]), ws, we, today))
        elif (g["period"] == "once" and g["deadline"] and g["deadline"] > we.isoformat()
              and ws.isoformat() <= g["created_day"] <= we.isoformat() and g["status"] == "open"):
            upcoming.append(g)
    finals = [r for r in results if r["final"]]
    grade, avg = overall_grade(finals if finals else results)
    provisional = not finals and bool(results)
    entries = journal.entries(ws, we)
    diary = [e for e in entries if e["kind"] == "diary"]
    moods = [e["mood"] for e in entries if e["mood"]]
    tags = Counter(t for e in entries for t in e["tags"])
    return {
        "week_start": ws.isoformat(), "week_end": we.isoformat(), "results": results,
        "upcoming": upcoming, "provisional": provisional, "grade": grade, "avg": avg, "comment": OVERALL[grade],
        "stats": {"entries": len(diary), "days": len({e["day"] for e in entries}),
                  "checkouts": len({e["day"] for e in entries if e["kind"] == "checkout"}),
                  "avg_mood": round(sum(moods) / len(moods), 2) if moods else None,
                  "top_tags": tags.most_common(5)},
    }


def _pct(r):
    return "-" if r is None else "%d%%" % round(r * 100)


def render_scorecard_md(sc):
    L = ["# 주간 성적표 (%s ~ %s)" % (sc["week_start"], sc["week_end"]), ""]
    L.append("**종합 성적: %s%s**" % (sc["grade"] or "해당 없음", " (중간)" if sc["provisional"] else "") +
             ("  (달성률 %s)" % _pct(sc["avg"]) if sc["avg"] is not None else ""))
    L.append("")
    L.append("> " + sc["comment"])
    L.append("")
    if sc["results"]:
        L.append("| 목표 | 결과 | 성적 | 한마디 |")
        L.append("|---|---|:-:|---|")
        for r in sc["results"]:
            g = (r["grade"] or "-") + ("" if r["final"] else " (중간)")
            L.append("| %s | %s | %s | %s |" % (r["title"].replace("|", "/"), r["note"], g, r["comment"]))
        L.append("")
    if sc["upcoming"]:
        L.append("## 다음 주 이후 마감")
        for g in sc["upcoming"]:
            L.append("- %s (마감 %s)" % (g["title"], g["deadline"]))
        L.append("")
    s = sc["stats"]
    L.append("## 이번 주 기록")
    L.append("- 일기 %d건, 기록한 날 %d일, 퇴근 3문답 %d일" % (s["entries"], s["days"], s["checkouts"]))
    if s["avg_mood"] is not None:
        L.append("- 평균 기분 %s / 5" % s["avg_mood"])
    if s["top_tags"]:
        L.append("- 자주 쓴 태그: " + ", ".join("#%s(%d)" % t for t in s["top_tags"]))
    return "\n".join(L) + "\n"


_CSS = """
:root{--bg:#fafaf7;--fg:#222;--mut:#666;--card:#fff;--line:#e4e2da;--A:#2e7d32;--B:#558b2f;--C:#f9a825;--D:#ef6c00;--F:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#eee;--mut:#aaa;--card:#202020;--line:#333}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,'Noto Sans KR',sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px}
h1{font-size:1.4rem;margin:0 0 4px}.mut{color:var(--mut)}
.big{font-size:3rem;font-weight:800;margin:8px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.row{display:flex;gap:12px;align-items:center}.row .g{font-size:1.8rem;font-weight:800;min-width:2.2rem;text-align:center}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:6px}.bar i{display:block;height:100%}
.gA{color:var(--A)}.gB{color:var(--B)}.gC{color:var(--C)}.gD{color:var(--D)}.gF{color:var(--F)}
"""


def render_scorecard_html(sc):
    e = html.escape

    def gc(g):
        return "g" + g if g else "mut"

    parts = ["<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
             "<title>주간 성적표 %s</title><style>%s</style></head><body><main>" % (e(sc["week_start"]), _CSS),
             "<h1>주간 성적표</h1><div class=\"mut\">%s ~ %s</div>" % (e(sc["week_start"]), e(sc["week_end"])),
             "<div class=\"big %s\">%s</div>" % (gc(sc["grade"]), e(sc["grade"] or "-")),
             "<p>%s</p>" % e(sc["comment"])]
    for r in sc["results"]:
        color = "var(--%s)" % r["grade"] if r["grade"] else "var(--mut)"
        width = int(round((r["ratio"] or 0) * 100))
        parts.append(
            "<div class=\"card\"><div class=\"row\"><div class=\"g %s\">%s</div><div><strong>%s</strong>"
            "<div class=\"mut\">%s%s</div></div></div><div class=\"bar\"><i style=\"width:%d%%;background:%s\"></i></div>"
            "<div>%s</div></div>" % (gc(r["grade"]), e(r["grade"] or "-"), e(r["title"]), e(r["note"]),
                                      "" if r["final"] else " · 중간 성적", width, color, e(r["comment"])))
    if not sc["results"]:
        parts.append("<p class=\"mut\">이번 주 채점할 선언이 없어요.</p>")
    s = sc["stats"]
    parts.append("<div class=\"card mut\">일기 %d건 · 기록한 날 %d일 · 퇴근 3문답 %d일%s%s</div>" % (
        s["entries"], s["days"], s["checkouts"],
        " · 평균 기분 %s/5" % s["avg_mood"] if s["avg_mood"] is not None else "",
        " · " + e(", ".join("#%s" % t for t, _ in s["top_tags"])) if s["top_tags"] else ""))
    parts.append("</main></body></html>")
    return "".join(parts)


def export_month(journal, ym):
    """ym: 'YYYY-MM' -> 월간 Markdown 문자열."""
    try:
        y, m = [int(x) for x in ym.split("-")]
        first = date(y, m, 1)
    except (ValueError, AttributeError):
        raise ValueError("월 형식은 YYYY-MM 입니다: %r" % (ym,))
    last = date(y, m, calendar.monthrange(y, m)[1])
    entries = journal.entries(first, last)
    L = ["# %d년 %d월 기록" % (y, m), ""]
    moods = [e["mood"] for e in entries if e["mood"]]
    tags = Counter(t for e in entries for t in e["tags"])
    L.append("- 기록 %d건 / %d일" % (len(entries), len({e["day"] for e in entries})))
    if moods:
        L.append("- 평균 기분 %.1f / 5" % (sum(moods) / float(len(moods))))
    if tags:
        L.append("- 태그: " + ", ".join("#%s(%d)" % t for t in tags.most_common(10)))
    L.append("")
    by_day = {}
    for e in entries:
        by_day.setdefault(e["day"], []).append(e)
    labels = {"win": "잘한 것", "blocked": "막힌 것", "next": "내일 첫 할 일"}
    for day in sorted(by_day):
        L.append("## %s" % day)
        L.append("")
        for e in by_day[day]:
            time = e["ts"][11:16]
            if e["kind"] == "checkout":
                L.append("- %s **%s**: %s" % (time, labels.get(e["qkey"], e["qkey"]), e["text"].replace("\n", " ")))
            else:
                mood = " (기분 %d/5)" % e["mood"] if e["mood"] else ""
                body = e["text"].replace("\n", "\n  ")
                L.append("- %s%s %s" % (time, mood, body))
        L.append("")
    gl = [g for g in journal.goals() if first.isoformat() <= g["created_day"] <= last.isoformat()]
    if gl:
        L.append("## 이 달의 선언")
        L.append("")
        names = {"open": "진행 중", "done": "완료", "dropped": "접음"}
        for g in gl:
            dl = " (마감 %s)" % g["deadline"] if g["deadline"] else ""
            L.append("- [%s] %s%s" % (names.get(g["status"], g["status"]), g["title"], dl))
        L.append("")
    return "\n".join(L)
