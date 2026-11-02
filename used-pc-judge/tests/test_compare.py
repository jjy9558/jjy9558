import unittest

from tests.helpers import Base, make_table, TODAY
from usedpc.compare import compare_texts
from usedpc.parser import split_listings
from usedpc.report import render_compare, render_compare_md

FAIR = """i5 12400F, RTX 3060 12G, 램 16GB DDR4 3200, NVMe 500G, B660M,
파워 시소닉 650W 골드, 정품쿨러, 케이스 다크플래쉬 DLX21
가격 53만원
"""
CHEAP = FAIR.replace("53만원", "49만원")
PRICEY = FAIR.replace("53만원", "65만원")
SCAM = FAIR.replace("53만원", "30만원 직거래만 예약금 먼저 입금 텔레그램으로 연락")
NOPRICE = FAIR.replace("가격 53만원", "")
LAPTOP = "삼성 갤럭시북 노트북 50만"


def joined(*texts):
    return "\n---\n".join(texts)


class SplitListings(Base):
    def test_split(self):
        parts = split_listings("a 1\n---\nb 2\n----\n\nc 3\n")
        self.assertEqual([p.strip() for p in parts], ["a 1", "b 2", "c 3"])

    def test_dashes_inside_text_do_not_split(self):
        parts = split_listings("i5-12400F --- 본체 55만\n---\nsecond")
        self.assertEqual(len(parts), 2)

    def test_empty_blocks_ignored(self):
        self.assertEqual(split_listings("\n---\n\n---\n"), [])


class CompareRanking(Base):
    def rows(self, *texts):
        return compare_texts(joined(*texts), make_table(), TODAY)

    def test_cheaper_ranks_higher(self):
        rows = self.rows(PRICEY, FAIR, CHEAP)
        self.assertEqual([r.index for r in rows], [3, 2, 1])
        self.assertEqual([r.rank for r in rows], [1, 2, 3])
        self.assertEqual([r.judgment.verdict for r in rows], ["적정", "적정", "비싸다"])

    def test_suspicious_after_normal_even_if_score_high(self):
        rows = self.rows(SCAM, PRICEY, FAIR)
        self.assertEqual(rows[-1].judgment.verdict, "의심")
        self.assertEqual(rows[-1].index, 1)
        self.assertEqual(rows[0].index, 3)

    def test_undecidable_goes_last(self):
        rows = self.rows(NOPRICE, SCAM, FAIR, LAPTOP)
        verdicts = [r.judgment.verdict for r in rows]
        self.assertEqual(verdicts[0], "적정")
        self.assertEqual(verdicts[1], "의심")
        self.assertEqual(set(verdicts[2:]), {"판단불가"})

    def test_stable_for_ties(self):
        rows = self.rows(FAIR, FAIR)
        self.assertEqual([r.index for r in rows], [1, 2])

    def test_labels_use_first_line(self):
        rows = self.rows(FAIR)
        self.assertTrue(rows[0].label.startswith("i5 12400F"))

    def test_render_text_and_markdown(self):
        rows = self.rows(PRICEY, FAIR, SCAM)
        text = render_compare(rows)
        self.assertIn("순위", text)
        self.assertIn("의심", text)
        self.assertIn("비싸다", text)
        md = render_compare_md(rows)
        self.assertIn("| 순위 |", md)
        self.assertIn("|---|", md)
        lines = [l for l in md.splitlines() if l.startswith("| ")]
        self.assertEqual(len(lines), 4)    # 헤더 + 3행

    def test_empty_input(self):
        self.assertEqual(compare_texts("\n\n", make_table(), TODAY), [])


if __name__ == "__main__":
    unittest.main()
