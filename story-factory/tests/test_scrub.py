import unittest

from storyfactory.scrub import Scrubber, sensitive_reasons


class ScrubTests(unittest.TestCase):
    def test_given_names_and_places_replaced(self):
        sc = Scrubber(["김민수"], ["행복아파트"], seed=3)
        out = sc.scrub("김민수 씨와 민수가 서울 행복아파트에서 만났다. 연락 010-1234-5678")
        for orig in ("김민수", "민수", "서울", "행복아파트", "5678"):
            self.assertNotIn(orig, out)
        self.assertEqual(sc.leaks(out), [])
        # 같은 사람은 같은 가명
        a = sc.scrub("김민수")
        b = sc.scrub("민수")
        self.assertEqual(a, b)

    def test_titled_names_detected_without_list(self):
        sc = Scrubber(seed=1)
        out = sc.scrub("이서준 님은 최영희씨와 박지수 씨를 만났다. 고객님도 있었다.")
        for orig in ("이서준", "최영희", "박지수"):
            self.assertNotIn(orig, out)
        self.assertIn("고객님", out)

    def test_pseudonyms_are_deterministic_and_seeded(self):
        t = "김민수와 박지영이 만났다"
        a = Scrubber(["김민수", "박지영"], seed=5).scrub(t)
        b = Scrubber(["김민수", "박지영"], seed=5).scrub(t)
        self.assertEqual(a, b)
        outs = {Scrubber(["김민수", "박지영"], seed=s).scrub(t) for s in range(8)}
        self.assertGreater(len(outs), 1)

    def test_pseudonym_never_contains_original(self):
        sc = Scrubber(["하늘이", "온유"], seed=0)
        out = sc.scrub("하늘이와 온유")
        self.assertEqual(sc.leaks(out), [])

    def test_redact_rules_applied(self):
        out = Scrubber().scrub("메일 a.b@example.com 경로 /home/someone/notes.txt")
        self.assertNotIn("example.com", out)
        self.assertNotIn("/home/someone", out)

    def test_sensitive_topics(self):
        self.assertIn("medical", sensitive_reasons("어제 병원에서 우울증 진단을 받았다"))
        self.assertIn("medical", sensitive_reasons("위암 수술 일정"))
        self.assertIn("minor", sensitive_reasons("우리 아이는 초등학생이다"))
        self.assertIn("minor", sensitive_reasons("올해 12살 조카"))
        self.assertIn("defamation", sensitive_reasons("상사가 사기꾼 같다"))
        self.assertIn("defamation", sensitive_reasons("김철수 씨는 쓰레기다"))
        # 오탐 방지
        for ok in ("딸기를 샀다", "암시적으로 말했다", "25세 직장인", "사기꾼 같은 광고 문구", "운동을 안 했다", "설거지 9일 방치"):
            self.assertEqual(sensitive_reasons(ok), [], ok)


if __name__ == "__main__":
    unittest.main()
