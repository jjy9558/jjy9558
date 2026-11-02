"""소재 카드 수집기. 모든 원본은 읽기 전용으로만 연다(쓰기/삭제 없음).

- life-nagger: counters.jsonl (한숨/푸시업/설거지 카운터)
- decision-pet: data.json 의 묘지(status=buried), voice-journal 에서 가져온 항목(bury_reason=vjournal) 포함
- cool-off: data.json 의 후회 목록(만족도 <= 2 또는 '거의 안 씀'), 참은 금액
- kakao-wrapped: ``--json`` 으로 저장한 *익명화* 통계 JSON 의 숫자 필드만. 원문 대화/멤버 이름/인용은 읽지 않는다.
- voice-journal: 사용자가 ``--journal-entry`` 로 지정한 일기 id 만. 지정이 없으면 DB 를 열지 않는다.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .model import Card

WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
USAGE_RARE = "거의 안 씀"       # cool-off 와 같은 값
REGRET_MAX_SATISFACTION = 2
MAX_CARDS = 10


@dataclass
class Sources:
    life_nagger: Path | None = None       # counters.jsonl 또는 그것이 든 폴더
    decision_pet: Path | None = None      # data.json 또는 폴더
    cool_off: Path | None = None          # data.json 또는 폴더
    kakao: Path | None = None             # kakao-wrapped --json 결과(익명화)
    voice_journal: Path | None = None     # journal.db
    journal_entries: list[int] = field(default_factory=list)


def _resolve(p: Path | None, filename: str) -> Path | None:
    if p is None:
        return None
    p = Path(p)
    return p / filename if p.is_dir() else p


def _load_json(path: Path, warnings: list[str], label: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        warnings.append("%s: 파일이 없어 건너뜀" % label)
    except (OSError, ValueError) as e:
        warnings.append("%s: 읽을 수 없어 건너뜀 (%s)" % (label, type(e).__name__))
    return None


def _to_int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def ko_hour(h: int) -> str:
    if h < 6:
        return "새벽 %d시" % (h or 12)
    if h < 12:
        return "오전 %d시" % h
    if h < 18:
        return "오후 %d시" % (h - 12 or 12)
    return "밤 %d시" % (h - 12)


# ------------------------------------------------------------ life-nagger
def from_life_nagger(path: Path, now: date, warnings: list[str]) -> list[Card]:
    p = _resolve(path, "counters.jsonl")
    rows = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                    rows.append((datetime.fromisoformat(o["t"]), str(o["k"]), int(o.get("n", 1))))
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        warnings.append("life-nagger: counters.jsonl 을 읽을 수 없어 건너뜀")
        return []
    rows = [r for r in rows if r[0].date() <= now]
    cards = []
    dishes = [t for t, k, _ in rows if k == "dishes"]
    if dishes:
        days = (now - max(dishes).date()).days
        if days >= 2:
            cards.append(Card("nagger", "dish_streak", {"days": days}, priority=min(95, 40 + days * 5)))
    start = now - timedelta(days=6)
    week = [(t, n) for t, k, n in rows if k == "sigh" and start <= t.date() <= now]
    total = sum(n for _, n in week)
    if total >= 3:
        buckets: dict = {}
        for t, n in week:
            buckets[(t.weekday(), t.hour)] = buckets.get((t.weekday(), t.hour), 0) + n
        (wd, hr), _ = max(sorted(buckets.items()), key=lambda kv: kv[1])
        cards.append(Card("nagger", "sigh_week", {"count": total, "weekday": WEEKDAYS_KO[wd], "hour": ko_hour(hr)},
                          priority=min(90, 55 + total)))
    start = now - timedelta(days=29)
    push = sum(n for t, k, n in rows if k == "pushup" and start <= t.date() <= now)
    if push >= 10:
        cards.append(Card("nagger", "pushup_month", {"count": push}, priority=45))
    return cards


# ------------------------------------------------------------ decision-pet
def from_decision_pet(path: Path, now: date, warnings: list[str]) -> list[Card]:
    data = _load_json(_resolve(path, "data.json"), warnings, "decision-pet")
    if not isinstance(data, dict):
        return []
    buried = [t for t in data.get("tasks", []) if isinstance(t, dict) and t.get("status") == "buried"]
    cards = []
    if len(buried) >= 3:
        skipped = sum(_to_int(t.get("times_skipped")) for t in buried)
        cards.append(Card("pet", "graveyard_count", {"count": len(buried), "skipped_total": skipped},
                          priority=min(88, 50 + 2 * len(buried))))
    own = [t for t in buried if t.get("bury_reason") != "vjournal" and t.get("title")]
    if own:
        t = sorted(own, key=lambda t: (-_to_int(t.get("times_skipped")), str(t.get("created")), _to_int(t.get("id"))))[0]
        age = _age_days(t.get("created"), now)
        cards.append(Card("pet", "graveyard_tomb", {"skipped": _to_int(t.get("times_skipped")), "age_days": age},
                          subject=str(t["title"]), priority=min(92, 60 + _to_int(t.get("times_skipped")) * 3)))
    quit_ = [t for t in buried if t.get("bury_reason") == "vjournal" and t.get("title")]
    if quit_:
        t = sorted(quit_, key=lambda t: (str(t.get("declared_day") or t.get("created")), _to_int(t.get("id"))))[0]
        days = _age_days(t.get("declared_day") or t.get("created"), now)
        cards.append(Card("pet", "declared_quit", {"days": days}, subject=str(t["title"]),
                          priority=min(93, 62 + days // 10)))
    return cards


def _age_days(value, now: date) -> int:
    try:
        d = datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        return 0
    return max(0, (now - d).days)


# ------------------------------------------------------------ cool-off
def won_ko(n: int) -> str:
    """189000 -> '18만 9천원'. 대본과 제목이 같은 표기를 쓰게 한다."""
    n = int(n)
    man, rest = divmod(n, 10000)
    parts = []
    if man:
        parts.append("%d만" % man)
    if rest:
        ch, low = divmod(rest, 1000)
        if ch:
            parts.append("%d천" % ch)
        if low:
            parts.append("%d" % low)
    return " ".join(parts) + "원" if parts else "0원"


def from_cool_off(path: Path, now: date, warnings: list[str]) -> list[Card]:
    data = _load_json(_resolve(path, "data.json"), warnings, "cool-off")
    if not isinstance(data, dict):
        return []
    regrets = []
    for p in data.get("purchases", []):
        if not isinstance(p, dict) or not p.get("name"):
            continue
        sat = p.get("satisfaction")
        if isinstance(sat, int) and (sat <= REGRET_MAX_SATISFACTION or p.get("usage") == USAGE_RARE):
            regrets.append(p)
    cards = []
    regrets.sort(key=lambda p: (-_to_int(p.get("price")), str(p.get("date")), _to_int(p.get("id"))))
    for p in regrets[:2]:
        cards.append(Card("cooloff", "regret_item",
                          {"price": _to_int(p.get("price")), "satisfaction": p["satisfaction"],
                           "usage": str(p.get("usage") or ""), "days_ago": _age_days(p.get("date"), now)},
                          subject=str(p["name"]), priority=min(94, 55 + _to_int(p.get("price")) // 20000)))
    if len(regrets) >= 2:
        cards.append(Card("cooloff", "regret_total", {"count": len(regrets), "total": sum(_to_int(p.get("price")) for p in regrets)},
                          priority=70))
    skipped = [w for w in data.get("wishes", []) if isinstance(w, dict) and w.get("status") == "skipped"]
    if len(skipped) >= 2:
        cards.append(Card("cooloff", "saved_total", {"count": len(skipped), "total": sum(_to_int(w.get("price")) for w in skipped)},
                          priority=58))
    return cards


# ------------------------------------------------------------ kakao-wrapped
def from_kakao(path: Path, warnings: list[str]) -> list[Card]:
    data = _load_json(Path(path), warnings, "kakao-wrapped")
    if not isinstance(data, dict):
        return []
    if (data.get("meta") or {}).get("anonymized") is not True:
        warnings.append("kakao-wrapped: 익명화(--anonymize)된 통계가 아니라서 사용하지 않음")
        return []
    if data.get("empty"):
        return []
    cards = []
    # 숫자 필드만 읽는다: members/awards/quote/top_words 등 이름·문장이 들어 있는 키는 건드리지 않는다.
    tot = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    streak = data.get("streak") if isinstance(data.get("streak"), dict) else {}
    if _to_int(streak.get("days")) >= 3:
        cards.append(Card("kakao", "streak", {"days": _to_int(streak["days"])}, priority=min(85, 50 + _to_int(streak["days"]))))
    hours = data.get("hours")
    if isinstance(hours, list) and len(hours) == 24 and data.get("peak_hour") is not None:
        ph = _to_int(data["peak_hour"])
        if 0 <= ph < 24:
            cards.append(Card("kakao", "peak_hour", {"hour": ko_hour(ph), "count": _to_int(hours[ph])}, priority=60))
    bd = data.get("busiest_day") if isinstance(data.get("busiest_day"), dict) else {}
    m = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})", str(bd.get("date") or ""))
    if m and _to_int(bd.get("count")):
        cards.append(Card("kakao", "busiest_day", {"month": int(m.group(1)), "day": int(m.group(2)), "count": _to_int(bd["count"])},
                          priority=66))
    if _to_int(tot.get("messages")) >= 100:
        cards.append(Card("kakao", "volume", {"messages": _to_int(tot["messages"]), "members": _to_int(tot.get("members")),
                                                    "active_days": _to_int(tot.get("active_days"))}, priority=52))
    return cards


# ------------------------------------------------------------ voice-journal
def from_voice_journal(db: Path, ids: list[int], warnings: list[str]) -> list[Card]:
    if not ids:
        return []      # 사용자가 지정한 항목이 없으면 열지도 않는다
    db = Path(db)
    if not db.is_file():
        warnings.append("voice-journal: DB 가 없어 건너뜀")
        return []
    cards = []
    try:
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        warnings.append("voice-journal: DB 를 열 수 없어 건너뜀")
        return []
    try:
        for i in sorted(set(ids)):
            try:
                row = conn.execute("SELECT day, text FROM entries WHERE id=?", (int(i),)).fetchone()
            except sqlite3.Error:
                warnings.append("voice-journal: 형식이 달라 건너뜀")
                break
            if not row:
                warnings.append("voice-journal: 일기 #%d 없음" % i)
                continue
            text = re.sub(r"\s+", " ", str(row[1])).strip()
            excerpt = text[:140].rsplit(" ", 1)[0] if len(text) > 140 else text
            dm = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})", str(row[0]))
            day = "%d월 %d일" % (int(dm.group(1)), int(dm.group(2))) if dm else "어느 날"
            cards.append(Card("journal", "journal_entry", {"day": day}, subject=excerpt,
                              priority=72, consent="user-specified", id="journal-entry-%d" % i))
    finally:
        conn.close()
    return cards


# ------------------------------------------------------------ 전체
def collect(src: Sources, now: date, max_cards: int = MAX_CARDS) -> tuple[list[Card], list[str]]:
    """소재 카드(최대 max_cards, 우선순위 내림차순, 동률은 id)와 경고 목록."""
    warnings: list[str] = []
    cards: list[Card] = []
    if src.life_nagger:
        cards += from_life_nagger(src.life_nagger, now, warnings)
    if src.decision_pet:
        cards += from_decision_pet(src.decision_pet, now, warnings)
    if src.cool_off:
        cards += from_cool_off(src.cool_off, now, warnings)
    if src.kakao:
        cards += from_kakao(src.kakao, warnings)
    if src.voice_journal:
        cards += from_voice_journal(src.voice_journal, src.journal_entries, warnings)
    elif src.journal_entries:
        warnings.append("voice-journal: DB 경로가 없어 지정한 일기를 읽지 않음")
    seen, uniq = set(), []
    for c in cards:
        if c.id not in seen:
            seen.add(c.id)
            uniq.append(c)
    uniq.sort(key=lambda c: (-c.priority, c.id))
    return uniq[:max_cards], warnings
