import base64
import email.header
import io
import unittest

from jjkit import notify
from jjkit.notify import Notifier, NotifyError, encode_header
from tests.fakes import FakeOpener


def decode_rfc2047(value):
    return str(email.header.make_header(email.header.decode_header(value)))


class EncodeHeaderTests(unittest.TestCase):
    def test_ascii_passthrough(self):
        self.assertEqual(encode_header("Refund soon"), "Refund soon")

    def test_korean_roundtrip(self):
        enc = encode_header("환불 기한 임박")
        enc.encode("ascii")
        self.assertTrue(enc.startswith("=?UTF-8?B?"))
        self.assertEqual(decode_rfc2047(enc), "환불 기한 임박")

    def test_long_title_split_without_breaking_chars(self):
        title = "가나다라마바사아자차카타파하" * 4
        enc = encode_header(title)
        words = enc.split(" ")
        self.assertGreater(len(words), 1)
        for w in words:
            self.assertLessEqual(len(w), 75)
            base64.b64decode(w[10:-2]).decode("utf-8")  # 조각마다 온전한 UTF-8
        self.assertEqual(decode_rfc2047(enc), title)

    def test_newlines_removed(self):
        self.assertEqual(encode_header("a\nb"), "a b")


class NtfyTests(unittest.TestCase):
    def test_json_mode_default(self):
        fake = FakeOpener({"id": "x"})
        tok = "tk_" + "z" * 8
        st = notify.ntfy("mytopic", "본문입니다", title="한글 제목", priority="high",
                         tags=["warning", "게임"], click="https://example.com", token=tok, opener=fake)
        self.assertEqual(st, 200)
        self.assertEqual(fake.last.full_url, "https://ntfy.sh/")
        self.assertEqual(fake.last_json(), {"topic": "mytopic", "message": "본문입니다", "title": "한글 제목",
                                            "priority": 4, "tags": ["warning", "게임"],
                                            "click": "https://example.com"})
        self.assertEqual(fake.header("Authorization"), "Bearer " + tok)

    def test_headers_mode(self):
        fake = FakeOpener(b"")
        notify.ntfy("t", "메시지", title="제목", priority=5, tags=["a"], server="http://127.0.0.1:81/",
                    mode="headers", opener=fake)
        req = fake.last
        self.assertEqual(req.full_url, "http://127.0.0.1:81/t")
        self.assertEqual(req.data.decode("utf-8"), "메시지")
        self.assertEqual(decode_rfc2047(fake.header("Title")), "제목")
        self.assertEqual(fake.header("Priority"), "5")
        self.assertEqual(fake.header("Tags"), "a")

    def test_bad_inputs(self):
        with self.assertRaises(NotifyError):
            notify.ntfy("", "x", opener=FakeOpener())
        with self.assertRaises(NotifyError):
            notify.ntfy("t", "x", priority="loud", opener=FakeOpener())
        with self.assertRaises(NotifyError):
            notify.ntfy("t", "x", mode="smoke", opener=FakeOpener())

    def test_priority_clamped(self):
        fake = FakeOpener({})
        notify.ntfy("t", "x", priority=9, opener=fake)
        self.assertEqual(fake.last_json()["priority"], 5)

    def test_http_error(self):
        with self.assertRaises(NotifyError):
            notify.ntfy("t", "x", opener=FakeOpener((403, {"error": "no"})))


class SayTests(unittest.TestCase):
    def test_sidekick_say(self):
        fake = FakeOpener({"ok": True, "id": 3, "text": "안녕"})
        tok = "s" * 12
        r = notify.sidekick_say(" 안녕 ", token=tok, module="mbrief", opener=fake)
        self.assertEqual(r["id"], 3)
        self.assertEqual(fake.last.full_url, "http://127.0.0.1:8765/api/say")
        self.assertEqual(fake.header("X-Sidekick-Token"), tok)
        self.assertEqual(fake.last_json(), {"text": "안녕", "module": "mbrief"})

    def test_sidekick_empty_and_429(self):
        with self.assertRaises(NotifyError):
            notify.sidekick_say("  ", token="x", opener=FakeOpener())
        with self.assertRaises(NotifyError):
            notify.sidekick_say("hi", token="x", opener=FakeOpener((429, {"error": "too many"})))

    def test_jarvis_notify(self):
        fake = FakeOpener({"job": {"id": 7}})
        r = notify.jarvis_notify("빌드 끝", device="pc", token="j" * 10, caps=["speaker"], opener=fake)
        self.assertEqual(r["job"]["id"], 7)
        self.assertEqual(fake.last.full_url, "http://127.0.0.1:8800/api/jobs")
        self.assertEqual(fake.header("X-Jarvis-Device"), "pc")
        body = fake.last_json()
        self.assertEqual(body["type"], "notify.say")
        self.assertEqual(body["payload"], {"text": "빌드 끝", "module": "jjkit"})
        self.assertEqual(body["caps"], ["speaker"])


class NotifierTests(unittest.TestCase):
    def test_channels_and_fanout(self):
        fake = FakeOpener({})
        out = io.StringIO()
        n = Notifier({"ntfy": {"topic": "t"}, "sidekick": {"token": "s" * 8},
                      "jarvis": {"token": "", "device": "pc"},  # 토큰 없음 -> 꺼짐
                      "console": True}, opener=fake, stream=out)
        self.assertEqual(n.channels(), ["ntfy", "sidekick", "console"])
        res = n.send("아침 브리핑", "오늘 일정 2개")
        self.assertEqual(res, {"ntfy": "ok", "sidekick": "ok", "console": "ok"})
        self.assertIn("[알림] 아침 브리핑", out.getvalue())
        urls = [r.full_url for r, _ in fake.requests]
        self.assertEqual(urls, ["https://ntfy.sh/", "http://127.0.0.1:8765/api/say"])

    def test_failure_does_not_raise(self):
        n = Notifier({"ntfy": {"topic": "t"}, "console": {"enabled": False}},
                     opener=FakeOpener(OSError("down")))
        res = n.send("x")
        self.assertTrue(res["ntfy"].startswith("error"))
        self.assertNotIn("console", res)

    def test_speak_override(self):
        fake = FakeOpener({})
        Notifier({"sidekick": {"token": "a" * 8}}, opener=fake).send("제목", "본문", speak="짧게 말하기")
        self.assertEqual(fake.last_json()["text"], "짧게 말하기")

    def test_empty_config(self):
        self.assertEqual(Notifier().send("x"), {})


if __name__ == "__main__":
    unittest.main()
