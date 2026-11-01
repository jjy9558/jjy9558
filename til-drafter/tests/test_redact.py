# -*- coding: utf-8 -*-
import unittest

import helpers  # noqa: F401  (sys.path 설정)
from til_drafter.redact import Redactor, RULE_LABELS, luhn_ok


def mk(**kw):
    kw.setdefault("use_os_user", False)
    return Redactor(**kw)


class PathTests(unittest.TestCase):
    def setUp(self):
        self.r = mk()

    def test_windows_path_keeps_last_component(self):
        out = self.r.redact(r"오류 파일: C:\Users\kimmin\Projects\blog\src\app.py 에서 발생")
        self.assertIn("<경로>/app.py", out)
        self.assertNotIn("kimmin", out)
        self.assertNotIn("Projects", out)

    def test_windows_other_drive_and_forward_slashes(self):
        self.assertEqual(self.r.redact(r"F:\work\notes\todo.md"), "<경로>/todo.md")
        self.assertEqual(self.r.redact("D:/data/x/report.csv"), "<경로>/report.csv")

    def test_windows_json_escaped_backslashes(self):
        out = self.r.redact("C:\\\\Users\\\\hong\\\\proj\\\\main.ts")
        self.assertEqual(out, "<경로>/main.ts")

    def test_windows_home_dir_itself(self):
        out = self.r.redact(r"cd C:\Users\hong 후 실행")
        self.assertNotIn("hong", out)
        self.assertIn("<경로>", out)

    def test_posix_paths(self):
        self.assertEqual(self.r.redact("/home/alice/code/proj/main.py"), "<경로>/main.py")
        self.assertEqual(self.r.redact("/Users/bob/Library/foo.txt"), "<경로>/foo.txt")
        self.assertEqual(self.r.redact("열기: /home/alice/x.py."), "열기: <경로>/x.py.")

    def test_posix_home_dir_hides_username(self):
        out = self.r.redact("홈은 /Users/bobby 입니다")
        self.assertNotIn("bobby", out)

    def test_trailing_punctuation_preserved(self):
        self.assertEqual(self.r.redact("(see /home/alice/a/b.py)"), "(see <경로>/b.py)")

    def test_tilde_and_env_paths(self):
        self.assertEqual(self.r.redact("~/.ssh/config"), "<경로>/config")
        self.assertEqual(self.r.redact(r"%USERPROFILE%\Desktop\memo.txt"), "<경로>/memo.txt")

    def test_unc_path(self):
        out = self.r.redact(r"\\fileserver\share\team\plan.xlsx")
        self.assertEqual(out, "<경로>/plan.xlsx")

    def test_relative_and_plain_slashes_untouched(self):
        for s in ["src/components/App.tsx", "and/or", "a/b/c", "TCP/IP 스택", "w/o 설정",
                  "/api/v1/users", "docs/home/index.md", "homework/1"]:
            self.assertEqual(self.r.redact(s), s, s)

    def test_path_in_url_not_treated_as_posix_path(self):
        out = self.r.redact("https://example.com/home/guide")
        self.assertEqual(out, "https://example.com/home/guide")

    def test_korean_text_attached_before_path(self):
        out = self.r.redact("파일/home/alice/proj/x.py 수정")
        self.assertIn("<경로>/x.py", out)


class UsernameTests(unittest.TestCase):
    def test_username_learned_from_path_then_hidden_everywhere(self):
        r = mk()
        r.learn(r"C:\Users\ghost_writer\AppData\x")
        out = r.redact("ghost_writer 계정으로 로그인, Ghost_Writer님")
        self.assertNotIn("ghost_writer", out.lower())

    def test_username_in_same_text_as_path(self):
        r = mk()
        out = r.redact(r"C:\Users\zeta77\a.txt 는 zeta77 의 파일")
        self.assertNotIn("zeta77", out)

    def test_explicit_username(self):
        r = mk(usernames=["jiyoung_k"])
        self.assertNotIn("jiyoung_k", r.redact("hello jiyoung_k!"))

    def test_username_requires_ascii_word_boundary(self):
        r = mk(usernames=["alex"])
        self.assertEqual(r.redact("alexander and alex_2"), "alexander and alex_2")
        self.assertEqual(r.redact("alex가 했다"), "<사용자>가 했다")

    def test_generic_names_not_learned(self):
        r = mk()
        r.learn("/home/user/project /Users/Public/x C:\\Users\\Administrator\\y")
        self.assertEqual(r.usernames, [])
        self.assertEqual(r.redact("the user asked the admin"), "the user asked the admin")

    def test_os_user_included_by_default(self):
        import os
        old = os.environ.get("USERNAME")
        os.environ["USERNAME"] = "pcowner88"
        try:
            r = Redactor()
            self.assertIn("pcowner88", r.usernames)
            self.assertNotIn("pcowner88", r.redact("hi pcowner88"))
        finally:
            if old is None:
                del os.environ["USERNAME"]
            else:
                os.environ["USERNAME"] = old

    def test_custom_words(self):
        r = mk(words=["우리회사", "ProjectPhoenix"])
        out = r.redact("우리회사의 projectphoenix 일정")
        self.assertNotIn("우리회사", out)
        self.assertNotIn("hoenix", out)
        self.assertEqual(r.counts["custom_word"], 2)

    def test_korean_username_in_path(self):
        r = mk()
        out = r.redact(r"C:\Users\홍길동\문서\메모.txt 와 홍길동 님")
        self.assertNotIn("홍길동", out)


class ContactTests(unittest.TestCase):
    def setUp(self):
        self.r = mk()

    def test_email(self):
        out = self.r.redact("문의: hong.gildong+tag@example.co.kr 로 보내줘")
        self.assertEqual(out, "문의: <이메일> 로 보내줘")

    def test_email_lookalikes_kept(self):
        for s in ["react@18.2.0 설치", "git@github.com:owner/repo.git", "@types/node", "@property"]:
            self.assertEqual(self.r.redact(s), s, s)

    def test_ipv4(self):
        self.assertEqual(self.r.redact("서버 192.168.0.12:8080 접속"), "서버 <IP>:8080 접속")
        self.assertEqual(self.r.redact("tailscale 100.101.102.103."), "tailscale <IP>.")

    def test_ipv4_non_ip_numbers_kept(self):
        for s in ["버전 10.0.19045.3693", "1.2.3", "v3.9.1", "999.1.1.1", "1.2.3.4.5"]:
            self.assertEqual(self.r.redact(s), s, s)

    def test_ipv6(self):
        self.assertEqual(self.r.redact("addr fe80::1ff:fe23:4567:890a ok"), "addr <IP> ok")
        self.assertEqual(self.r.redact("2001:db8::8a2e:370:7334"), "<IP>")
        self.assertEqual(self.r.redact("2001:0db8:85a3:0000:0000:8a2e:0370:7334"), "<IP>")
        self.assertEqual(self.r.redact("[fe80::dead:beef]:443"), "[<IP>]:443")

    def test_ipv6_lookalikes_kept(self):
        for s in ["a[1::2]", "x[::-1]", "std::vector<int>", "Foo::bar", "시각 12:30:45", "aa:bb:cc:dd:ee:ff",
                  "[::1]", "C++ ::add"]:
            self.assertEqual(self.r.redact(s), s, s)

    def test_tailscale_hostname(self):
        out = self.r.redact("ssh my-pc.tail1a2b3.ts.net 접속, http://box.tail9.ts.net:3000/x")
        self.assertNotIn("ts.net", out)
        self.assertNotIn("my-pc", out)
        self.assertEqual(self.r.counts["tailscale"], 2)

    def test_phone_numbers(self):
        for s in ["010-1234-5678", "01012345678", "010.1234.5678", "010 1234 5678", "+82 10-1234-5678",
                  "+82-10-9876-5432", "02-123-4567", "031-1234-5678", "070-1234-5678"]:
            self.assertEqual(self.r.redact("연락처 %s 입니다" % s), "연락처 <전화번호> 입니다", s)

    def test_phone_lookalikes_kept(self):
        for s in ["2026-11-01", "123-4567", "port 3000", "0.5 초", "build 20261101"]:
            self.assertEqual(self.r.redact(s), s, s)

    def test_card_numbers(self):
        self.assertEqual(self.r.redact("카드 1234-5678-9012-3456"), "카드 <카드번호>")
        self.assertEqual(self.r.redact("1234 5678 9012 3456"), "<카드번호>")
        self.assertEqual(self.r.redact("3782 822463 10005"), "<카드번호>")
        self.assertEqual(self.r.redact("4111111111111111"), "<카드번호>")  # Luhn 통과

    def test_card_lookalikes_kept(self):
        for s in ["id 1234567890123456", "1730000000000", "123e4567-e89b-12d3-a456-426614174000"]:
            self.assertEqual(self.r.redact(s), s, s)
        self.assertTrue(luhn_ok("4111111111111111"))
        self.assertFalse(luhn_ok("1234567890123456"))

    def test_resident_registration_number(self):
        self.assertEqual(self.r.redact("주민번호 900101-1234567"), "주민번호 <주민번호>")


class SecretTests(unittest.TestCase):
    def setUp(self):
        self.r = mk()

    def assertGone(self, secret, text=None):
        out = self.r.redact(text if text is not None else "값은 %s 입니다" % secret)
        self.assertNotIn(secret, out)
        return out

    def test_openai_and_anthropic_keys(self):
        self.assertGone("sk-" + "a1B2c3D4e5F6g7H8i9J0")
        self.assertGone("sk-ant-api03-" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8_-Qr9")
        self.assertGone("sk-proj-" + "x9Y8z7W6v5U4t3S2r1Q0")

    def test_github_tokens(self):
        self.assertGone("ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")
        self.assertGone("gho_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")
        self.assertGone("github_pat_" + "11ABCDEFG0aBcDeFgHiJkL_mNoPqRsTuVwXyZ0123456789abcdef")

    def test_slack_aws_google(self):
        self.assertGone("xoxb-" + "1234567890-0987654321-AbCdEfGhIjKlMnOp")
        self.assertGone("xoxp-" + "1234567890-1234567890-1234567890-abcdef")
        self.assertGone("AKIA" + "IOSFODNN7EXAMPLE")
        self.assertGone("AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q")

    def test_bearer(self):
        out = self.r.redact("Authorization: Bearer abc123def456ghi789")
        self.assertEqual(out, "Authorization: Bearer <토큰>")
        out = self.r.redact('curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc_DEF-123"')
        self.assertNotIn("eyJ", out)
        self.assertIn("Bearer <토큰>", out)

    def test_bearer_plain_words_kept(self):
        self.assertEqual(self.r.redact("Bearer authentication 방식"), "Bearer authentication 방식")
        self.assertEqual(self.r.redact("Bearer token을 쓴다"), "Bearer token을 쓴다")

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        self.assertEqual(self.r.redact("jwt=" + jwt).count("eyJ"), 0)

    def test_long_hex(self):
        h = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
        out = self.r.redact("sha256 %s 확인" % h)
        self.assertNotIn(h, out)
        self.assertEqual(self.r.counts["long_hex"], 1)

    def test_short_hex_kept(self):
        for s in ["커밋 a1b2c3d", "color #ff00ff", "0xDEADBEEF", "deadbeefcafe1234"]:
            self.assertEqual(self.r.redact(s), s, s)

    def test_long_base64(self):
        b = "dGhpcyBpcyBhIHZlcnkgc2VjcmV0IHZhbHVlIQ8xYz0AbCdEfGh"
        out = self.r.redact("data: " + b)
        self.assertNotIn(b, out)
        b2 = "Zm9vYmFyMTIzNDU2Nzg5MEFCQ0RFRkdISUpLTE1OT1BRUlNUVVZXWFla=="
        self.assertNotIn(b2, self.r.redact(b2))

    def test_long_words_not_over_redacted(self):
        for s in ["this-is-a-very-long-kebab-case-slug-name-for-a-blog-post",
                  "Some-Long-Title-For-Blog-Post-2026-Draft-Version",
                  "a_very_long_snake_case_identifier_with_digits_123",
                  "supercalifragilisticexpialidocious",
                  "pneumonoultramicroscopicsilicovolcanoconiosis"]:
            self.assertEqual(self.r.redact(s), s, s)

    def test_private_key_block(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234\nabcd\n-----END RSA PRIVATE KEY-----"
        out = self.r.redact("키:\n" + pem + "\n끝")
        self.assertNotIn("MIIE", out)
        self.assertIn("끝", out)

    def test_key_value_assignments(self):
        out = self.r.redact("export OPENAI_API_KEY=abc123xyz789 && run")
        self.assertEqual(out, "export OPENAI_API_KEY=<값> && run")
        out = self.r.redact('{"password": "hunter2hunter2", "user": "x"}')
        self.assertNotIn("hunter2", out)
        out = self.r.redact("DB_PASSWORD: s3cretvalue")
        self.assertNotIn("s3cretvalue", out)
        out = self.r.redact("client_secret=abcDEF123456")
        self.assertNotIn("abcDEF123456", out)

    def test_key_value_code_not_redacted(self):
        for s in ["password = getpass.getpass()", "token = tokenizer.next()", "token: str",
                  "secret = os.environ.get", "max_tokens=4096", "api_key=$API_KEY",
                  "password=${DB_PASS}", "token=<your-token>", "input_tokens: 1234567", "pwd = os.getcwd()"]:
            self.assertEqual(self.r.redact(s), s, s)

    def test_url_with_secrets(self):
        out = self.r.redact("GET https://api.example.com/v1/items?api_key=abcd1234&page=2 실패")
        self.assertEqual(out, "GET https://api.example.com/v1/items?api_key=<값>&page=<값> 실패")
        out = self.r.redact("https://user:p4ss@git.example.com/repo.git")
        self.assertEqual(out, "https://git.example.com/repo.git")
        out = self.r.redact("callback http://localhost:3000/cb#access_token=zzzTOKENzzz.")
        self.assertEqual(out, "callback http://localhost:3000/cb.")

    def test_webhook_url(self):
        out = self.r.redact("https://hooks.slack.com/services/T000/B000/abcdefghijklmnop")
        self.assertEqual(out, "<웹훅URL>")

    def test_plain_urls_kept(self):
        for s in ["https://docs.python.org/3/library/re.html", "http://localhost:3000/api",
                  "https://example.com/a#section-2"]:
            self.assertEqual(self.r.redact(s), s, s)


class NoOverRedactionTests(unittest.TestCase):
    def test_normal_text_and_code_unchanged(self):
        r = mk()
        samples = [
            "list[0] 대신 list[-1] 을 쓰면 마지막 원소를 얻는다",
            "for i in range(10): print(i)",
            "x = foo.bar(baz)[2]",
            "dict.get('key', None) 와 collections.Counter",
            "2026-11-01 23:30 에 배포, 버전 v1.2.3",
            "이슈 #123 과 PR 45 병합, 50% 완료, 3.14 근사",
            "SELECT * FROM users WHERE id = 5;",
            "https://github.com/anthropics/claude-code 참고",
            "정규식 ^\\d+$ 는 숫자만 매칭한다",
            "프로젝트 til-drafter 의 README.md 수정",
            "함수 getUserById 는 user 를 반환",
            "TIL: 파이썬에서 a[1:3] 슬라이스는 끝 인덱스를 포함하지 않는다",
            "오류 코드 E1234, 응답 시간 120ms, 포트 8080",
        ]
        for s in samples:
            self.assertEqual(r.redact(s), s, s)
        self.assertEqual(sum(r.counts.values()), 0)


class CountsTests(unittest.TestCase):
    def test_counts_hold_rules_not_secrets(self):
        r = mk()
        secret = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
        r.redact("token %s mail a@b.com again a@b.com" % secret)
        self.assertEqual(r.counts["api_key"], 1)
        self.assertEqual(r.counts["email"], 2)
        blob = repr(dict(r.counts)) + repr(r.summary())
        self.assertNotIn(secret, blob)
        self.assertNotIn("a@b.com", blob)

    def test_all_rule_ids_have_labels(self):
        r = mk(words=["secretword"], usernames=["someuser"])
        r.redact("x")
        for rule in r.counts:
            self.assertIn(rule, RULE_LABELS)

    def test_idempotent(self):
        r = mk()
        once = r.redact(r"C:\Users\kim\a.py 010-1234-5678 a@b.com 10.0.0.1 https://x.com/?a=1")
        twice = r.redact(once)
        self.assertEqual(once, twice)

    def test_non_string_and_empty(self):
        r = mk()
        self.assertEqual(r.redact(""), "")
        self.assertIsNone(r.redact(None))


if __name__ == "__main__":
    unittest.main()
