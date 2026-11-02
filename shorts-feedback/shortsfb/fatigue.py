"""제작 피로 규칙: 최근 며칠 동안 만든 편수가 많거나 쉬는 날 없이 이어지면 휴식을 권하는 한 줄.

기록은 날짜별 편수(``production.json``)만 저장한다. 진단이 아니라 장난스러운 넛지다.
"""
from datetime import date, timedelta
from pathlib import Path

from .store import read_json, write_json

FILE = "production.json"
DEFAULT_WINDOW, DEFAULT_LIMIT, DEFAULT_STREAK = 3, 5, 6


def load(data: Path) -> dict[str, int]:
    d = read_json(Path(data) / FILE, {"days": {}})
    return {k: int(v) for k, v in d.get("days", {}).items()}


def record(data: Path, when: str | None = None, count: int = 1, absolute: bool = False) -> int:
    """그날 편수를 더하거나(기본) 바로 정한다(``absolute``). 같은 날의 새 합계를 돌려준다."""
    day = date.fromisoformat(when) if when else date.today()
    if count < 0 or count > 100:
        raise ValueError("편수는 0~100이어야 합니다")
    days = load(data)
    k = day.isoformat()
    days[k] = count if absolute else days.get(k, 0) + count
    if days[k] == 0:
        del days[k]
    write_json(Path(data) / FILE, {"days": days})
    return days.get(k, 0)


def evaluate(days: dict[str, int], today: date | None = None, window: int = DEFAULT_WINDOW,
             limit: int = DEFAULT_LIMIT, streak: int = DEFAULT_STREAK) -> dict:
    today = today or date.today()
    recent = sum(days.get((today - timedelta(d)).isoformat(), 0) for d in range(window))
    d = 0 if days.get(today.isoformat(), 0) else 1  # 오늘 아직 안 만들었으면 어제부터 센다
    run = 0
    while days.get((today - timedelta(d)).isoformat(), 0) > 0 and run < 365:
        run += 1
        d += 1
    msgs = []
    if recent >= limit:
        msgs.append("최근 %d일 동안 %d편을 만들었어요(기준 %d편). 오늘은 쉬어 가세요." % (window, recent, limit))
    if run >= streak:
        msgs.append("%d일 연속으로 만들고 있어요(기준 %d일). 하루는 아무것도 안 만드는 날로 정해요." % (run, streak))
    return {"tired": bool(msgs), "recent": recent, "streak": run, "window": window, "limit": limit,
            "line": msgs[0] if msgs else "제작 페이스는 괜찮아요 (최근 %d일 %d편, 연속 %d일)." % (window, recent, run)}
