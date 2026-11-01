import json
import unittest

from helpers import M, fixture_path, NAMES
from kakao_wrapped import parser, stats, textutil


def fixture_stats(name="android.txt", **kw):
    res = parser.parse_file(fixture_path(name))
    kw.setdefault("year", 2026)
    return stats.compute_stats(res.messages, **kw)


class YearFilter(unittest.TestCase):
    def test_only_chosen_year_is_counted(self):
        s26 = fixture_stats(year=2026)
        s25 = fixture_stats(year=2025)
        self.assertEqual(s26["totals"]["messages"], 11)  # system line + 2025 msg excluded
        self.assertEqual(s25["totals"]["messages"], 1)
        self.assertEqual(s25["members"][0]["name"], "철수")

    def test_available_years(self):
        res = parser.parse_file(fixture_path("android.txt"))
        self.assertEqual(stats.available_years(res.messages), [2025, 2026])

    def test_year_without_messages(self):
        s = fixture_stats(year=2030)
        self.assertTrue(s["empty"])

    def test_counts_and_shares(self):
        s = fixture_stats()
        by = {m["name"]: m for m in s["members"]}
        self.assertEqual(by["철수"]["messages"], 4)
        self.assertEqual(by["영희"]["messages"], 4)
        self.assertEqual(by["민수"]["messages"], 3)
        self.assertAlmostEqual(sum(m["share"] for m in s["members"]), 100, delta=0.2)
        self.assertEqual(by["민수"]["photos"], 4)  # 사진 + 사진 3장
        self.assertEqual(by["영희"]["emoticons"], 1)
        self.assertEqual(by["철수"]["videos"], 1)
        self.assertEqual(by["영희"]["files"], 1)
        self.assertEqual(by["철수"]["deleted"], 1)
        self.assertEqual(s["totals"]["photos"], 4)

    def test_media_not_counted_as_words(self):
        s = fixture_stats()
        words = {w for w, _ in s["top_words"]}
        for placeholder in ("사진", "동영상", "이모티콘", "삭제된"):
            self.assertNotIn(placeholder, words)

    def test_busiest_day_month_and_heatmap(self):
        s = fixture_stats()
        self.assertEqual(s["busiest_day"]["date"], "2026-01-03")
        self.assertEqual(s["busiest_day"]["count"], 7)
        self.assertEqual(s["busiest_month"]["month"], 1)
        self.assertEqual(sum(sum(r) for r in s["heatmap"]), 11)
        # 2026-01-03 is a Saturday (index 5), 15시
        self.assertEqual(s["heatmap"][5][15], 7)
        self.assertEqual(s["hours"][15], 7)

    def test_first_and_last_message(self):
        s = fixture_stats()
        self.assertEqual(s["first_message"]["ts"], "2026-01-03 15:05")
        self.assertEqual(s["first_message"]["name"], "철수")
        self.assertEqual(s["last_message"]["ts"], "2026-01-04 12:07")
        self.assertEqual(s["last_message"]["text"], "(파일)")

    def test_links(self):
        s = fixture_stats()
        self.assertEqual(s["links"]["domains"], [["youtube.com", 1]])
        self.assertEqual(s["links"]["top_sharer"]["name"], "철수")

    def test_night_owl(self):
        s = fixture_stats()
        self.assertEqual(s["awards"]["night_owl"]["winner"]["name"], "영희")
        self.assertEqual(s["awards"]["night_owl"]["winner"]["value"], 1)


class ReplyTime(unittest.TestCase):
    def build(self):
        return [
            M("2026-03-01 10:00:00", "철수", "점심 뭐 먹을까"),
            M("2026-03-01 10:00:30", "영희", "김밥"),       # 영희 reply 30s
            M("2026-03-01 10:02:00", "철수", "오 좋아"),     # 철수 reply 90s
            M("2026-03-01 10:02:20", "영희", "ㅇㅋ"),        # 영희 reply 20s
            M("2026-03-01 10:02:25", "영희", "어디서 볼까"),  # same person: not a reply
            M("2026-03-01 12:58:00", "민수", "나 방금 일어남"),  # gap 2h55m: ignored
            M("2026-03-01 13:30:00", "철수", "ㅋㅋ"),        # 철수 reply 1920s
        ]

    def test_median_and_ignored_gaps(self):
        s = stats.compute_stats(self.build(), 2026, min_samples=1)
        by = {m["name"]: m for m in s["members"]}
        self.assertEqual(by["영희"]["median_reply_sec"], 25.0)
        self.assertEqual(by["영희"]["reply_count"], 2)
        self.assertEqual(by["철수"]["reply_count"], 2)
        self.assertEqual(by["철수"]["median_reply_sec"], (90 + 1920) / 2)
        self.assertIsNone(by["민수"]["median_reply_sec"])  # only gap > 1h
        self.assertEqual(s["awards"]["fast_replier"]["winner"]["name"], "영희")
        self.assertEqual(s["awards"]["slow_replier"]["winner"]["name"], "철수")

    def test_min_samples_gate(self):
        s = stats.compute_stats(self.build(), 2026, min_samples=5)
        self.assertIsNone(s["awards"]["fast_replier"])


class KkkIndex(unittest.TestCase):
    def test_average_and_longest_streak(self):
        msgs = [
            M("2026-03-01 10:00", "철수", "ㅋㅋㅋ"),
            M("2026-03-01 10:01", "철수", "안녕"),
            M("2026-03-01 10:02", "철수", "ㅋㅋ ㅋㅋㅋㅋ"),
            M("2026-03-01 10:03", "영희", "ㅋ"),
            M("2026-03-01 10:04", "영희", "사진"),   # media: not a text message
        ]
        s = stats.compute_stats(msgs, 2026, min_samples=1)
        by = {m["name"]: m for m in s["members"]}
        self.assertEqual(by["철수"]["kkk_total"], 9)
        self.assertEqual(by["철수"]["text_messages"], 3)
        self.assertEqual(by["철수"]["kkk_per_msg"], 3.0)
        self.assertEqual(by["철수"]["kkk_longest"], 4)
        self.assertEqual(by["영희"]["kkk_per_msg"], 1.0)
        self.assertEqual(s["awards"]["kkk_king"]["winner"]["name"], "철수")
        self.assertEqual(s["awards"]["kkk_streak"], {"name": "철수", "length": 4})

    def test_longest_run_helper(self):
        self.assertEqual(textutil.longest_run("ㅋㅋ아ㅋㅋㅋ", "ㅋ"), 3)
        self.assertEqual(textutil.longest_run("없음", "ㅋ"), 0)


class Awards(unittest.TestCase):
    def test_lurker_needs_active_chat(self):
        msgs = [M("2026-02-01 10:00", "민수", "나 간다")]
        for i in range(30):
            msgs.append(M("2026-02-%02d 12:%02d" % (2 + i // 10, i), "철수" if i % 2 else "영희", "수다 %d" % i))
        msgs.append(M("2026-02-20 09:00", "민수", "돌아왔다"))
        s = stats.compute_stats(msgs, 2026, lurk_min_others=20)
        lk = s["awards"]["lurker"]
        self.assertEqual(lk["name"], "민수")
        self.assertEqual(lk["others_messages"], 30)
        self.assertFalse(lk["ongoing"])
        # quiet chat -> nobody is called a lurker
        s2 = stats.compute_stats(msgs, 2026, lurk_min_others=100)
        self.assertIsNone(s2["awards"]["lurker"])

    def test_starter_king(self):
        msgs = [
            M("2026-02-01 09:00", "철수", "굿모닝"),
            M("2026-02-01 09:01", "영희", "응"),
            M("2026-02-01 20:00", "철수", "저녁?"),
            M("2026-02-02 08:00", "영희", "아침"),
            M("2026-02-03 08:00", "철수", "또 아침"),
        ]
        s = stats.compute_stats(msgs, 2026)
        self.assertEqual(s["awards"]["starter_king"]["winner"], {"name": "철수", "value": 3})

    def test_photo_and_emoticon_kings(self):
        msgs = [
            M("2026-02-01 09:00", "철수", "사진 5장"),
            M("2026-02-01 09:01", "영희", "사진"),
            M("2026-02-01 09:02", "영희", "이모티콘"),
            M("2026-02-01 09:03", "영희", "이모티콘"),
            M("2026-02-01 09:04", "철수", "이모티콘"),
        ]
        s = stats.compute_stats(msgs, 2026)
        self.assertEqual(s["awards"]["photo_king"]["winner"]["name"], "철수")
        self.assertEqual(s["awards"]["emoticon_king"]["winner"]["name"], "영희")

    def test_quote_of_year_picks_longest_follow_up(self):
        msgs = [
            M("2026-04-01 10:00:00", "철수", "혹시 이 소식 들었어?"),
            M("2026-04-01 10:00:20", "영희", "뭐?"),
            M("2026-04-01 10:00:40", "민수", "헐 진짜?"),
            M("2026-04-01 10:01:10", "영희", "대박이다"),
            M("2026-04-01 10:02:00", "민수", "ㅋㅋㅋㅋ"),
            M("2026-04-01 15:00:00", "철수", "점심 먹었어"),
            M("2026-04-01 15:00:30", "영희", "응"),
        ]
        q = stats.compute_stats(msgs, 2026)["quote_of_year"]
        self.assertEqual(q["text"], "혹시 이 소식 들었어?")
        self.assertEqual(q["name"], "철수")
        self.assertEqual(q["followers"], 4)
        self.assertEqual(q["distinct_people"], 2)

    def test_streak(self):
        msgs = [M("2026-05-%02d 10:00" % d, "철수", "출석 %d" % d) for d in (1, 2, 3, 5, 6)]
        s = stats.compute_stats(msgs, 2026)
        self.assertEqual(s["streak"], {"days": 3, "start": "2026-05-01", "end": "2026-05-03"})


class Tokenizer(unittest.TestCase):
    def test_particles_and_noise(self):
        toks = textutil.tokenize("오늘 점심을 먹으러 카페에서 만나자ㅋㅋㅋ 진짜 http://a.com/x ㅎㅎ 12345")
        self.assertIn("점심", toks)
        self.assertIn("카페", toks)
        self.assertNotIn("진짜", toks)            # stopword
        self.assertFalse([t for t in toks if "ㅋ" in t or "ㅎ" in t])
        self.assertFalse([t for t in toks if "http" in t or t.isdigit()])

    def test_short_words_survive(self):
        self.assertIn("아이", textutil.tokenize("아이 귀여워"))  # must not become "아"

    def test_drop_names(self):
        self.assertEqual(textutil.tokenize("철수야 밥 먹자", drop=["철수"]), ["먹자"])


class Redaction(unittest.TestCase):
    def test_phone_email_url(self):
        out = textutil.redact("전화 010-1234-5678 / 01012345678 / 02-123-4567 메일 a.b@test.co.kr 링크 https://x.com/a?b=1")
        for secret in ("010-1234-5678", "01012345678", "02-123-4567", "a.b@test.co.kr", "x.com"):
            self.assertNotIn(secret, out)
        self.assertIn("[전화번호]", out)
        self.assertIn("[이메일]", out)

    def test_quotes_in_stats_are_redacted(self):
        msgs = [
            M("2026-01-01 00:00", "철수", "내 번호는 010-9999-8888 이고 메일은 chulsoo@example.com 이야"),
            M("2026-01-01 00:01", "영희", "ok"),
        ]
        s = stats.compute_stats(msgs, 2026)
        dumped = json.dumps(s, ensure_ascii=False)
        for secret in ("010-9999-8888", "chulsoo@example.com"):
            self.assertNotIn(secret, dumped)


class Anonymize(unittest.TestCase):
    def messages(self):
        msgs = []
        for i in range(12):
            msgs.append(M("2026-06-01 10:%02d" % (2 * i), "철수", "철수야 같이 밥 먹자 영희랑 민수도"))
            msgs.append(M("2026-06-01 10:%02d" % (2 * i + 1), "영희", "민수는 어디갔어 맛집"))
        msgs.append(M("2026-06-01 12:00", "민수", "나 여기 있어"))
        return msgs

    def test_consistent_mapping(self):
        s = stats.compute_stats(self.messages(), 2026, anonymize=True)
        self.assertEqual({m["name"] for m in s["members"]}, {"멤버1", "멤버2", "멤버3"})
        self.assertEqual(s["members"][0]["name"], "멤버1")
        self.assertEqual(s["members"][0]["messages"], 12)
        self.assertEqual(s["members"][-1]["name"], "멤버3")  # least active
        msgs, mapping = stats.anonymize_messages([m for m in self.messages()])
        self.assertEqual(mapping["민수"], "멤버3")
        self.assertEqual({m.user for m in msgs if m.user == "민수"}, set())

    def test_no_names_or_quotes_in_stats(self):
        s = stats.compute_stats(self.messages(), 2026, anonymize=True, title="철수영희 방")
        dumped = json.dumps(s, ensure_ascii=False)
        for n in NAMES:
            self.assertNotIn(n, dumped)
        self.assertIsNone(s["meta"]["title"])
        self.assertIsNone(s["first_message"]["text"])
        self.assertIsNone(s["quote_of_year"]["text"])

    def test_not_anonymized_keeps_names(self):
        s = stats.compute_stats(self.messages(), 2026)
        self.assertIn("철수", json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
