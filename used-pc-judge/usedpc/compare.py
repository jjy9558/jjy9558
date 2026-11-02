"""여러 매물 비교: '---' 로 구분된 텍스트를 가성비 점수 순으로 정렬."""

from dataclasses import dataclass
from typing import List

from .judge import Judgment, judge
from .parser import parse_listing, split_listings
from .prices import PriceTable


@dataclass
class Ranked:
    rank: int
    index: int          # 입력 순서 (1부터)
    label: str
    judgment: Judgment


def _label(text: str, idx: int) -> str:
    for line in text.splitlines():
        if line.strip():
            t = line.strip()
            return t if len(t) <= 28 else t[:27] + "…"
    return "매물 {}".format(idx)


_GROUP = {"싸다": 0, "적정": 0, "비싸다": 0, "의심": 1, "판단불가": 2}


def compare_texts(text: str, table: PriceTable, today=None, llm=None) -> List[Ranked]:
    listings = split_listings(text)
    rows = []
    for i, raw in enumerate(listings, 1):
        p = parse_listing(raw, today=today, llm=llm)
        j = judge(p, table, today)
        rows.append((i, _label(raw, i), j))

    def key(r):
        j = r[2]
        score = j.value_score if j.value_score is not None else -1e9
        return (_GROUP.get(j.verdict, 2), -score, r[0])

    rows.sort(key=key)
    return [Ranked(rank=n, index=i, label=lab, judgment=j) for n, (i, lab, j) in enumerate(rows, 1)]
