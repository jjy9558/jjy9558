"""이름 매칭: 정확히 일치 우선, 그다음 가장 긴 키가 들어 있는 항목."""
import re


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def keys_of(entry: dict) -> list[str]:
    return [norm(entry["name"])] + [norm(a) for a in entry.get("aliases", [])]


def find(entries: list[dict], text: str) -> dict | None:
    """entries 앞쪽이 우선(사용자 규칙을 앞에 두면 내장 규칙을 덮어쓴다)."""
    t = norm(text)
    if not t:
        return None
    for e in entries:
        if t in keys_of(e):
            return e
    best, best_len = None, 1
    for e in entries:
        for k in keys_of(e):
            if len(k) > best_len and k in t:
                best, best_len = e, len(k)
    return best


def find_all(entries: list[dict], text: str) -> list[dict]:
    """긴 키부터 훑어 겹치지 않게 모두 찾는다(캡션용)."""
    t = norm(text)
    pairs = sorted(((k, i) for i, e in enumerate(entries) for k in keys_of(e) if len(k) >= 2),
                   key=lambda p: (-len(p[0]), p[1]))
    found: dict[int, int] = {}
    for k, i in pairs:
        pos = t.find(k)
        if pos >= 0:
            found.setdefault(i, pos)
            t = t.replace(k, "\0" * len(k))
    return [entries[i] for i in sorted(found, key=lambda i: found[i])]
