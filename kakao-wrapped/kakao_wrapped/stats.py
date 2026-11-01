"""Compute the year-end statistics from parsed messages."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import textutil
from .parser import (
    DELETED,
    EMOTICON,
    FILE,
    MEDIA_KINDS,
    PHOTO,
    SYSTEM,
    TEXT,
    VIDEO,
    VOICE,
    Message,
)

REPLY_MAX_GAP = 3600  # seconds; longer gaps are not "replies"
STARTER_GAP = 6 * 3600  # seconds of silence before a message counts as a 선톡
BURST_GAP = 180  # seconds; messages closer than this belong to the same burst
NIGHT_HOURS = range(0, 6)  # 0:00 - 5:59
QUOTE_MAX_CHARS = 80
ANON_PREFIX = "멤버"


# ---------------------------------------------------------------------------
# anonymisation
# ---------------------------------------------------------------------------

def anonymize_messages(messages: List[Message]) -> Tuple[List[Message], Dict[str, str]]:
    """Replace names by 멤버1..N (ordered by message count, then first appearance)."""
    counts: Counter = Counter()
    first: Dict[str, int] = {}
    for i, m in enumerate(messages):
        if m.kind == SYSTEM or not m.user:
            continue
        counts[m.user] += 1
        first.setdefault(m.user, i)
    order = sorted(counts, key=lambda u: (-counts[u], first[u]))
    mapping = {u: "%s%d" % (ANON_PREFIX, i + 1) for i, u in enumerate(order)}
    out = [
        Message(m.ts, mapping.get(m.user, m.user), m.text, m.kind, m.count)
        for m in messages
    ]
    return out, mapping


def _name_aliases(mapping: Dict[str, str]) -> List[str]:
    """Strings that identify members; used to keep names out of word lists."""
    import re

    aliases = set()
    for name in mapping:
        aliases.add(name.strip())
        for part in re.split(r"[\s/()\[\]_\-.,]+", name):
            if part:
                aliases.add(part)
    return sorted(aliases, key=len, reverse=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _round(x: float, n: int = 2) -> float:
    return round(float(x), n)


def _share(part: int, total: int) -> float:
    return _round(100.0 * part / total, 1) if total else 0.0


def _winner(items: List[Dict[str, Any]], key: str, reverse: bool = True) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    return sorted(items, key=lambda d: (-d[key] if reverse else d[key], d["name"]))[0]


def _ranking(items: List[Dict[str, Any]], key: str, top: int, reverse: bool = True,
             tie: Optional[str] = None) -> List[Dict[str, Any]]:
    sign = -1 if reverse else 1

    def sort_key(d: Dict[str, Any]) -> Any:
        return (sign * d[key], sign * (d[tie] or 0) if tie else 0, d["name"])

    return sorted(items, key=sort_key)[:top]


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M")


def _quote(text: str, include: bool) -> Optional[str]:
    if not include:
        return None
    t = textutil.redact(" ".join(text.split()))
    if len(t) > QUOTE_MAX_CHARS:
        t = t[: QUOTE_MAX_CHARS - 1].rstrip() + "…"
    return t


def _describe(m: Message, include_quote: bool) -> Dict[str, Any]:
    label = {
        PHOTO: "(사진)", VIDEO: "(동영상)", EMOTICON: "(이모티콘)",
        FILE: "(파일)", VOICE: "(음성메시지)", DELETED: "(삭제된 메시지)",
    }.get(m.kind)
    return {
        "ts": _iso(m.ts),
        "name": m.user,
        "kind": m.kind,
        "text": label if label else _quote(m.text, include_quote),
    }


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------

def available_years(messages: List[Message]) -> List[int]:
    return sorted({m.ts.year for m in messages if m.kind != SYSTEM})


def compute_stats(
    messages: List[Message],
    year: int,
    top: int = 5,
    anonymize: bool = False,
    title: Optional[str] = None,
    fmt: Optional[str] = None,
    min_samples: int = 5,
    lurk_min_others: int = 20,
) -> Dict[str, Any]:
    """Compute every statistic for ``year``. Returns a JSON-serialisable dict."""
    msgs = [m for m in messages if m.kind != SYSTEM and m.user and m.ts.year == year]
    msgs.sort(key=lambda m: m.ts)  # stable: keeps file order for equal timestamps
    mapping: Dict[str, str] = {}
    if anonymize:
        msgs, mapping = anonymize_messages(msgs)
        title = None
    # In anonymise mode the *original* names must never show up in word lists.
    drop_names = _name_aliases(mapping) if anonymize else []
    include_quotes = not anonymize

    total = len(msgs)
    result: Dict[str, Any] = {
        "meta": {
            "year": year,
            "title": title,
            "format": fmt,
            "anonymized": anonymize,
            "top": top,
        },
        "empty": total == 0,
    }
    if total == 0:
        return result

    # ---- per member accumulators -----------------------------------------
    mem: Dict[str, Dict[str, Any]] = {}

    def M(name: str) -> Dict[str, Any]:
        if name not in mem:
            mem[name] = {
                "name": name, "messages": 0, "text_messages": 0, "photos": 0, "videos": 0,
                "emoticons": 0, "files": 0, "voice": 0, "deleted": 0, "chars": 0,
                "kkk_total": 0, "kkk_longest": 0, "cry_total": 0,
                "night_msgs": 0, "links": 0, "starts": 0,
            }
        return mem[name]

    heat = [[0] * 24 for _ in range(7)]
    hours = [0] * 24
    months = [0] * 13  # index 1..12
    weekdays = [0] * 7
    per_day: Counter = Counter()
    per_day_person: Dict[date, Counter] = defaultdict(Counter)
    words_all: Counter = Counter()
    words_by: Dict[str, Counter] = defaultdict(Counter)
    domains: Counter = Counter()
    link_total = 0

    for m in msgs:
        d = M(m.user)
        d["messages"] += 1
        h = m.ts.hour
        wd = m.ts.weekday()
        heat[wd][h] += 1
        hours[h] += 1
        months[m.ts.month] += 1
        weekdays[wd] += 1
        per_day[m.ts.date()] += 1
        per_day_person[m.ts.date()][m.user] += 1
        if h in NIGHT_HOURS:
            d["night_msgs"] += 1
        k = m.kind
        if k == PHOTO:
            d["photos"] += m.count
        elif k == VIDEO:
            d["videos"] += m.count
        elif k == EMOTICON:
            d["emoticons"] += m.count
        elif k == FILE:
            d["files"] += 1
        elif k == VOICE:
            d["voice"] += 1
        elif k == DELETED:
            d["deleted"] += 1
        else:
            d["text_messages"] += 1
            t = m.text
            d["chars"] += len(t)
            kk = t.count("ㅋ")
            d["kkk_total"] += kk
            d["cry_total"] += t.count("ㅠ") + t.count("ㅜ")
            run = textutil.longest_run(t, "ㅋ")
            if run > d["kkk_longest"]:
                d["kkk_longest"] = run
            doms = textutil.extract_domains(t)
            if doms:
                d["links"] += len(doms)
                link_total += len(doms)
                domains.update(doms)
            toks = textutil.tokenize(t, drop_names)
            words_all.update(toks)
            words_by[m.user].update(toks)

    # ---- time based: replies, starters, lurking ----------------------------
    replies: Dict[str, List[float]] = defaultdict(list)
    for prev, cur in zip(msgs, msgs[1:]):
        if prev.user != cur.user:
            gap = (cur.ts - prev.ts).total_seconds()
            if 0 <= gap <= REPLY_MAX_GAP:
                replies[cur.user].append(gap)
    starters: Counter = Counter()
    for i, m in enumerate(msgs):
        if i == 0 or (m.ts - msgs[i - 1].ts).total_seconds() > STARTER_GAP:
            starters[m.user] += 1
            M(m.user)["starts"] += 1

    # ---- finalise members -----------------------------------------------------
    members: List[Dict[str, Any]] = []
    for name, d in mem.items():
        tm = d["text_messages"]
        d["share"] = _share(d["messages"], total)
        d["avg_len"] = _round(d["chars"] / tm, 1) if tm else 0.0
        d["kkk_per_msg"] = _round(d["kkk_total"] / tm, 2) if tm else 0.0
        d["cry_per_msg"] = _round(d["cry_total"] / tm, 2) if tm else 0.0
        d["night_ratio"] = _share(d["night_msgs"], d["messages"])
        rs = replies.get(name, [])
        d["reply_count"] = len(rs)
        d["median_reply_sec"] = _round(statistics.median(rs), 1) if rs else None
        d["mean_reply_sec"] = _round(statistics.mean(rs), 1) if rs else None
        d["top_words"] = [[w, c] for w, c in words_by[name].most_common(top)]
        members.append(d)
    members.sort(key=lambda d: (-d["messages"], d["name"]))
    result["members"] = members

    # ---- totals --------------------------------------------------------------
    dates_sorted = sorted(per_day)
    first_d, last_d = dates_sorted[0], dates_sorted[-1]
    span_days = (last_d - first_d).days + 1
    result["totals"] = {
        "messages": total,
        "members": len(members),
        "text_messages": sum(d["text_messages"] for d in members),
        "photos": sum(d["photos"] for d in members),
        "videos": sum(d["videos"] for d in members),
        "emoticons": sum(d["emoticons"] for d in members),
        "files": sum(d["files"] for d in members),
        "voice": sum(d["voice"] for d in members),
        "deleted": sum(d["deleted"] for d in members),
        "chars": sum(d["chars"] for d in members),
        "active_days": len(per_day),
        "span_days": span_days,
        "avg_per_active_day": _round(total / len(per_day), 1),
        "first_day": first_d.isoformat(),
        "last_day": last_d.isoformat(),
    }

    # ---- busiest day / month ------------------------------------------------------
    bd, bc = sorted(per_day.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    bd_person = sorted(per_day_person[bd].items(), key=lambda kv: (-kv[1], kv[0]))[0]
    result["busiest_day"] = {
        "date": bd.isoformat(), "count": bc,
        "weekday": bd.weekday(), "top_name": bd_person[0], "top_count": bd_person[1],
    }
    bm = max(range(1, 13), key=lambda mo: (months[mo], -mo))
    result["busiest_month"] = {"month": bm, "count": months[bm]}
    result["months"] = months[1:]
    result["weekdays"] = weekdays
    result["hours"] = hours
    result["heatmap"] = heat
    peak = max(range(24), key=lambda h: (hours[h], -h))
    result["peak_hour"] = peak
    # longest streak of consecutive active days
    best = (0, None, None)  # length, start, end
    run_start = prev_d = None
    for d_ in dates_sorted:
        if prev_d is not None and d_ - prev_d == timedelta(days=1):
            pass
        else:
            run_start = d_
        prev_d = d_
        length = (d_ - run_start).days + 1
        if length > best[0]:
            best = (length, run_start, d_)
    result["streak"] = {
        "days": best[0], "start": best[1].isoformat(), "end": best[2].isoformat()
    }

    # ---- awards ------------------------------------------------------------------------
    awards: Dict[str, Any] = {}

    def rank_rows(key: str, extra: Optional[List[str]] = None, reverse: bool = True,
                  pool: Optional[List[Dict[str, Any]]] = None, min_value: float = 0,
                  tie: Optional[str] = None) -> List[Dict[str, Any]]:
        pool = members if pool is None else pool
        pool = [d for d in pool if d[key] is not None and (d[key] > min_value if reverse else True)]
        rows = []
        for d in _ranking(pool, key, top, reverse, tie):
            row = {"name": d["name"], "value": d[key]}
            for e in extra or []:
                row[e] = d[e]
            rows.append(row)
        return rows

    # 새벽형 인간
    rows = rank_rows("night_msgs", ["night_ratio", "messages"])
    awards["night_owl"] = {"winner": rows[0], "ranking": rows} if rows else None

    # 칼답왕 / 느긋왕
    pool = [d for d in members if d["reply_count"] >= min_samples]
    if pool:
        fast = rank_rows("median_reply_sec", ["reply_count"], reverse=False, pool=pool, tie="mean_reply_sec")
        slow = rank_rows("median_reply_sec", ["reply_count"], reverse=True, pool=pool, tie="mean_reply_sec")
        awards["fast_replier"] = {"winner": fast[0], "ranking": fast}
        awards["slow_replier"] = {"winner": slow[0], "ranking": slow} if len(pool) > 1 else None
    else:
        awards["fast_replier"] = awards["slow_replier"] = None

    # 읽씹/잠수왕
    awards["lurker"] = _lurker(msgs, lurk_min_others, include_quotes)

    # 이모티콘 / 사진 왕
    rows = rank_rows("emoticons", ["messages"])
    awards["emoticon_king"] = {"winner": rows[0], "ranking": rows} if rows else None
    rows = rank_rows("photos", ["videos"])
    awards["photo_king"] = {"winner": rows[0], "ranking": rows} if rows else None

    # ㅋㅋ 지수
    pool = [d for d in members if d["text_messages"] >= min_samples and d["kkk_total"] > 0]
    if pool:
        rows = rank_rows("kkk_per_msg", ["kkk_total", "text_messages"], pool=pool)
        awards["kkk_king"] = {"winner": rows[0], "ranking": rows}
    else:
        awards["kkk_king"] = None
    kl = [d for d in members if d["kkk_longest"] > 0]
    if kl:
        w = sorted(kl, key=lambda d: (-d["kkk_longest"], d["name"]))[0]
        awards["kkk_streak"] = {"name": w["name"], "length": w["kkk_longest"]}
    else:
        awards["kkk_streak"] = None
    awards["kkk_group_avg"] = _round(
        sum(d["kkk_total"] for d in members) / max(1, sum(d["text_messages"] for d in members)), 2
    )

    # 눈물 지수 (ㅠ/ㅜ)
    pool = [d for d in members if d["text_messages"] >= min_samples and d["cry_total"] > 0]
    if pool:
        rows = rank_rows("cry_per_msg", ["cry_total"], pool=pool)
        awards["cry_king"] = {"winner": rows[0], "ranking": rows}
    else:
        awards["cry_king"] = None

    # 선톡왕
    rows = rank_rows("starts")
    awards["starter_king"] = {"winner": rows[0], "ranking": rows} if rows else None

    # 장문왕
    pool = [d for d in members if d["text_messages"] >= min_samples]
    if pool:
        rows = rank_rows("avg_len", ["text_messages"], pool=pool)
        awards["long_writer"] = {"winner": rows[0], "ranking": rows} if rows else None
    else:
        awards["long_writer"] = None

    result["awards"] = awards

    # ---- words -----------------------------------------------------------------------------------
    result["top_words"] = [[w, c] for w, c in words_all.most_common(max(top * 2, 10))]

    # ---- links ------------------------------------------------------------------------------------
    sharers = [d for d in members if d["links"] > 0]
    sharer = _winner(sharers, "links")
    result["links"] = {
        "total": link_total,
        "domains": [[dm, c] for dm, c in domains.most_common(max(top, 5))],
        "top_sharer": {"name": sharer["name"], "count": sharer["links"]} if sharer else None,
    }

    # ---- first / last / quote ----------------------------------------------------------------------
    result["first_message"] = _describe(msgs[0], include_quotes)
    result["last_message"] = _describe(msgs[-1], include_quotes)
    result["quote_of_year"] = _quote_of_year(msgs, include_quotes)

    return result


# ---------------------------------------------------------------------------
# lurker + quote
# ---------------------------------------------------------------------------

def _lurker(msgs: List[Message], min_others: int, include_quotes: bool) -> Optional[Dict[str, Any]]:
    """Longest gap between two messages of the same person while others kept chatting.

    A gap counts only when at least ``min_others`` messages by other people were
    sent during it. The silence after a member's last message (until the last
    message of the year) is also considered ("ongoing").
    """
    last_idx: Dict[str, int] = {}
    best: Optional[Dict[str, Any]] = None
    n = len(msgs)

    def consider(name: str, i: int, j: int, ongoing: bool) -> None:
        nonlocal best
        # messages strictly between i and j are all by others; for an ongoing
        # silence msgs[j] (the final message) is by someone else as well.
        others = j - i if ongoing else j - i - 1
        if others < min_others:
            return
        secs = (msgs[j].ts - msgs[i].ts).total_seconds()
        if best is None or secs > best["seconds"] or (
            secs == best["seconds"] and name < best["name"]
        ):
            best = {
                "name": name, "seconds": secs, "hours": _round(secs / 3600, 1),
                "days": _round(secs / 86400, 1),
                "from": _iso(msgs[i].ts), "to": _iso(msgs[j].ts),
                "others_messages": others, "ongoing": ongoing,
            }

    for j, m in enumerate(msgs):
        if m.user in last_idx:
            consider(m.user, last_idx[m.user], j, False)
        last_idx[m.user] = j
    for name, i in last_idx.items():
        if i < n - 1:
            consider(name, i, n - 1, True)
    return best


def _quote_of_year(msgs: List[Message], include_quote: bool) -> Optional[Dict[str, Any]]:
    """올해의 한마디 = the message after which the conversation kept going the longest.

    Messages are split into bursts (gap between consecutive messages <= 3 minutes).
    For each text message we count how many messages *by other people* follow it
    inside the same burst; the highest count wins (ties: more distinct people
    replying, then the earlier message). Candidates must contain real words
    (not only ㅋㅋ / links) and be at most 100 characters long.
    """
    n = len(msgs)
    best = None  # (others, distinct, -index)
    best_i = -1
    i = 0
    while i < n:
        j = i + 1
        while j < n and (msgs[j].ts - msgs[j - 1].ts).total_seconds() <= BURST_GAP:
            j += 1
        after: Counter = Counter()
        total_after = 0
        for k in range(j - 1, i - 1, -1):
            m = msgs[k]
            others = total_after - after[m.user]
            distinct = len(after) - (1 if after[m.user] else 0)
            if (m.kind == TEXT and others > 0 and len(m.text) <= 100
                    and textutil.has_content(m.text)):
                key = (others, distinct, -k)
                if best is None or key > best:
                    best, best_i = key, k
            after[m.user] += 1
            total_after += 1
        i = j
    if best is None:
        return None
    m = msgs[best_i]
    return {
        "ts": _iso(m.ts), "name": m.user, "text": _quote(m.text, include_quote),
        "followers": best[0], "distinct_people": best[1],
    }
