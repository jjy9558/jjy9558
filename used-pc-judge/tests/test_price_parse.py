import unittest

from tests.helpers import Base, parse
from usedpc.listing_info import parse_price_text, parse_sino_number


class PriceTextParsing(Base):
    def test_units(self):
        cases = {
            "85만": 850000,
            "85만원": 850000,
            "85.5만": 855000,
            "85만5천원": 855000,
            "85만 5천": 855000,
            "850,000원": 850000,
            "850000": 850000,
            "850000원": 850000,
            "850k": 850000,
            "85": 850000,        # 단위 없는 작은 수는 만원으로 간주
            "1,200,000": 1200000,
            "120만원": 1200000,
            "팔십오만원": 850000,
            "백이십만": 1200000,
            "₩850,000": 850000,
        }
        for t, e in cases.items():
            self.assertEqual(parse_price_text(t), e, t)

    def test_invalid(self):
        self.assertIsNone(parse_price_text("가격미정"))
        self.assertIsNone(parse_price_text(""))

    def test_sino(self):
        self.assertEqual(parse_sino_number("팔십오"), 85)
        self.assertEqual(parse_sino_number("이백삼십"), 230)
        self.assertEqual(parse_sino_number("십"), 10)
        self.assertIsNone(parse_sino_number("abc"))


class PriceInListing(Base):
    def price(self, text):
        return parse(text).val("price")

    def test_labelled_and_title(self):
        self.assertEqual(self.price("본체 팝니다\n가격 65만원 (네고 가능)"), 650000)
        self.assertEqual(self.price("▶ 가격 : 850,000원"), 850000)
        self.assertEqual(self.price("[급처] 12400f 3060ti 완본체\n70만 직거래만"), 700000)

    def test_bare_number_after_label(self):
        self.assertEqual(self.price("i5 12400f 3060 본체\n가격 85 (택포)"), 850000)
        self.assertEqual(self.price("희망가 120"), 1200000)

    def test_sell_suffix(self):
        self.assertEqual(self.price("라이젠 5600 / RX 6700 XT 완본체 55에 팝니다"), 550000)

    def test_k_notation(self):
        self.assertEqual(self.price("i5 13400f 4060ti 본체 850k 직거래"), 850000)

    def test_cpu_k_suffix_not_price(self):
        self.assertIsNone(self.price("i7 8700k 램 16g"))
        self.assertIsNone(self.price("울트라5 245K 팝니다"))

    def test_reference_price_excluded(self):
        p = parse("i7 12700k 3070 본체 신품 150만에 샀고 지금 판매가 95만5천원 네고 사절")
        self.assertEqual(p.val("price"), 955000)
        self.assertEqual(p.val("original_price"), 1500000)
        self.assertFalse(p.val("negotiable"))

    def test_extra_amounts_excluded(self):
        p = parse("본체 80만원\n모니터 포함시 +10만원\n예약금 5만원")
        self.assertEqual(p.val("price"), 800000)

    def test_plus_between_parts_does_not_hide_price(self):
        self.assertEqual(self.price("5600 + 6600 본체 40만"), 400000)

    def test_no_price(self):
        p = parse("i5 12400f RTX 3060 램 16 ssd 500 본체 팝니다 가격은 채팅으로 문의주세요")
        self.assertFalse(p.has("price"))

    def test_negotiable(self):
        self.assertTrue(parse("60만 네고 가능").val("negotiable"))
        self.assertFalse(parse("60만 네고 불가").val("negotiable"))
        self.assertIsNone(parse("60만").val("negotiable"))

    def test_shipping_note(self):
        p = parse("25만원 택포")
        self.assertTrue(any("택배비" in n for n in p.notes))

    def test_price_candidates_recorded(self):
        p = parse("신품 150만에 샀고 지금 95만")
        self.assertEqual(len(p.price_candidates), 2)
        self.assertTrue(any(c["reference"] for c in p.price_candidates))

    def test_price_not_confused_with_specs(self):
        p = parse("램 16기가 ssd 500g 3060 12g 65만")
        self.assertEqual(p.val("price"), 650000)


if __name__ == "__main__":
    unittest.main()
