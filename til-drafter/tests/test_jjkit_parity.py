# -*- coding: utf-8 -*-
"""jjkit 마이그레이션 회귀: 예전 til-drafter 구현이 잡던 비밀은 지금도 모두 잡혀야 한다.

가짜 비밀은 실행 중에 조립한다(저장소에 진짜처럼 보이는 값을 두지 않기 위해).
"""
import unittest

import helpers  # noqa: F401  (sys.path 설정)
from til_drafter.redact import Redactor, RULE_LABELS

A = "abcdefghijklmnopqrstuvwxyz0123456789"


def _samples():
    return [
        ("private_key", "-----BEGIN " + "RSA PRIVATE" + " KEY-----\nMIIB" + A + "\n-----END " + "RSA PRIVATE" + " KEY-----", None),
        ("jwt", "t=" + "eyJ" + "hbGciOiJIUzI1" + "." + "eyJzdWIiOiIx" + "ODk" + "." + "sig" + A[:10], "eyJzdWIi"),
        ("api_key", "key " + "sk-" + "ant" + A[:30], A[:20]),
        ("api_key", "tok " + "gh" + "p_" + A[:30], A[:20]),
        ("api_key", "tok " + "github" + "_pat_" + A[:30], A[:20]),
        ("api_key", "tok " + "xo" + "xb-" + "1234567890-" + A[:12], A[:12]),
        ("api_key", "id " + "AK" + "IA" + "ABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP"),
        ("api_key", "k " + "AI" + "za" + A[:35], A[:30]),
        ("api_key", "k " + "glp" + "at-" + A[:25], A[:20]),
        ("api_key", "k " + "np" + "m_" + A[:32], A[:20]),
        ("api_key", "k " + "hf" + "_" + A[:32], A[:20]),
        ("bearer", "Authorization: Bearer " + A[12:36], A[14:30]),
        ("long_hex", "h " + "0123456789abcdef" * 3, "0123456789abcdef0123"),
        ("long_base64", "b " + "Ab1" + A[:20] + "XyZ" + "9" * 10, "XyZ9999"),
        ("secret_kv", "pass" + "word = hunter" + "22xyz", "hunter22xyz"),
        ("secret_kv", "API_" + "KEY=" + "Zq9" + "xw8vu7", "Zq9xw8vu7"),
        ("webhook", "https://hooks." + "slack.com/services/T000/B000/" + A[:20], A[:20]),
        ("url_userinfo", "https://bob:" + "pw1234" + "@example.com/x", "pw1234"),
        ("url_query", "https://example.com/p?code=" + "q1w2e3r4" + "&x=1", "q1w2e3r4"),
        ("tailscale", "host my-box.tail" + "1234.ts.net", "tail1234"),
        ("email", "mail jane.doe" + "@example.com", "jane.doe"),
        ("ipv6", "addr " + "2001:db8:" + "85a3::8a2e:370:7334", "85a3"),
        ("ipv4", "ip " + "203.0." + "113.77", "203.0.113.77"),
        ("rrn", "rrn " + "900101" + "-1234567", "1234567"),
        ("card", "card " + "4111 1111 " + "1111 1111", "4111 1111"),
        ("card", "card " + "4111111111" + "111111", "4111111111"),
        ("phone", "tel " + "010-1234-" + "5678", "1234-5678"),
        ("phone", "tel " + "02-123-" + "4567", "123-4567"),
        ("path", "open C:\\Users\\" + "carol" + "\\proj\\secret_notes.txt", "carol"),
        ("path", "open /home/" + "dave" + "/work/app/main.py", "dave"),
        ("path", "open ~/" + "Documents/private/plan.md", "Documents/private"),
        ("username", "run by " + "erin" + "x tool; path /home/erin" + "x/a/b", "erinx"),
    ]


class OldRulesStillCaughtTests(unittest.TestCase):
    def test_every_old_secret_is_masked(self):
        for rule, text, secret in _samples():
            with self.subTest(rule=rule, text=text[:30]):
                r = Redactor(use_os_user=False)
                out = r.redact(text)
                self.assertNotEqual(out, text)
                self.assertGreater(r.counts[rule], 0, (rule, out))
                if secret:
                    self.assertNotIn(secret, out)

    def test_custom_word_still_masked(self):
        r = Redactor(words=["Acme" + "Corp"], use_os_user=False)
        self.assertNotIn("AcmeCorp", r.redact("work at AcmeCorp today"))
        self.assertGreater(r.counts["custom_word"], 0)

    def test_old_rule_ids_still_exist(self):
        for rule in ("private_key", "jwt", "api_key", "bearer", "long_hex", "long_base64",
                     "secret_kv", "webhook", "url_userinfo", "url_query", "tailscale", "email",
                     "ipv6", "ipv4", "rrn", "card", "phone", "path", "username", "custom_word"):
            self.assertIn(rule, RULE_LABELS)


if __name__ == "__main__":
    unittest.main()
