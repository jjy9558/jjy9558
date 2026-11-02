import unittest

import helpers as H  # noqa: F401 - sys.path 설정
from helpers import ALNUM, B64URL, DIGITS, UPPER, rnd

from devguard import scanner
from devguard.scanner import Allowlist, mask_secret, scan_added_lines, scan_filename, scan_line


def rules(text, **kw):
    return [f.rule for f in scan_line(text, "x.py", 1, **kw)]


class TruePositives(unittest.TestCase):
    def test_anthropic(self):
        key = "sk-ant-" + "api03-" + rnd(80, B64URL)
        self.assertEqual(rules('KEY = "%s"' % key), ["anthropic-key"])

    def test_openai_variants(self):
        self.assertEqual(rules("k=" + "sk-" + "proj-" + rnd(48, B64URL, 2)), ["openai-key"])
        self.assertEqual(rules("OPENAI='" + "sk-" + rnd(48, seed=5) + "'"), ["openai-key"])

    def test_github(self):
        self.assertEqual(rules("t = " + "gh" + "p_" + rnd(36, seed=4)), ["github-token"])
        self.assertEqual(rules("t = " + "github" + "_pat_" + rnd(70, ALNUM + "_", 4)), ["github-fine-grained"])

    def test_slack_token_and_webhook(self):
        tok = "xox" + "b-" + rnd(12, DIGITS) + "-" + rnd(13, DIGITS, 2) + "-" + rnd(24, seed=2)
        self.assertEqual(rules("SLACK=" + tok), ["slack-token"])
        hook = "https://hooks.slack.com/services/T" + rnd(8, UPPER + DIGITS) + "/B" + rnd(8, UPPER + DIGITS, 2) + "/" + rnd(24, seed=3)
        self.assertEqual(rules("url: " + hook), ["slack-webhook"])

    def test_aws(self):
        self.assertEqual(rules("id = " + "AK" + "IA" + rnd(16, UPPER + DIGITS, 7)), ["aws-access-key-id"])

    def test_google(self):
        self.assertEqual(rules("key=" + "AI" + "za" + rnd(35, B64URL, 9)), ["google-api-key"])

    def test_jwt(self):
        self.assertEqual(rules("Authorization: Bearer " + H.fake_jwt()), ["jwt"])

    def test_private_key_header(self):
        for kind in ("RSA ", "EC ", "OPENSSH ", ""):
            line = "-----BEGIN " + kind + "PRIVATE" + " KEY-----"
            f = scan_line(line, "k.pem", 1)
            self.assertEqual([x.rule for x in f], ["private-key"], kind)

    def test_stripe(self):
        self.assertEqual(rules("sk_" + "live_" + rnd(30, seed=6)), ["stripe-live-key"])

    def test_url_credentials(self):
        self.assertEqual(rules("DATABASE_URL=postgres://admin:" + rnd(16, seed=8) + "@db.internal:5432/app"),
                         ["url-credentials"])

    def test_generic_assignments(self):
        cases = [
            'API_KEY = "%s"' % rnd(32, seed=11),
            "SECRET_TOKEN=%s" % rnd(40, seed=12),
            '"client_secret": "%s",' % rnd(30, seed=13),
            "export AWS_SECRET_ACCESS_KEY=%s" % rnd(40, ALNUM + "/+", 14),
            "password: Tr0ub4dor&3x",
            'DB_PASSWORD="Sup3r-S3cret-pw"',
            "authToken: %s" % rnd(32, seed=15),
            "api_key: str = '%s'" % rnd(32, seed=16),
        ]
        for c in cases:
            self.assertEqual(rules(c), ["generic-secret"], c)

    def test_specific_wins_over_generic_no_duplicate(self):
        key = "sk-ant-" + rnd(60, B64URL, 3)
        self.assertEqual(rules("ANTHROPIC_API_KEY=" + key), ["anthropic-key"])


class FalsePositiveGuards(unittest.TestCase):
    def none(self, text, **kw):
        self.assertEqual(rules(text, **kw), [], text)

    def test_uuid_and_hashes(self):
        self.none('api_key = "123e4567-e89b-12d3-a456-426614174000"')
        self.none('cache_key = "%s"' % rnd(40, "0123456789abcdef", 3))
        self.none('commit_token: %s' % rnd(40, "0123456789abcdef", 4))
        self.none('"integrity": "sha512-%s"' % rnd(86, ALNUM + "+/", 2))
        self.none('checksum_key = "%s"' % rnd(64, "0123456789abcdef", 5))

    def test_hex_secret_still_caught_when_name_is_not_hashy(self):
        self.assertEqual(rules('secret_key = "%s"' % rnd(64, "0123456789abcdef", 5)), ["generic-secret"])

    def test_lockfile_lines_skip_generic(self):
        lines = [(1, '      "integrity": "sha512-%s",' % rnd(86, ALNUM + "+/", 3)),
                 (2, '    resolved "https://registry.yarnpkg.com/x/-/x-1.0.0.tgz#%s"' % rnd(40, "0123456789abcdef", 3)),
                 (3, "  password_hash_key: %s" % rnd(48, seed=2))]
        for path in ("package-lock.json", "yarn.lock", "poetry.lock", "go.sum", "sub/pnpm-lock.yaml"):
            self.assertEqual(scan_added_lines({path: lines}, []), [], path)

    def test_placeholders(self):
        samples = [
            "API_KEY=your_api_key_here",
            "OPENAI_API_KEY=sk-...",
            "OPENAI_API_KEY=" + "sk-" + "x" * 40,
            "aws_access_key_id = AKIAIOSFODNN7EXAMPLE",
            "token = " + "gh" + "p_" + "x" * 36,
            'password = "changeme"',
            'password = "your-password-here"',
            "token: ${GITHUB_TOKEN}",
            "token: ${{ secrets.GITHUB_TOKEN }}",
            'api_key = os.environ["API_KEY"]',
            'api_key = os.getenv("API_KEY")',
            "password = getpass.getpass()",
            "secret = secrets.token_hex(32)",
            "api_key = <YOUR_API_KEY>",
            "SECRET_KEY=" + "0" * 32,
            "SLACK_WEBHOOK=https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
            "Authorization: Bearer " + "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
            "DATABASE_URL=postgres://user:password@localhost:5432/db",
            "DATABASE_URL=postgres://user:${DB_PASS}@localhost/db",
            "token = 'abcdefghijklmnopqrstuvwxyz1234567890'",
        ]
        for s in samples:
            self.none(s)

    def test_non_secret_names_and_values(self):
        for s in [
            "max_tokens = 4096",
            'token_type = "bearer"',
            'monkey = "banana12345678901234567"',
            'tokenizer = "bert-base-uncased-model-v2-xyz-123"',
            'if token == "abc123xyz456abc123xyz456":',
            'key = "application/json"',
            'secret_name = "prod/db/credentials-v2-abc123"',
            'password_file = "/etc/secrets/db_password_2"',
            'api_key = "i18n"',
            'key: some.translation.key.name.here',
            "TOKEN_ENV=GITHUB_ACCESS_TOKEN_2",
            'password = password_input',
            'url = "https://example.com/?key=abcdef1234567890abcdef"',
        ]:
            self.none(s)

    def test_sk_inside_words(self):
        for s in ["task-description-that-is-very-long-name-1",
                  "disk-usage-monitoring-service-config-v2",
                  "sk-circle-fade-in-out-animation-class",
                  "risk-assessment-framework-document-2024"]:
            self.none(s)

    def test_inline_allow_marker(self):
        key = "sk-ant-" + rnd(60, B64URL)
        self.none('K = "%s"  # devguard:allow' % key)
        self.none('K = "%s"  // devguard:allow' % key)

    def test_disabled_rule(self):
        key = "sk-ant-" + rnd(60, B64URL)
        self.none("K=" + key, disabled=frozenset(["anthropic-key", "generic-secret", "openai-key"]))


class FileNames(unittest.TestCase):
    def test_env_files_blocked(self):
        for n in (".env", "app/.env", ".env.local", ".env.production", "config/prod.env", "C:\\x\\.env"):
            self.assertIsNotNone(scan_filename(n), n)

    def test_env_examples_ok(self):
        for n in (".env.example", ".env.sample", ".env.template", "svc/.env.dist", ".env.defaults"):
            self.assertIsNone(scan_filename(n), n)

    def test_key_files(self):
        for n in ("id_rsa", "~/.ssh/id_ed25519", "cert.pfx", "store.p12", ".netrc"):
            self.assertIsNotNone(scan_filename(n), n)
        self.assertIsNone(scan_filename("id_rsa.pub"))
        self.assertIsNone(scan_filename("README.md"))
        self.assertIsNone(scan_filename("environment.py"))


class Masking(unittest.TestCase):
    def test_never_full(self):
        for n in (1, 2, 4, 8, 9, 12, 20, 40, 100):
            s = rnd(n, seed=n)
            m = mask_secret(s)
            self.assertNotEqual(m, s)
            if n > 8:
                self.assertNotIn(s[n // 4:-(n // 8 or 1)], m)
                self.assertLessEqual(len(m) - 4, n // 2)

    def test_shape(self):
        key = "sk-ant-api03-" + rnd(60)
        m = mask_secret(key)
        self.assertTrue(m.startswith("sk-ant"))
        self.assertIn("****", m)
        self.assertNotIn(key[10:-5], m)

    def test_report_has_no_full_secret(self):
        key = "ghp_" + rnd(36, seed=21)
        findings = scan_line("t=" + key, "a.py", 3)
        text = scanner.format_report(findings)
        self.assertNotIn(key, text)
        self.assertIn("a.py:3", text)
        self.assertIn("도둑 경보", text)


class AllowlistTests(unittest.TestCase):
    def test_parse_and_match(self):
        al = Allowlist.parse("""
        # comment
        tests/fixtures/
        *.sample.json   # trailing comment
        docs/keys.md
        rule:openai-key
        line:EXAMPLE_ONLY
        """)
        self.assertTrue(al.path_allowed("tests/fixtures/a/b.txt"))
        self.assertTrue(al.path_allowed("x/tests/fixtures/a.txt"))
        self.assertTrue(al.path_allowed("deep/dir/foo.sample.json"))
        self.assertTrue(al.path_allowed("docs/keys.md"))
        self.assertFalse(al.path_allowed("src/keys.md"))
        self.assertIn("openai-key", al.rules)
        self.assertTrue(al.line_allowed("a EXAMPLE_ONLY b"))

    def test_allowlist_applies(self):
        key = "ghp_" + rnd(36, seed=2)
        added = {"tests/fixtures/t.py": [(1, "t=" + key)], "src/a.py": [(1, "t=" + key)]}
        found = scan_added_lines(added, [], Allowlist.parse("tests/fixtures/"))
        self.assertEqual([f.path for f in found], ["src/a.py"])
        found = scan_added_lines(added, [], Allowlist.parse("rule:github-token"))
        self.assertEqual(found, [])
        found = scan_added_lines(added, [], Allowlist.parse("line:ghp_"))
        self.assertEqual(found, [])

    def test_env_file_allowlisted(self):
        self.assertEqual(scan_added_lines({}, [".env"], Allowlist.parse(".env")), [])
        self.assertEqual(len(scan_added_lines({}, [".env"], Allowlist())), 1)


class DiffParsing(unittest.TestCase):
    DIFF = (
        "diff --git a/src/a.py b/src/a.py\n"
        "index 111..222 100644\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -10,2 +10,3 @@ def f():\n"
        "-old secret line\n"
        "+new line one\n"
        "+new line two\n"
        "@@ -50,0 +52,1 @@\n"
        "+++ looks like header but is content\n"
        "diff --git a/gone.txt b/gone.txt\n"
        "deleted file mode 100644\n"
        "--- a/gone.txt\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-deleted\n"
        "diff --git a/한글 파일.txt b/한글 파일.txt\n"
        "--- /dev/null\n"
        "+++ b/한글 파일.txt\t\n"
        "@@ -0,0 +1 @@\n"
        "+안녕\r\n"
    )

    def test_parse(self):
        got = scanner.parse_diff_added_lines(self.DIFF)
        self.assertEqual(got["src/a.py"], [(10, "new line one"), (11, "new line two"),
                                           (52, "++ looks like header but is content")])
        self.assertNotIn("gone.txt", got)
        self.assertEqual(got["한글 파일.txt"], [(1, "안녕")])


if __name__ == "__main__":
    unittest.main()
