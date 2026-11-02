import unittest

from jjkit import redact as R
from jjkit.redact import Redactor, find, luhn_ok, mask_id


def luhn_complete(prefix):
    """prefix 뒤에 검증 숫자를 붙여 Luhn을 통과하는 가짜 카드번호를 만든다."""
    for d in "0123456789":
        if luhn_ok(prefix + d):
            return prefix + d
    raise AssertionError


# 실제처럼 보이는 비밀값을 저장소에 남기지 않도록 실행 시점에 만든다
FAKE_GH = "gh" + "p_" + "Ab1" * 10
FAKE_SK = "sk" + "-" + "proj" + "X9y8" * 6
FAKE_AWS = "AK" + "IA" + "Q" * 16
CARD = luhn_complete("4" + "1" * 14)


def red(text, **kw):
    kw.setdefault("use_os_user", False)
    r = Redactor(**kw)
    return r.redact(text), r


class TokenTests(unittest.TestCase):
    def test_api_keys(self):
        out, r = red("keys: %s %s %s" % (FAKE_GH, FAKE_SK, FAKE_AWS))
        self.assertEqual(out, "keys: <토큰> <토큰> <토큰>")
        self.assertEqual(r.counts["api_key"], 3)

    def test_plain_sk_words_kept(self):
        self.assertEqual(red("sk-some-plain-words-here")[0], "sk-some-plain-words-here")

    def test_bearer_and_kv(self):
        out, _ = red("Authorization: Bearer abc.def1234 and password=hunter22x")
        self.assertIn("Bearer <토큰>", out)
        self.assertIn("password=<값>", out)

    def test_kv_identifier_kept(self):
        self.assertEqual(red("token = tokenizer")[0], "token = tokenizer")

    def test_url_query_and_userinfo(self):
        out, r = red("see https://bob:pw@example.com/a?x=1&y=2#frag")
        self.assertEqual(out, "see https://example.com/a?x=<값>&y=<값>#frag")
        self.assertEqual(r.counts["url_userinfo"], 1)


class PersonalTests(unittest.TestCase):
    def test_phone_variants(self):
        for s in ("010-1234-5678", "01012345678", "010.1234.5678", "+82 10-1234-5678", "02-123-4567",
                  "031 123 4567"):
            self.assertEqual(red("연락 " + s + " 주세요")[0], "연락 <전화번호> 주세요", s)

    def test_service_number_kept(self):
        self.assertEqual(red("고객센터 1588-1234")[0], "고객센터 1588-1234")

    def test_rrn_with_and_without_hyphen(self):
        self.assertEqual(red("900101-1234567")[0], "<주민번호>")
        self.assertEqual(red("9001011234567")[0], "<주민번호>")
        self.assertEqual(red("900101-5234567")[0], "<주민번호>")  # 외국인등록번호
        self.assertNotIn("rrn", find("901301-1234567"))  # 13월은 주민번호가 아님

    def test_cards(self):
        grouped = " ".join(CARD[i:i + 4] for i in range(0, 16, 4))
        self.assertEqual(red("카드 " + grouped)[0], "카드 <카드번호>")
        self.assertEqual(red("카드 " + CARD)[0], "카드 <카드번호>")
        bad = CARD[:-1] + str((int(CARD[-1]) + 1) % 10)
        self.assertEqual(red(bad)[0], bad)  # Luhn 실패 숫자열은 그대로

    def test_accounts(self):
        self.assertEqual(red("입금계좌: 국민은행 123456789012")[0], "입금계좌: 국민은행 <계좌번호>")
        self.assertEqual(red("농협 3021234567890 으로")[0], "농협 <계좌번호> 으로")
        self.assertEqual(red("110-123-456789 로 보내 주세요")[0], "<계좌번호> 로 보내 주세요")

    def test_account_false_positives(self):
        for s in ("2026-11-02", "123-45-67890", "v1.2.3", "1-2-3"):
            self.assertEqual(red(s)[0], s, s)

    def test_labeled_id(self):
        self.assertEqual(red("고객번호: 12345678901")[0], "고객번호: <식별번호>")
        self.assertEqual(red("납부번호 A1234-56789")[0], "납부번호 <식별번호>")

    def test_email_ip(self):
        out, _ = red("me@example.com 192.168.0.10 git@github.com:o/r")
        self.assertEqual(out, "<이메일> <IP> git@github.com:o/r")

    def test_tailscale(self):
        self.assertEqual(red("mini.tail1234.ts.net")[0], "<호스트>")


class PathAndWordTests(unittest.TestCase):
    def test_paths_and_username_learning(self):
        out, r = red(r"C:\Users\jinyoung\proj\app.py 그리고 /home/jinyoung/x.txt, jinyoung 님")
        self.assertEqual(out, "<경로>/app.py 그리고 <경로>/x.txt, <사용자> 님")
        self.assertIn("jinyoung", r.usernames)

    def test_custom_words(self):
        self.assertEqual(red("홍길동님이 Acme에서", words=["홍길동", "Acme"])[0], "<가림>님이 <가림>에서")

    def test_disable(self):
        out, _ = red("/home/someone/a.txt 010-1234-5678", disable={"path", "username"})
        self.assertEqual(out, "/home/someone/a.txt <전화번호>")
        with self.assertRaises(ValueError):
            Redactor(disable={"nope"})


class ApiTests(unittest.TestCase):
    def test_idempotent(self):
        text = ("전화 010-1234-5678, 카드 %s, 계좌 110-123-456789, %s, 900101-1234567, /home/a/b/c.txt"
                % (CARD, FAKE_GH))
        once = R.redact(text)
        self.assertEqual(R.redact(once), once)

    def test_find(self):
        got = find("판매합니다 010-1234-5678 입금계좌 신한은행 110123456789")
        self.assertEqual(got, {"phone": 1, "account": 1})
        self.assertEqual(find("깨끗한 글"), {})

    def test_summary_has_no_values(self):
        _, r = red("010-1234-5678")
        self.assertEqual(r.summary(), [("phone", R.RULE_LABELS["phone"], 1)])

    def test_non_string(self):
        self.assertIsNone(Redactor(use_os_user=False).redact(None))
        self.assertEqual(Redactor(use_os_user=False).redact(""), "")

    def test_mask_id(self):
        self.assertEqual(mask_id("12345678901"), "********901")
        self.assertEqual(mask_id("AB-12345"), "**-**345")
        self.assertEqual(mask_id("ab"), "**")
        self.assertEqual(mask_id(""), "")


if __name__ == "__main__":
    unittest.main()
