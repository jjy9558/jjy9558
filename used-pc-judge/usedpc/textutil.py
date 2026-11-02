"""출력 보조: 원/만원 표기, 한글 폭을 고려한 정렬."""

import unicodedata
from typing import List, Optional, Sequence


def fmt_won(n: Optional[float]) -> str:
    if n is None:
        return "-"
    return "{:,}원".format(int(round(n)))


def fmt_man(n: Optional[float]) -> str:
    """원 단위 -> '85만원', '85.5만원'."""
    if n is None:
        return "-"
    man = n / 10000.0
    if abs(man - round(man)) < 0.005:
        return "{:,}만원".format(int(round(man)))
    return "{:,.1f}만원".format(man)


def fmt_money(n: Optional[float]) -> str:
    """'85만원 (850,000원)' 형태."""
    if n is None:
        return "-"
    return "{} ({})".format(fmt_man(n), fmt_won(n))


def round_to(n: float, unit: int = 5000) -> int:
    return int(round(n / unit) * unit)


def wlen(s: str) -> int:
    """터미널 표시 폭 (한글 등 전각 문자는 2칸)."""
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s: str, width: int, right: bool = False) -> str:
    gap = max(0, width - wlen(s))
    return (" " * gap + s) if right else (s + " " * gap)


def table(rows: Sequence[Sequence[str]], header: Sequence[str],
          right_cols: Sequence[int] = ()) -> List[str]:
    widths = [wlen(h) for h in header]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], wlen(c))
    out = []
    out.append("  ".join(pad(h, widths[i], i in right_cols) for i, h in enumerate(header)))
    out.append("  ".join("-" * widths[i] for i in range(len(header))))
    for r in rows:
        out.append("  ".join(pad(c, widths[i], i in right_cols) for i, c in enumerate(r)))
    return out


def md_table(rows: Sequence[Sequence[str]], header: Sequence[str]) -> List[str]:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(c.replace("|", "/") for c in r) + " |")
    return out


def ro(word: str) -> str:
    """'로/으로' 조사 선택 (대략): '4070 Super' -> '로', 'RTX 3090' -> '으로'."""
    w = word.rstrip(" )]")
    if not w:
        return "로"
    ch = w[-1]
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        return "로" if jong in (0, 8) else "으로"
    if ch.isdigit():
        return "으로" if ch in "03" or ch == "6" else "로"
    if ch.lower() in "mn":
        return "으로"
    return "로"
