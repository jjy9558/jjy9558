import unittest

from tests.helpers import Base, make_table, parse, TODAY
from usedpc.judge import judge
from usedpc.questions import make_questions, seller_message

COMPLETE = """i5 12400F, RTX 3060 12G, 램 16GB(8x2) DDR4 3200, NVMe 500G, B660M,
파워 시소닉 650W 골드, 정품쿨러, 케이스 다크플래쉬 DLX21, 2024년 구매
무상 AS 2027년 3월까지, 영수증 있음, 네고 가능 가격 54만원
"""


def questions(text):
    p = parse(text)
    j = judge(p, make_table(), TODAY)
    return make_questions(p, j), p, j


def texts(qs):
    return [q.text for q in qs]


class SellerQuestions(Base):
    def test_psu_brand_question_template(self):
        qs, _, _ = questions("i5 12400F RTX 3060 12G 램 16 ssd 500 본체 55만")
        self.assertIn("혹시 파워 브랜드/모델 알 수 있을까요?", texts(qs))

    def test_psu_unknown_flag_asks_psu_even_if_watt_known(self):
        qs, _, _ = questions("i5 12400F RTX 3060 12G 램 16 ssd 500 본체 파워 600w 무명 55만")
        self.assertIn("혹시 파워 브랜드/모델 알 수 있을까요?", texts(qs))

    def test_complete_listing_asks_few_required_questions(self):
        qs, _, _ = questions(COMPLETE)
        self.assertEqual([q for q in qs if q.priority == 1], [])
        self.assertNotIn("혹시 파워 브랜드/모델 알 수 있을까요?", texts(qs))

    def test_missing_fields_generate_questions(self):
        qs, _, _ = questions("본체 팝니다 55만")
        joined = " ".join(texts(qs))
        for word in ("CPU", "그래픽카드", "램", "저장장치", "파워"):
            self.assertIn(word, joined)
        self.assertTrue(all(q.priority == 1 for q in qs[:4]))

    def test_missing_price(self):
        qs, _, _ = questions("i5 12400F RTX 3060 12G 램 16 ssd 500 본체")
        self.assertTrue(any("가격" in t for t in texts(qs)))

    def test_vram_variant_question(self):
        qs, _, _ = questions("i5 12400f RTX 3060 본체 램 16 ssd 500 55만")
        vq = [t for t in texts(qs) if "VRAM" in t]
        self.assertTrue(vq)
        self.assertIn("8GB/12GB", vq[0])

    def test_cpu_generation_only(self):
        qs, _, _ = questions("인텔 i5 12세대 + 3060 본체 60만")
        self.assertTrue(any("CPU 정확한 모델명" in t for t in texts(qs)))

    def test_ram_details_missing(self):
        qs, _, _ = questions("i5 12400F RTX 3060 12G 램 16기가 ssd 500 본체 55만")
        self.assertTrue(any("DDR 규격" in t for t in texts(qs)))

    def test_nvme_or_sata(self):
        qs, _, _ = questions("i5 12400F RTX 3060 12G 램 16 ssd 500 본체 55만")
        self.assertTrue(any("NVMe" in t and "SATA" in t for t in texts(qs)))

    def test_red_flag_follow_ups(self):
        qs, _, _ = questions(COMPLETE.replace("영수증 있음", "") + "채굴 이력 있음. 쿨러 소음 있음. 박스 없음. 부팅 가끔 안됨")
        joined = " ".join(texts(qs))
        self.assertIn("채굴 이력", joined)
        self.assertIn("소음", joined)
        self.assertIn("박스/영수증", joined)
        self.assertIn("증상", joined)

    def test_no_box_but_receipt_present_does_not_ask_for_proof(self):
        qs, _, _ = questions(COMPLETE + "박스 없음")
        self.assertFalse(any("박스/영수증" in t for t in texts(qs)))

    def test_payment_questions(self):
        qs, _, _ = questions(COMPLETE + "직거래만, 선입금 먼저, 안전결제 불가, 텔레그램으로 연락")
        joined = " ".join(texts(qs))
        self.assertIn("선입금/예약금 없이", joined)
        self.assertIn("안전결제", joined)

    def test_suspicious_verdict_adds_verification_question(self):
        qs, _, j = questions(COMPLETE.replace("54만원", "25만원"))
        self.assertEqual(j.verdict, "의심")
        self.assertTrue(any("현장" in t or "영상 통화" in t for t in texts(qs)))

    def test_sorted_by_priority(self):
        qs, _, _ = questions("본체 팝니다 55만 채굴")
        pr = [q.priority for q in qs]
        self.assertEqual(pr, sorted(pr))

    def test_excluded_parts_not_asked(self):
        qs, _, _ = questions("i5 12400 본체 램 16 ssd 500 파워 500w 그래픽카드 제외 25만")
        self.assertFalse(any("그래픽카드 모델명" in t for t in texts(qs)))

    def test_seller_message_template(self):
        qs, _, _ = questions("i5 12400F RTX 3060 12G 램 16 ssd 500 본체 55만")
        msg = seller_message(qs)
        self.assertTrue(msg.startswith("안녕하세요"))
        self.assertIn("1. ", msg)
        self.assertIn("혹시 파워 브랜드/모델 알 수 있을까요?", msg)
        self.assertTrue(msg.rstrip().endswith("감사합니다."))
        # 참고(3순위) 질문은 기본 메시지에서 빠진다
        self.assertNotIn("네고", msg)

    def test_seller_message_with_few_questions_still_has_content(self):
        qs, _, _ = questions(COMPLETE)
        msg = seller_message(qs)
        self.assertGreaterEqual(msg.count("\n"), 2)


if __name__ == "__main__":
    unittest.main()
