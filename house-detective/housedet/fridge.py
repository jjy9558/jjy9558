"""냉장고: 기한 계산, 상태 분류, 탐정 보고서."""
from datetime import date, timedelta

from . import match

DISCLAIMER_FOOD = "※ 참고용이며 보수적으로 잡은 값입니다. 보관 상태는 제각각이니 의심되면 버리세요."
SOON_DAYS = 2  # 남은 일수가 이 값 이하면 임박
STORAGES = ("냉장", "냉동", "실온")


def compute_expiry(shelf: list[dict], name: str, storage: str, start: date,
                   days: int | None = None, expires: date | None = None):
    """(만료일 또는 None, 근거) 를 돌려준다. 표에 없으면 None."""
    if expires is not None:
        return expires, "직접 입력한 기한"
    if days is not None:
        return start + timedelta(days=days), f"직접 입력 {days}일"
    entry = match.find(shelf, name)
    if entry is None:
        return None, "표에 없는 품목"
    d = entry.get("days", {}).get(storage)
    if d is None:
        return None, f"표에 '{storage}' 보관 정보 없음"
    return start + timedelta(days=int(d)), f"표 {entry['name']} {storage} {d}일"


def status(item: dict, today: date) -> tuple[str, int | None]:
    """('지남'|'임박'|'여유'|'모름', 남은 일수). 만료일 당일은 남은 0일로 '임박'(오늘까지)."""
    if not item.get("expires"):
        return "모름", None
    left = (date.fromisoformat(item["expires"]) - today).days
    if left < 0:
        return "지남", left
    if left <= SOON_DAYS:
        return "임박", left
    return "여유", left


def sorted_items(items: list[dict], today: date) -> list[dict]:
    """임박 순: 남은 일수 오름차순(지난 것 먼저), 기한 모름은 맨 뒤."""
    def key(it):
        _, left = status(it, today)
        return (left is None, left if left is not None else 0, it.get("id", 0))
    return sorted(items, key=key)


def left_text(st: str, left: int | None) -> str:
    if left is None:
        return "기한 모름"
    if left < 0:
        return f"{-left}일 지남"
    if left == 0:
        return "오늘까지"
    return f"D-{left}"


def verdict_line(it: dict, st: str, left: int | None) -> str:
    n = it["name"]
    if st == "지남":
        return f"{n}: 기한 {-left}일 지남. 먹지 말고 버리세요."
    if st == "임박":
        if left == 0:
            return f"{n}: 오늘이 마지막 날입니다. 오늘 안에 드시거나, 상태가 조금이라도 의심되면 버리세요."
        return f"{n}: {left}일 남음. 용의선상에 올랐습니다."
    if st == "모름":
        return f"{n}: 기한 정보 없음. 포장 표시를 확인하세요. 모르면 버리세요."
    return f"{n}: {left}일 남음. 일단 무죄."


def suggestions(items: list[dict], shelf: list[dict], today: date) -> list[str]:
    """임박(아직 안 지난) 품목으로 템플릿 아이디어. 지난 품목은 절대 쓰지 않는다."""
    soon = [it for it in items if status(it, today)[0] == "임박"]
    out = []
    for it in soon:
        e = match.find(shelf, it["name"])
        if e and e.get("ideas"):
            out.append(f"{it['name']}: {e['ideas']}")
    if len(soon) >= 2:
        names = ", ".join(i["name"] for i in soon[:4])
        out.append(f"({names}) 한꺼번에: 볶음밥이나 전으로 털어내기. 충분히 익혀서 오늘 안에 드세요.")
    return out


def report(items: list[dict], shelf: list[dict], today: date) -> list[str]:
    lines = [f"=== 냉장고 탐정 보고서 ({today.isoformat()}) ===", ""]
    if not items:
        return lines + ["냉장고가 텅 비었습니다. 수사할 용의자가 없군요.", "", DISCLAIMER_FOOD]
    ordered = sorted_items(items, today)
    expired = [i for i in ordered if status(i, today)[0] == "지남"]
    soon = [i for i in ordered if status(i, today)[0] == "임박"]
    if expired:
        lines.append(f"[현행범 {len(expired)}건] 이미 기한이 지났습니다. 판결: 폐기.")
        lines += ["  - " + verdict_line(i, *status(i, today)) for i in expired]
        lines.append("")
    if soon:
        lines.append(f"[용의자 {len(soon)}건] 곧 기한이 끝납니다. 알리바이를 대세요.")
        lines += ["  - " + verdict_line(i, *status(i, today)) for i in soon]
        lines.append("")
    if not expired and not soon:
        lines += ["오늘은 수상한 자가 없습니다. 탐정은 심심합니다.", ""]
    unknown = [i for i in ordered if status(i, today)[0] == "모름"]
    if unknown:
        lines.append("[기한 모름] " + ", ".join(i["name"] for i in unknown) + " (포장 표시를 확인하고 --expires로 알려 주세요)")
        lines.append("")
    sug = suggestions(items, shelf, today)
    if sug:
        lines.append("[수사 연계 요리 제안] 임박 품목 활용 아이디어")
        lines += ["  - " + s for s in sug]
        lines.append("")
    lines.append(DISCLAIMER_FOOD)
    return lines
