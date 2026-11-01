import unittest

from helpers import fixture_path, read_fixture  # noqa: F401  (sets sys.path)
from kakao_wrapped import parser
from kakao_wrapped.parser import (
    DELETED, EMOTICON, FILE, PHOTO, SYSTEM, TEXT, VIDEO, FormatError,
)

# the same conversation exported in every format
CANONICAL = [
    ("2025-12-31 23:58", "철수", TEXT, 1, "올해도 고생했어"),
    ("2026-01-03 15:05", "철수", TEXT, 1, "안녕 영희야 ㅋㅋㅋ"),
    ("2026-01-03 15:06", "영희", TEXT, 1, "안녕!\n오늘 뭐해?"),
    ("2026-01-03 15:08", "민수", PHOTO, 1, "사진"),
    ("2026-01-03 15:09", "민수", PHOTO, 3, "사진 3장"),
    ("2026-01-03 15:10", "영희", EMOTICON, 1, "이모티콘"),
    ("2026-01-03 15:11", "철수", DELETED, 1, "삭제된 메시지입니다."),
    ("2026-01-03 15:12", "철수", TEXT, 1, "https://www.youtube.com/watch?v=abc 이거 봐"),
    ("2026-01-04 00:30", "영희", TEXT, 1, "새벽이다ㅋㅋㅋㅋㅋ"),
    ("2026-01-04 12:05", "민수", TEXT, 1, "점심 먹자"),
    ("2026-01-04 12:06", "철수", VIDEO, 1, "동영상"),
    ("2026-01-04 12:07", "영희", FILE, 1, "파일: 보고서.pdf"),
]


def summarize(result):
    return [
        (m.ts.strftime("%Y-%m-%d %H:%M"), m.user, m.kind, m.count, m.text)
        for m in result.messages
        if m.kind != SYSTEM
    ]


class FormatDetection(unittest.TestCase):
    def detect(self, name):
        return parser.detect_format(parser.decode_bytes(read_fixture(name)))

    def test_each_variant(self):
        self.assertEqual(self.detect("pc.txt"), parser.FMT_PC)
        self.assertEqual(self.detect("android.txt"), parser.FMT_ANDROID)
        self.assertEqual(self.detect("ios.txt"), parser.FMT_IOS)
        self.assertEqual(self.detect("ios_24h.txt"), parser.FMT_IOS)
        self.assertEqual(self.detect("mobile.csv"), parser.FMT_CSV)
        self.assertEqual(self.detect("english.txt"), parser.FMT_ENGLISH)
        self.assertEqual(self.detect("pc_english.txt"), parser.FMT_PC)

    def test_unknown_format(self):
        with self.assertRaises(FormatError):
            parser.detect_format("그냥 아무 텍스트\n여러 줄\n")


class CanonicalConversation(unittest.TestCase):
    """pc / android / ios / csv must all yield the identical message list."""

    def check(self, name, fmt):
        res = parser.parse_file(fixture_path(name))
        self.assertEqual(res.fmt, fmt)
        self.assertEqual(summarize(res), CANONICAL)
        self.assertEqual(res.system_count, 1)
        return res

    def test_pc(self):
        res = self.check("pc.txt", parser.FMT_PC)
        self.assertIsNone(res.title)  # "카카오톡 대화" has no room name

    def test_android(self):
        res = self.check("android.txt", parser.FMT_ANDROID)
        self.assertEqual(res.title, "영희")

    def test_ios(self):
        self.check("ios.txt", parser.FMT_IOS)

    def test_csv(self):
        self.check("mobile.csv", parser.FMT_CSV)


class MultilineAndSystem(unittest.TestCase):
    def test_multiline_attaches_to_previous(self):
        for name in ("pc.txt", "android.txt", "ios.txt", "mobile.csv"):
            res = parser.parse_file(fixture_path(name))
            multi = [m for m in res.messages if m.user == "영희" and m.text.startswith("안녕!")]
            self.assertEqual(len(multi), 1, name)
            self.assertEqual(multi[0].text, "안녕!\n오늘 뭐해?", name)

    def test_system_lines_are_separate(self):
        for name in ("pc.txt", "android.txt", "ios.txt", "mobile.csv"):
            res = parser.parse_file(fixture_path(name))
            system = [m for m in res.messages if m.kind == SYSTEM]
            self.assertEqual(len(system), 1, name)
            self.assertIn("들어왔습니다", system[0].text)
            self.assertEqual(system[0].user, "")
            # the system line must not be glued to the previous message
            prev = [m for m in res.messages if m.user == "영희"][0]
            self.assertNotIn("들어왔습니다", prev.text, name)

    def test_leave_invite_variants_are_system(self):
        text = (
            "2026년 1월 3일 토요일\n"
            "2026년 1월 3일 오후 3:00, 철수 : 안녕\n"
            "철수님이 영희님을 초대했습니다.\n"
            "2026년 1월 3일 오후 3:01: 영희님이 나갔습니다.\n"
            "2026년 1월 3일 오후 3:02, 민수 : 잘 가\n"
        )
        res = parser.parse_export(text)
        self.assertEqual(res.system_count, 2)
        self.assertEqual([m.text for m in res.messages if m.kind != SYSTEM], ["안녕", "잘 가"])


class MediaClassification(unittest.TestCase):
    def test_placeholders(self):
        c = parser.classify_text
        self.assertEqual(c("사진"), (PHOTO, 1))
        self.assertEqual(c("사진 12장"), (PHOTO, 12))
        self.assertEqual(c("(사진)"), (PHOTO, 1))
        self.assertEqual(c("동영상"), (VIDEO, 1))
        self.assertEqual(c("이모티콘"), (EMOTICON, 1))
        self.assertEqual(c("(이모티콘)"), (EMOTICON, 1))
        self.assertEqual(c("파일: 숙제.hwp"), (FILE, 1))
        self.assertEqual(c("삭제된 메시지입니다."), (DELETED, 1))
        self.assertEqual(c("Photo 3"), (PHOTO, 3))
        self.assertEqual(c("Emoticon"), (EMOTICON, 1))

    def test_real_text_is_not_media(self):
        c = parser.classify_text
        self.assertEqual(c("사진 잘 나왔다")[0], TEXT)
        self.assertEqual(c("어제 찍은 사진 보내줄게")[0], TEXT)
        self.assertEqual(c("이모티콘 샀어?")[0], TEXT)
        self.assertEqual(c("사진\n두 줄")[0], TEXT)

    def test_counted_as_media_in_fixture(self):
        res = parser.parse_file(fixture_path("android.txt"))
        kinds = [m.kind for m in res.messages]
        self.assertEqual(kinds.count(PHOTO), 2)
        self.assertEqual(sum(m.count for m in res.messages if m.kind == PHOTO), 4)
        self.assertEqual(kinds.count(EMOTICON), 1)
        self.assertEqual(kinds.count(VIDEO), 1)
        self.assertEqual(kinds.count(FILE), 1)
        self.assertEqual(kinds.count(DELETED), 1)


class OtherVariants(unittest.TestCase):
    def test_ios_24h(self):
        res = parser.parse_file(fixture_path("ios_24h.txt"))
        self.assertEqual(
            [(m.ts.hour, m.ts.minute, m.user) for m in res.messages],
            [(15, 5, "철수"), (15, 6, "영희"), (15, 7, "영희")],
        )

    def test_english_mobile(self):
        res = parser.parse_file(fixture_path("english.txt"))
        self.assertEqual(res.fmt, parser.FMT_ENGLISH)
        self.assertEqual(res.title, "Younghee")
        msgs = [m for m in res.messages if m.kind != SYSTEM]
        self.assertEqual(msgs[0].ts.hour, 15)
        self.assertEqual(msgs[1].text, "hi!\nsecond line of hi")
        self.assertEqual([m.kind for m in msgs][2:5], [PHOTO, PHOTO, EMOTICON])
        self.assertEqual(msgs[3].count, 3)
        self.assertEqual((msgs[-1].ts.day, msgs[-1].ts.hour, msgs[-1].ts.minute), (4, 0, 30))
        self.assertEqual(res.system_count, 1)

    def test_english_pc_am_pm(self):
        res = parser.parse_file(fixture_path("pc_english.txt"))
        self.assertEqual([m.ts.hour for m in res.messages], [15, 15, 0])

    def test_noon_and_midnight(self):
        res = parser.parse_file(fixture_path("android.txt"))
        by_text = {m.text: m for m in res.messages}
        self.assertEqual(by_text["새벽이다ㅋㅋㅋㅋㅋ"].ts.hour, 0)   # 오전 12:30
        self.assertEqual(by_text["점심 먹자"].ts.hour, 12)           # 오후 12:05

    def test_encodings(self):
        text = read_fixture("android.txt").decode("utf-8")
        for enc in ("utf-8-sig", "utf-16", "cp949"):
            res = parser.parse_export(parser.decode_bytes(text.encode(enc)))
            self.assertEqual(summarize(res), CANONICAL, enc)

    def test_crlf(self):
        text = read_fixture("pc.txt").decode("utf-8").replace("\n", "\r\n")
        res = parser.parse_export(parser.decode_bytes(text.encode("utf-8")))
        self.assertEqual(summarize(res), CANONICAL)

    def test_csv_comma_and_quotes_in_message(self):
        text = 'Date,User,Message\n2026-01-03 15:05:00,"철수","안녕, ""영희"" 야"\n'
        res = parser.parse_export(text)
        self.assertEqual(res.messages[0].text, '안녕, "영희" 야')


if __name__ == "__main__":
    unittest.main()
