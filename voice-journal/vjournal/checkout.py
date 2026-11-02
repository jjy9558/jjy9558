# -*- coding: utf-8 -*-
"""퇴근 전 3문답 + 아침 브리핑."""
from datetime import timedelta

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
