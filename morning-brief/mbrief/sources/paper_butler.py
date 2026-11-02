"""paper-butler 어댑터: sqlite(mode=ro)에서 납부 기한(documents.due_date)과 환불·반품 기한(refunds.deadline).

형식(paper-butler/paperbutler/storage.py): documents(id, doc_type, subtype, issuer, merchant, amount,
due_date 'YYYY-MM-DD', auto_debit, done), refunds(id, doc_id, merchant, amount, deadline, done, note).
DB 는 PAPERBUTLER_HOME(기본 ~/.paperbutler)/paperbutler.db. Database 클래스는 열 때 스키마를 쓰므로
쓰지 않고 SELECT 만 직접 한다.
"""
from __future__ import annotations

import datetime as dt

from ..model import Context, Item
from ..speech import when_phrase, won_text, ko_won
from ..sqlro import open_ro

NAME = "paper_butler"
SECTION = "납부·기한"
DEFAULTS = {"enabled": True, "db": "", "lookahead_days": 14, "overdue_days": 7, "refund_days": 14}

SUBTYPE_LABEL = {
    "전기": "전기요금", "가스": "가스요금", "수도": "수도요금", "통신": "통신요금", "관리비": "관리비",
    "건강보험": "건강보험료", "국민연금": "국민연금보험료", "자동차세": "자동차세", "재산세": "재산세",
    "주민세": "주민세", "과태료": "과태료", "카드대금": "카드대금", "기타": "고지서",
}


def _prio(days: int) -> float:
    if days < 0:
        return 92
    return {0: 90, 1: 80, 2: 72, 3: 66}.get(days, 45 if days <= 7 else 25)


def _day(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def collect(cfg: dict, ctx: Context) -> list[Item]:
    path = ctx.path(cfg["db"]) if cfg.get("db") else \
        ctx.home_path("PAPERBUTLER_HOME", "~/.paperbutler") / "paperbutler.db"
    today = ctx.today
    start = (today - dt.timedelta(days=int(cfg["overdue_days"]))).isoformat()
    end = (today + dt.timedelta(days=int(cfg["lookahead_days"]))).isoformat()
    r_end = (today + dt.timedelta(days=int(cfg["refund_days"]))).isoformat()
    items: list[Item] = []
    with open_ro(path, "paper-butler DB") as con:
        docs = con.execute(
            "SELECT id, doc_type, subtype, issuer, merchant, amount, due_date, auto_debit FROM documents "
            "WHERE due_date IS NOT NULL AND due_date BETWEEN ? AND ? AND done=0 ORDER BY due_date, id",
            (start, end)).fetchall()
        refunds = con.execute(
            "SELECT id, doc_id, merchant, amount, deadline FROM refunds "
            "WHERE deadline BETWEEN ? AND ? AND done=0 ORDER BY deadline, id",
            (today.isoformat(), r_end)).fetchall()
    for r in docs:
        due = _day(r["due_date"])
        if due is None:
            continue
        days = (due - today).days
        label = SUBTYPE_LABEL.get(r["subtype"] or "", r["subtype"] or "") or "납부"
        who = ctx.red(r["issuer"] or r["merchant"] or "").strip()
        name = (who + " " + label).strip() if label != "납부" else (who or "납부")
        auto = bool(r["auto_debit"])
        word = when_phrase(due, today)
        amount = r["amount"]
        speak = "납부: %s %s%s" % (name, word, ", " + ko_won(amount) if amount else "")
        if auto:
            speak += ", 자동이체"
        short = "납부 %s %s" % (name, word)
        prio = _prio(days) - (15 if auto else 0)
        title = "%s %s%s" % (name, won_text(amount) if amount is not None else "", " (자동이체)" if auto else "")
        actions = ("완료 처리: python -m paperbutler done %d" % r["id"],)
        items.append(Item(NAME, "bill", title.strip(), "납부기한 %s (%s)" % (due.isoformat(), word), prio,
                          speak, short, due.isoformat(), actions))
    for r in refunds:
        due = _day(r["deadline"])
        if due is None:
            continue
        days = (due - today).days
        who = ctx.red(r["merchant"] or "상점").strip()
        word = when_phrase(due, today)
        amount = r["amount"]
        speak = "환불 반품 기한: %s %s까지%s" % (who, word, ", " + ko_won(amount) if amount else "")
        items.append(Item(NAME, "refund", "%s 환불·반품 %s" % (who, won_text(amount) if amount is not None else ""),
                          "기한 %s (%s). 정책상 기한이니 판매처에서 확인하세요." % (due.isoformat(), word),
                          _prio(days) - 2, speak, "환불 반품 %s %s까지" % (who, word), due.isoformat(),
                          ("완료 처리: python -m paperbutler done %d" % r["doc_id"],)))
    return items
