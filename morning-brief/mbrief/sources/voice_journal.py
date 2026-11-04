"""voice-journal 어댑터: sqlite(mode=ro)에서 어제 선언 결과, 어제 퇴근 3문답의 '내일 첫 할 일', 마감 임박 선언, 표시해 둔 메모.

형식(voice-journal/vjournal/store.py): entries(day, text, kind 'diary'|'checkout', qkey 'win'|'blocked'|'next',
flagged), goals(title, period 'once'|'day'|'week', target, deadline, start_day, status 'open'|'done'|'dropped',
done_day, dropped_day, created_day), goal_events(goal_id, day, amount). DB 는 VJOURNAL_DB(기본 ~/.vjournal/journal.db).
야간 Claude 3줄(claude_ingest)은 DB 에 저장되지 않으므로 claude_night 어댑터가 따로 만든다.
"""
from __future__ import annotations

import datetime as dt

from ..model import Context, Item
from ..speech import shorten, when_phrase
from ..sqlro import open_ro
from . import _contract

NAME = "voice_journal"
SECTION = "어제와 오늘 할 일"
DEFAULTS = {"enabled": True, "db": "", "flag_days": 3, "checkout_lookback": 7, "max_flags": 3,
            **_contract.JSON_DEFAULTS}


def _num(x) -> str:
    x = float(x)
    return str(int(x)) if x == int(x) else ("%g" % x)


def yesterday_results(con, yday: dt.date) -> list[dict]:
    """어제 판정이 나는 선언들: [{title, ok, progress, target, unit}]."""
    y = yday.isoformat()
    week_start = (yday - dt.timedelta(days=yday.weekday())).isoformat()
    out = []
    for g in con.execute("SELECT * FROM goals ORDER BY id").fetchall():
        status, dropped = g["status"], g["dropped_day"]
        if status == "dropped" and (dropped or "") <= y:
            continue
        if g["created_day"] > y:
            continue
        period, target = g["period"], g["target"]
        if period == "once":
            if g["deadline"] != y:
                continue
            ok = status == "done"
            out.append({"title": g["title"], "ok": ok, "progress": None, "target": None, "unit": g["unit"]})
            continue
        if period == "day":
            if (g["start_day"] and g["start_day"] > y) or (g["deadline"] and g["deadline"] < y):
                continue
            lo, hi = y, y
        elif period == "week":
            if yday.weekday() != 6 or (g["deadline"] and g["deadline"] < week_start):
                continue
            lo, hi = week_start, y
        else:
            continue
        got = con.execute("SELECT COALESCE(SUM(amount),0) FROM goal_events WHERE goal_id=? AND day BETWEEN ? AND ?",
                          (g["id"], lo, hi)).fetchone()[0]
        need = target or 1
        out.append({"title": g["title"], "ok": got >= need, "progress": got, "target": need, "unit": g["unit"]})
    return out


def collect(cfg: dict, ctx: Context) -> list[Item]:
    return _contract.collect_with_mode(NAME, cfg, ctx, lambda: _collect_json(cfg, ctx),
                                       lambda: _collect_direct(cfg, ctx))


def _collect_json(cfg: dict, ctx: Context) -> list[Item]:
    """`vjournal morning --json --date D` (schema v1). 퇴근 3문답의 '막힌 것'은 계약에 없어 이 방식에서는 빠진다."""
    today = ctx.today
    env = {"VJOURNAL_DB": str(ctx.path(cfg["db"]))} if cfg.get("db") else {}
    obj = _contract.run_json(cfg, ctx, project="voice-journal", pkg="vjournal",
                             args=["morning", "--json", "--date", today.isoformat()], env_extra=env, date_key="date")
    results = []
    for g in obj["goals_yesterday"]:
        if g["result"] == "dropped":                        # 직접 읽기와 같게: 접은 선언은 판정 대상이 아님
            continue
        prog = str(g["progress"] or "")
        results.append({"title": str(g["text"]), "ok": g["result"] == "done",
                        "extra": " (%s)" % prog if prog and g["result"] != "done" else ""})
    first = obj["first_task"]
    first_task = (str(first), (today - dt.timedelta(days=1)).isoformat()) if first else None
    due = [(str(g["text"]), dt.date.fromisoformat(g["deadline"])) for g in obj["goals_due_soon"]
           if 0 <= int(g["days_left"]) <= 1]
    cutoff = (today - dt.timedelta(days=int(cfg["flag_days"]))).isoformat()
    flags = sorted(((str(f["created"])[:10], str(f["created"]), str(f["text"])) for f in obj["flags"]
                    if str(f["created"])[:10] >= cutoff), key=lambda t: t[1], reverse=True)
    flags = [(d, text) for d, _, text in flags[:int(cfg["max_flags"])]]
    return _build(ctx, results, first_task, None, due, flags)


def _collect_direct(cfg: dict, ctx: Context) -> list[Item]:
    path = ctx.path(cfg["db"]) if cfg.get("db") else ctx.home_path("VJOURNAL_DB", "~/.vjournal/journal.db")
    today = ctx.today
    yday = today - dt.timedelta(days=1)
    with open_ro(path, "voice-journal DB") as con:
        raw = yesterday_results(con, yday)
        answers, src_day = {}, None
        for back in range(1, int(cfg["checkout_lookback"]) + 1):
            d = (today - dt.timedelta(days=back)).isoformat()
            rows = con.execute("SELECT qkey, text FROM entries WHERE kind='checkout' AND day=? ORDER BY ts, id",
                               (d,)).fetchall()
            if rows:
                answers, src_day = {r["qkey"]: r["text"] for r in rows}, d
                break
        due_goals = con.execute(
            "SELECT id, title, deadline FROM goals WHERE status='open' AND deadline BETWEEN ? AND ? ORDER BY deadline, id",
            (today.isoformat(), (today + dt.timedelta(days=1)).isoformat())).fetchall()
        flags = con.execute(
            "SELECT id, day, text FROM entries WHERE flagged=1 AND day>=? ORDER BY ts DESC LIMIT ?",
            ((today - dt.timedelta(days=int(cfg["flag_days"]))).isoformat(), int(cfg["max_flags"]))).fetchall()
    results = [{"title": r["title"], "ok": r["ok"],
                "extra": "" if r["progress"] is None else " (%s/%s)" % (_num(r["progress"]), _num(r["target"]))}
               for r in raw]
    first_task = (answers["next"], src_day) if answers.get("next") else None
    due = [(g["title"], dt.date.fromisoformat(g["deadline"])) for g in due_goals]
    return _build(ctx, results, first_task, answers.get("blocked"), due, [(f["day"], f["text"]) for f in flags])


def _build(ctx: Context, results: list[dict], first_task, blocked, due_goals, flags) -> list[Item]:
    """두 방식이 같은 모양으로 넘기는 값으로 항목을 만든다.

    results [{title, ok, extra}], first_task (text, 기록한 날) | None, blocked 텍스트 | None,
    due_goals [(title, 마감일)], flags [(날짜, text)].
    """
    today = ctx.today
    yday = today - dt.timedelta(days=1)
    items: list[Item] = []

    missed = [r for r in results if not r["ok"]]
    if results:
        if missed:
            names = ", ".join("'%s'" % shorten(ctx.red(r["title"]), 24) for r in missed[:2])
            speak = ("어제 선언 %s 미달." % names) if len(results) == 1 else \
                ("어제 선언 %d개 중 %d개 미달: %s." % (len(results), len(missed), names))
            lines = []
            for r in results:
                mark = "달성" if r["ok"] else "미달"
                lines.append("- [%s] %s%s" % (mark, ctx.red(r["title"]), r["extra"]))
            items.append(Item(NAME, "declaration", "어제 선언 %d/%d 미달" % (len(missed), len(results)),
                              "\n".join(lines), 55 + min(len(missed), 3) * 2, speak,
                              "어제 선언 %d개 미달." % len(missed), None, ("오늘 만회하기",)))
        else:
            items.append(Item(NAME, "declaration", "어제 선언 %d개 모두 달성" % len(results),
                              "\n".join("- [달성] " + ctx.red(r["title"]) for r in results), 18,
                              "어제 선언 %d개 모두 달성." % len(results), "", None, (), card=False))
    if first_task and first_task[0]:
        src_day = first_task[1]
        yesterday = src_day == yday.isoformat()
        text = shorten(ctx.red(first_task[0]).splitlines()[0], 80)
        items.append(Item(NAME, "first-task", "내일 첫 할 일(%s 기록)" % src_day, text,
                          58 if yesterday else 44, "오늘 첫 할 일: " + shorten(text, 50),
                          "첫 할 일: " + shorten(text, 24), today.isoformat(), ("오늘 가장 먼저 하기",)))
    if blocked:
        text = shorten(ctx.red(blocked).splitlines()[0], 80)
        items.append(Item(NAME, "blocked", "어제 막혔던 것", text, 30,
                          "어제 막혔던 것: " + shorten(text, 40), "", None, ()))
    for g_title, d in due_goals:
        left = (d - today).days
        title = shorten(ctx.red(g_title), 40)
        items.append(Item(NAME, "goal-due", "선언 마감 %s: %s" % (when_phrase(d, today), title),
                          "마감 %s" % d.isoformat(), 66 if left == 0 else 52,
                          "선언 마감 %s: %s" % (when_phrase(d, today), title), "선언 마감 %s" % when_phrase(d, today),
                          d.isoformat(), ()))
    for f_day, f_text in flags:
        first = shorten(ctx.red(f_text).splitlines()[0], 60)
        items.append(Item(NAME, "flag", "표시해 둔 메모 (%s)" % f_day, first, 32,
                          "표시해 둔 메모: " + shorten(first, 34), "", None, ()))
    return items
