"""터미널 표시 폭 계산과 표 출력 (한글 등 전각 문자는 2칸).

기준 구현: used-pc-judge/usedpc/textutil.py (``wlen``, ``pad``, ``table``).

- ``width("한글abc") == 7``. 결합 문자와 폭 0 문자(ZWJ, 변이 선택자 등)는 0칸.
- ``ambiguous=2``로 동아시아 '모호' 폭 문자(①, ※, ° 등)를 2칸으로 셀 수 있다(일부 한국어 콘솔).
"""
import unicodedata
from typing import Any, Iterable, Sequence

__all__ = ["char_width", "width", "pad", "truncate", "table", "md_table"]

_ZERO = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}


def char_width(ch: str, ambiguous: int = 1) -> int:
    if ch in _ZERO or unicodedata.combining(ch):
        return 0
    if "\ufe00" <= ch <= "\ufe0f":  # 변이 선택자
        return 0
    cat = unicodedata.category(ch)
    if cat in ("Cc", "Cf"):
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 2
    if eaw == "A":
        return ambiguous
    return 1


def width(s: Any, ambiguous: int = 1) -> int:
    return sum(char_width(ch, ambiguous) for ch in str(s))


def pad(s: Any, w: int, align: str = "left", fill: str = " ", ambiguous: int = 1) -> str:
    """표시 폭 ``w``에 맞춰 공백을 채운다. align: left | right | center. 넘치면 자르지 않는다."""
    s = str(s)
    gap = max(0, w - width(s, ambiguous))
    match align:
        case "right":
            return fill * gap + s
        case "center":
            left = gap // 2
            return fill * left + s + fill * (gap - left)
        case _:
            return s + fill * gap


def truncate(s: Any, w: int, ellipsis: str = "…", ambiguous: int = 1) -> str:
    """표시 폭 ``w`` 안으로 자른다(전각 문자 중간에서 자르지 않음)."""
    s = str(s)
    if width(s, ambiguous) <= w:
        return s
    ew = width(ellipsis, ambiguous)
    if w <= ew:
        ellipsis, ew = "", 0
    out, cur = [], 0
    for ch in s:
        cw = char_width(ch, ambiguous)
        if cur + cw > w - ew:
            break
        out.append(ch)
        cur += cw
    return "".join(out) + ellipsis


def table(rows: Iterable[Sequence[Any]], header: Sequence[Any] | None = None, *,
          right_cols: Iterable[int] = (), sep: str = "  ", rule: str = "-",
          max_width: dict[int, int] | None = None, ambiguous: int = 1) -> list[str]:
    """줄 목록을 돌려준다. ``right_cols``는 오른쪽 정렬할 열 번호, ``max_width``는 열별 최대 폭."""
    body = [[str(c) if c is not None else "" for c in r] for r in rows]
    head = [str(h) for h in header] if header is not None else None
    if max_width:
        for r in body + ([head] if head else []):
            for i, lim in max_width.items():
                if i < len(r):
                    r[i] = truncate(r[i], lim, ambiguous=ambiguous)
    ncol = max([len(r) for r in body] + [len(head) if head else 0] + [0])
    widths = [0] * ncol
    for r in body + ([head] if head else []):
        for i, c in enumerate(r):
            widths[i] = max(widths[i], width(c, ambiguous))
    right = set(right_cols)

    def line(cells: list[str]) -> str:
        cells = cells + [""] * (ncol - len(cells))
        return sep.join(pad(c, widths[i], "right" if i in right else "left", ambiguous=ambiguous)
                        for i, c in enumerate(cells)).rstrip()

    out = []
    if head is not None:
        out.append(line(head))
        if rule:
            out.append(sep.join(rule * w for w in widths))
    out.extend(line(r) for r in body)
    return out


def md_table(rows: Iterable[Sequence[Any]], header: Sequence[Any]) -> list[str]:
    def cell(c: Any) -> str:
        return ("" if c is None else str(c)).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(cell(h) for h in header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out.extend("| " + " | ".join(cell(c) for c in r) + " |" for r in rows)
    return out
