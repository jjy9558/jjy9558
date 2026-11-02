"""한글(전각 문자)을 포함한 표를 맞춰 출력하기 위한 도우미."""

from __future__ import annotations

import unicodedata
from typing import Iterable, List, Optional, Sequence


def display_width(text: str) -> int:
    width = 0
    for ch in str(text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int, align: str = "l") -> str:
    gap = width - display_width(text)
    if gap <= 0:
        return text
    return " " * gap + text if align == "r" else text + " " * gap


def table(headers: Sequence[str], rows: Iterable[Sequence], aligns: Optional[List[str]] = None) -> str:
    rows = [[str(cell) for cell in row] for row in rows]
    aligns = aligns or ["l"] * len(headers)
    widths = []
    for i, header in enumerate(headers):
        widths.append(max([display_width(header)] + [display_width(r[i]) for r in rows]))

    def line(cells):
        return "  ".join(pad(c, w, a) for c, w, a in zip(cells, widths, aligns)).rstrip()

    out = [line(list(headers)), "  ".join("-" * w for w in widths)]
    out.extend(line(r) for r in rows)
    return "\n".join(out)


def won(amount: int) -> str:
    return f"{int(amount):,}원"
