"""합성: 어댑터 항목을 우선순위로 줄 세우고, 말할 길이 예산에 맞춰 낭독문(speak_text)을 만든다."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Context, Item
from .speech import ko_date, speechify, shorten

SCHEMA_VERSION = 1
EMPTY_SPEECH = "오늘은 특별히 챙길 일이 없습니다."
_END = re.compile(r"[.!?…。]$")


@dataclass
class Composed:
    brief: dict
    cards: list
    ranked: list = field(default_factory=list)       # [(Item, 실효 우선순위)] 우선순위 순
    spoken: list = field(default_factory=list)       # [(Item, 낭독 문장)] 낭독문에 들어간 것
    budget_chars: int = 0


def is_quiet_day(cfg: dict, ctx: Context) -> bool:
    return ctx.today.weekday() in (cfg.get("quiet_days") or {}).get("weekdays", [])


def effective(item: Item, cfg: dict, quiet: bool) -> float:
    w = float((cfg.get("weights") or {}).get(item.source, 1.0))
    if quiet:
        w *= float(((cfg.get("quiet_days") or {}).get("weights") or {}).get(item.source, 1.0))
    return round(item.priority * w, 1)


def due_key(due: str | None) -> tuple:
    if not due:
        return (1, "")
    s = str(due)
    return (0, s[:16] if "T" in s else s[:10] + "T00:00")


def _sentence(text: str) -> str:
    t = speechify(text)
    return t if not t or _END.search(t) else t + "."


def budget_chars(cfg: dict, seconds: float | None = None, cap: bool = True) -> int:
    sp = cfg["speak"]
    sec = float(sp["seconds"] if seconds is None else seconds)
    n = int(sec * float(sp["chars_per_sec"]))
    return max(20, min(n, int(sp["max_chars"])) if cap else n)


def _tail(n: int) -> str:
    return "그 밖에 %d건은 화면에서 확인하세요." % n


def build_speech(ranked: list[tuple[Item, float]], today, budget: int, intro: bool) -> tuple[str, list, int]:
    """(낭독문, [(항목, 문장)], 못 말한 항목 수). 항상 len(낭독문) <= budget.

    예산이 빠듯하면 가장 급한 항목을 온전히 살리려고 인사말, 꼬리말('그 밖에 N건') 순으로 뺀다."""
    greeting = "좋은 아침입니다. %s." % ko_date(today)
    pool = [(it, p) for it, p in ranked if it.speak]

    def attempt(head: str, reserve: int):
        total = len(head)
        lines: list[tuple[Item, str]] = []
        cut = False       # 가장 급한 항목을 잘라서 넣었는가
        for it, _ in pool:
            room = budget - reserve - total - 1
            cands = [_sentence(it.speak)]
            if it.speak_short:
                cands.append(_sentence(it.speak_short))
            if not lines:      # 가장 급한 항목은 길어도 잘라서라도 말한다
                cands.append(_sentence(shorten(cands[0].rstrip("."), max(room - 1, 10))))
            for n, c in enumerate(cands):
                if c and len(c) <= room:
                    cut = cut or (not lines and n == len(cands) - 1)
                    lines.append((it, c))
                    total += len(c) + 1
                    break
        return lines, len(pool) - len(lines), cut

    def plan(head: str, with_tail: bool):
        lines, dropped, cut = attempt(head, 0)
        tail = ""
        if dropped and with_tail:
            guess = dropped
            for _ in range(5):   # 건수 자릿수가 바뀌면 꼬리 길이도 바뀌므로 맞을 때까지 되풀이
                lines, dropped, cut = attempt(head, len(_tail(guess)) + 1)
                if dropped == guess:
                    break
                guess = dropped
            tail = _tail(dropped) if dropped else ""
        return head, lines, dropped, tail, cut

    options = [(greeting, True), ("", True), ("", False)] if intro else [("", True), ("", False)]
    for head, with_tail in options:
        head, lines, dropped, tail, cut = plan(head, with_tail)
        if (lines and not cut) or not pool:
            break
    parts = [x for x in [head] + [c for _, c in lines] + [tail] if x]
    if not lines:
        parts.append(EMPTY_SPEECH)
    text = " ".join(parts)
    if len(text) > budget:   # 최후의 안전장치
        text = shorten(text, budget)
    return text, lines, dropped


def compose(items: list[Item], statuses: dict, titles: dict, cfg: dict, ctx: Context, *,
            seconds: float | None = None, cap: bool = True) -> Composed:
    quiet = is_quiet_day(cfg, ctx)
    order = {name: i for i, name in enumerate(statuses)}
    ranked = sorted(((it, effective(it, cfg, quiet)) for it in items),
                    key=lambda t: (-t[1], due_key(t[0].due), order.get(t[0].source, 99), t[0].title))
    budget = budget_chars(cfg, seconds, cap)
    text, spoken, dropped = build_speech(ranked, ctx.today, budget, bool(cfg["speak"].get("intro", True)))
    spoken_ids = {id(it) for it, _ in spoken}

    sections: dict[str, dict] = {}
    for it, p in ranked:
        sec = sections.setdefault(it.source, {"id": it.source, "title": it.section or titles.get(it.source, it.source),
                                              "items": []})
        sec["items"].append({"source": it.source, "kind": it.kind, "text": it.title, "detail": it.detail,
                             "priority": p, "due": it.due, "spoken": id(it) in spoken_ids})
    brief = {
        "version": SCHEMA_VERSION,
        "date": ctx.today.isoformat(),
        "quiet_day": quiet,
        "speak_text": text,
        "reject_text": str(cfg["reject_text"]),
        "speak_budget_chars": budget,
        "dropped": dropped,
        "sections": list(sections.values()),
        "sources": statuses,
    }
    cards = [{"kind": it.kind, "title": it.title, "detail": it.detail, "due": it.due, "actions": list(it.actions)}
             for it, p in sorted(((i, p) for i, p in ranked if i.card),
                                 key=lambda t: (due_key(t[0].due), -t[1], t[0].title))]
    return Composed(brief, cards, ranked, spoken, budget)
