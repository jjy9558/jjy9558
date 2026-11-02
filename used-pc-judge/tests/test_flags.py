import unittest

from tests.helpers import Base, parse


def codes(text):
    return parse(text).flag_codes()


class RedFlags(Base):
    def test_mining(self):
        self.assertIn("mining", codes("RTX 3080 채굴하던 카드입니다"))
        self.assertIn("mining", codes("마이닝용으로 2년 썼어요"))

    def test_mining_negated(self):
        for t in ("채굴 안했어요", "비채굴 카드", "채굴 이력 없음", "채굴용 아닙니다", "채굴x 게임용"):
            p = parse("RX 6800 XT " + t)
            self.assertNotIn("mining", p.flag_codes(), t)
            self.assertTrue(any("채굴" in x for x in p.positives), t)

    def test_refurb(self):
        self.assertIn("refurb", codes("RTX 3060 Ti 리퍼 제품"))
        self.assertIn("refurb", codes("제조사 리퍼비시"))

    def test_prepay_with_direct_is_scam_level(self):
        p = parse("직거래만 합니다. 선입금 먼저 해주세요")
        f = [f for f in p.flags if f.code == "prepay_direct"]
        self.assertTrue(f)
        self.assertEqual(f[0].severity, 3)
        self.assertTrue(f[0].scam)
        self.assertIn("prepay_direct", codes("직거래만 가능, 예약금 5만원 먼저 입금"))

    def test_prepay_without_direct_is_milder(self):
        p = parse("택배 거래 선입금 부탁드려요")
        self.assertNotIn("prepay_direct", p.flag_codes())
        f = [f for f in p.flags if f.code == "prepay"]
        self.assertTrue(f)
        self.assertEqual(f[0].severity, 2)

    def test_normal_courier_payment_is_not_flagged(self):
        self.assertEqual(codes("입금 확인 후 택배 발송합니다 안전결제 가능"), [])

    def test_no_safe_payment(self):
        self.assertIn("no_safe_pay", codes("안전결제 불가합니다"))
        self.assertIn("no_safe_pay", codes("네이버페이 불가"))

    def test_messenger_redirect(self):
        self.assertIn("messenger", codes("텔레그램으로 연락주세요"))
        self.assertIn("messenger", codes("카톡으로 문의주세요"))
        self.assertNotIn("messenger", codes("카톡 말고 채팅으로 문의"))

    def test_external_link(self):
        p = parse("결제는 여기서 https://bit.ly/abc 입금 부탁")
        f = [f for f in p.flags if f.code == "external_link"]
        self.assertTrue(f)
        self.assertEqual(f[0].severity, 3)
        # 결제 맥락이 없는 링크는 참고 수준
        p = parse("사진 더 보기 https://example.com/photo")
        self.assertIn("link", p.flag_codes())
        self.assertNotIn("external_link", p.flag_codes())

    def test_boot_failure(self):
        for t in ("부팅 안됨", "부팅불가 부품용", "전원이 안 들어옵니다", "화면 안 나옴", "고장났어요", "수리용"):
            f = [f for f in parse("본체 " + t).flags if f.code == "boot_fail"]
            self.assertTrue(f, t)
            self.assertTrue(f[0].defect)
            self.assertEqual(f[0].severity, 3)

    def test_boot_failure_negated(self):
        for t in ("불량 없음", "고장 없어요", "고장난 적 없습니다"):
            self.assertNotIn("boot_fail", codes("본체 정상 " + t), t)

    def test_unstable(self):
        self.assertIn("unstable", codes("가끔 블루스크린 떠요"))
        self.assertIn("unstable", codes("게임하면 갑자기 꺼짐"))
        self.assertNotIn("unstable", codes("블루스크린 없음"))

    def test_psu_unknown(self):
        self.assertIn("psu_unknown", codes("파워는 무명이라 잘 모르겠어요"))
        self.assertIn("psu_unknown", codes("노네임 파워입니다"))
        self.assertIn("psu_unknown", codes("파워 출처불명"))

    def test_cooler_noise(self):
        self.assertIn("cooler_noise", codes("쿨러 소음 약간 있어요"))
        self.assertIn("cooler_noise", codes("그래픽 팬 소리 좀 남"))
        self.assertIn("cooler_noise", codes("코일 울음 있음"))
        p = parse("쿨러 소음 없음")
        self.assertNotIn("cooler_noise", p.flag_codes())
        self.assertTrue(any("소음" in x for x in p.positives))

    def test_no_box_and_receipt(self):
        self.assertIn("no_box", codes("박스 없음"))
        self.assertIn("no_box", codes("박스x"))
        self.assertIn("no_receipt", codes("영수증 없어요"))
        self.assertNotIn("no_box", codes("박스 있음 풀박"))

    def test_urgent(self):
        self.assertIn("urgent", codes("[급처] 본체"))
        self.assertIn("urgent", codes("이사 때문에 급하게 팝니다"))

    def test_too_good(self):
        p = parse("시세보다 너무 싸게 올려요")
        f = [f for f in p.flags if f.code == "too_good"]
        self.assertTrue(f)
        self.assertTrue(f[0].scam)
        self.assertIn("too_good", codes("파격 세일 헐값 처분"))

    def test_no_test_and_third_party_and_cosmetic(self):
        self.assertIn("no_test", codes("환불 불가, 현상 그대로 판매"))
        self.assertIn("third_party", codes("지인 물건 대신 팔아요"))
        self.assertIn("cosmetic", codes("케이스에 기스 있어요"))

    def test_severity_ordering(self):
        p = parse("부팅 안됨 박스 없음 급처")
        sev = {f.code: f.severity for f in p.flags}
        self.assertGreater(sev["boot_fail"], sev["no_box"])

    def test_clean_listing_has_no_flags(self):
        p = parse("본체 팝니다\n인텔 i5-12400F\nRTX 3060 12GB\n램 16GB DDR4 3200\n가격 65만원")
        self.assertEqual(p.flags, [])

    def test_positive_signals(self):
        p = parse("풀박스 영수증 있음, 시리얼 확인 가능, 벤치 인증 가능, 현장 테스트 가능, 안전결제 가능, 하자 없음")
        for word in ("풀박스", "영수증", "시리얼", "벤치", "현장 테스트", "안전결제", "정상 작동"):
            self.assertTrue(any(word in x for x in p.positives), word)


if __name__ == "__main__":
    unittest.main()
