"""동아시아 문자 폭(East Asian Width)을 고려한 표 정렬.

W/F 문자(한글, 한자, 전각 기호)는 2칸, 결합 문자는 0칸, 나머지(A 포함)는 1칸으로 센다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def display_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def pad(text: str, width: int, align: str = "l") -> str:
    """표시 폭 기준으로 width 칸까지 채운다. align: l(왼쪽), r(오른쪽), c(가운데)."""
    gap = width - display_width(text)
    if gap <= 0:
        return text
    if align == "r":
        return " " * gap + text
    if align == "c":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def render_table(headers: Sequence[object], rows: Sequence[Sequence[object]],
                 aligns: Sequence[str] | None = None) -> str:
    """ASCII 테두리 표. 박스 문자는 폭이 모호(A)해서 쓰지 않는다."""
    n = len(headers)
    aligns = list(aligns) if aligns is not None else ["l"] * n
    if len(aligns) != n:
        raise ValueError("aligns 개수가 열 개수와 다릅니다")
    head = [str(h) for h in headers]
    body = []
    for row in rows:
        cells = [str(c) for c in row]
        if len(cells) != n:
            raise ValueError("행의 열 개수가 헤더와 다릅니다")
        body.append(cells)

    widths = [max([display_width(head[i])] + [display_width(r[i]) for r in body]) for i in range(n)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells: list[str], header: bool = False) -> str:
        parts = []
        for i, cell in enumerate(cells):
            align = "c" if header else aligns[i]
            parts.append(" " + pad(cell, widths[i], align) + " ")
        return "|" + "|".join(parts) + "|"

    out = [sep, line(head, header=True), sep]
    out.extend(line(r) for r in body)
    out.append(sep)
    return "\n".join(out)
