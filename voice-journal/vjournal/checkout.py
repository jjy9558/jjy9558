# -*- coding: utf-8 -*-
"""퇴근 전 3문답 + 아침 브리핑."""
from datetime import date, datetime, timedelta

from .contract import SCHEMA_VERSION, validate
from .store import TS_FMT

KEYS = ("win", "blocked", "next")

# 키(의미)는 고정, 문구는 날짜에 따라 돌아간다.
QUESTIONS = {
    "win": ["오늘 제일 잘한 거?", "오늘 스스로 칭찬해줄 한 가지?", "오늘 뿌듯했던 순간은?"],
    "blocked": ["막힌 거?", "오늘 발목 잡은 건?", "아직 풀리지 않은 게 뭐야?"],
    "next": ["내일 첫 할 일?", "내일 눈 뜨자마자 할 한 가지?", "내일 제일 먼저 손댈 건?"],
}

LABELS = {"win": "잘한 것", "blocked": "막힌 것", "next": "내일 첫 할 일"}


def questions_for(day):
    i = day.toordinal() % 3
    return [(k, QUESTIONS[k][i]) for k in KEYS]


def save_answers(journal, answers, ts=None):
    """answers: {key: text}. 빈 답은 건너뛴다. 저장된 entry 결과 리스트."""
    unknown = set(answers) - set(KEYS)
    if unknown:
        raise ValueError("알 수 없는 질문 키: %s" % ", ".join(sorted(unknown)))
    out = []
    for k in KEYS:
        text = (answers.get(k) or "").strip()
        if text:
            out.append(journal.add_entry(text, kind="checkout", qkey=k, ts=ts))
    return out


def run_checkout(journal, ask, out=print):
    """ask(prompt)->str 로 3문답을 진행하고 저장."""
    day = journal.today()
    answers = {}
    for k, q in questions_for(day):
        try:
            answers[k] = ask("%s " % q)
        except EOFError:
            answers[k] = ""
    saved = save_answers(journal, answers)
    out("저장: %d개 답변" % len(saved))
    return saved


def morning(journal, today=None, lookback=7):
    today = today or journal.today()
    src, answers = None, {}
    for back in range(1, lookback + 1):
        d = today - timedelta(days=back)
        a = journal.checkout_answers(d)
        if a:
            src, answers = d, a
            break
    flagged = [e for e in journal.flagged(today - timedelta(days=14))]
    due = []
    for g in journal.goals("open"):
        if g["deadline"] and today.isoformat() <= g["deadline"] <= (today + timedelta(days=1)).isoformat():
            due.append(g)
    return {"date": today.isoformat(), "source_day": src.isoformat() if src else None,
            "is_yesterday": src == today - timedelta(days=1) if src else False,
            "next": answers.get("next"), "blocked": answers.get("blocked"),
            "win": answers.get("win"), "flagged": flagged, "due_goals": due}


def render_morning(m):
    lines = ["좋은 아침 (%s)" % m["date"]]
    if m["next"]:
        src = "어제" if m["is_yesterday"] else "%s 기록" % m["source_day"]
        lines.append("")
        lines.append("[%s의 '내일 첫 할 일'] %s" % (src, m["next"]))
    else:
        lines.append("")
        lines.append("[내일 첫 할 일] 기록이 없어요. 오늘 퇴근 전엔 `checkout` 해봐요.")
    if m["blocked"]:
        lines.append("[어제 막혔던 것] %s" % m["blocked"])
    if m["due_goals"]:
        lines.append("")
        lines.append("마감 임박 선언:")
        for g in m["due_goals"]:
            lines.append("  - #%d %s (마감 %s)" % (g["id"], g["title"], g["deadline"]))
    if m["flagged"]:
        lines.append("")
        lines.append("표시해둔 것:")
        for e in m["flagged"]:
            first = e["text"].splitlines()[0]
            lines.append("  - #%d [%s] %s" % (e["id"], e["day"], first[:80]))
    return "\n".join(lines)


# --- JSON (morning --json, 다른 도구 연동) ------------------------------------
# 기준일 day 의 아침 브리핑. "어제" = day - 1. 모든 텍스트는 red(Redactor) 를 거친다.

def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.1f" % x


def _progress_text(got, target, unit):
    if target:
        return "%s/%s%s" % (_num(got), _num(target), unit)
    return "%s%s" % (_num(got), unit)


def _ended_before(g, iso):
    """iso 날짜 이전에 이미 완료되었거나 접힌 목표인가."""
    if g["status"] == "done" and g["done_day"] and g["done_day"] < iso:
        return True
    return g["status"] == "dropped" and bool(g["dropped_day"]) and g["dropped_day"] < iso


def _goals_yesterday(journal, day, red):
    """어제 활성이었던 목표의 결과. 규칙:
    - 어제 접음 -> dropped / 어제 완료 -> done
    - 어제 진행 있음 -> 매일 목표는 목표치 이상이면 done, 그 밖은 partial
    - 어제 진행 없음 -> 매일 목표(기한 전)와 어제가 기한인 목표는 missed
    - 그 밖(주간 목표 무진행 등)은 제외
    """
    y = day - timedelta(days=1)
    yi = y.isoformat()
    out = []
    for g in journal.goals():
        if g["created_day"] > yi or _ended_before(g, yi):
            continue
        got = sum(a for d, a in journal.goal_events(g["id"]) if d == yi)
        target, unit = g["target"], g["unit"] or ""
        if g["status"] == "dropped" and g["dropped_day"] == yi:
            result, progress = "dropped", "접음"
        elif g["status"] == "done" and g["done_day"] == yi:
            result = "done"
            progress = _progress_text(got, target, unit) if got else "완료"
        elif got > 0:
            result = "done" if (g["period"] == "day" and got >= (target or 1)) else "partial"
            progress = _progress_text(got, target, unit)
        elif g["period"] == "day" and (g["start_day"] or g["created_day"]) <= yi \
                and (not g["deadline"] or g["deadline"] >= yi):
            result, progress = "missed", _progress_text(0, target, unit)
        elif g["period"] == "once" and g["deadline"] == yi:
            result, progress = "missed", _progress_text(0, target, unit)
        else:
            continue
        out.append({"id": g["id"], "text": red.redact(g["title"]), "result": result, "progress": progress})
    return out


def _goals_due_soon(journal, day, red):
    """열린 목표 중 마감이 오늘·내일인 것 (기존 morning 과 같은 범위)."""
    end = day + timedelta(days=1)
    out = []
    for g in journal.goals("open"):
        if g["deadline"] and day.isoformat() <= g["deadline"] <= end.isoformat():
            out.append({"id": g["id"], "text": red.redact(g["title"]), "deadline": g["deadline"],
                        "days_left": (date.fromisoformat(g["deadline"]) - day).days})
    return out


def _flags(journal, day, red):
    """표시 항목 중 최근 14일 이내, 기준일 이전(포함) 것. created 는 로컬 시각 naive ISO 8601."""
    out = []
    for e in journal.flagged(day - timedelta(days=14)):
        if e["day"] > day.isoformat():
            continue
        out.append({"id": e["id"], "text": red.redact(e["text"]),
                    "created": datetime.strptime(e["ts"], TS_FMT).isoformat()})
    return out


def morning_payload(journal, day, red):
    """`morning --json` 의 dict. 스키마 검증을 거쳐서 돌려준다."""
    nxt = journal.checkout_answers(day - timedelta(days=1)).get("next")
    first = red.redact(nxt.strip()) if nxt and nxt.strip() else None
    return validate({
        "version": SCHEMA_VERSION,
        "date": day.isoformat(),
        "first_task": first,
        "goals_yesterday": _goals_yesterday(journal, day, red),
        "goals_due_soon": _goals_due_soon(journal, day, red),
        "flags": _flags(journal, day, red),
    })
