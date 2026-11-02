import socket
import unittest
import urllib.error

from cool_off.notify import Notifier, NotifyError
from tests.fakes import FakeOpener, network_down


class NotifierTests(unittest.TestCase):
    def test_posts_utf8_body_to_topic_url(self):
        opener = FakeOpener()
        ok, detail = Notifier("cool-off-abc123", opener=opener).send("[쿨링오프 끝] 에어팟 프로 359,000원")
        self.assertTrue(ok)
        self.assertEqual(len(opener.requests), 1)
        req = opener.requests[0]
        self.assertEqual(req["url"], "https://ntfy.sh/cool-off-abc123")
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["body"], "[쿨링오프 끝] 에어팟 프로 359,000원")
        self.assertEqual(req["headers"]["Title"], "cool-off")
        self.assertEqual(req["timeout"], 10.0)

    def test_network_failure_is_reported_not_raised(self):
        ok, detail = Notifier("topic1", opener=FakeOpener(error=network_down())).send("hi")
        self.assertFalse(ok)
        self.assertIn("네트워크 오류", detail)

    def test_timeout_is_reported(self):
        ok, detail = Notifier("topic1", opener=FakeOpener(error=socket.timeout("timed out"))).send("hi")
        self.assertFalse(ok)
        self.assertIn("timed out", detail)

    def test_http_error_is_reported_with_code(self):
        error = urllib.error.HTTPError("https://ntfy.sh/topic1", 429, "Too Many", {}, None)
        ok, detail = Notifier("topic1", opener=FakeOpener(error=error)).send("hi")
        self.assertFalse(ok)
        self.assertEqual(detail, "HTTP 429")

    def test_non_2xx_status_is_failure(self):
        ok, detail = Notifier("topic1", opener=FakeOpener(status=503)).send("hi")
        self.assertFalse(ok)
        self.assertEqual(detail, "HTTP 503")

    def test_invalid_topic_rejected(self):
        for bad in ("", "a b", "주제", "a/b", "x" * 65):
            with self.subTest(topic=bad):
                with self.assertRaises(NotifyError):
                    Notifier(bad, opener=FakeOpener())


if __name__ == "__main__":
    unittest.main()
