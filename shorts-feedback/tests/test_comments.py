import json
import tempfile
import unittest
from pathlib import Path

from shortsfb import comments as C

from helpers import FIX
from synth import TOPICS, make_comments


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat_json(self, messages, **kw):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class ClusterCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texts, cls.labels = make_comments(50, seed=3)  # 200개, 주제 4개

    # D-5 (3)
    def test_four_topics_separate_with_purity(self):
        res = C.cluster_comments(self.texts)
        self.assertEqual(len(self.texts), 200)
        self.assertGreaterEqual(C.purity(res["assignments"], self.labels), 0.7)
        self.assertGreaterEqual(res["k"], 3)

    def test_explicit_k_four_is_pure(self):
        res = C.cluster_comments(self.texts, k=4)
        self.assertEqual(res["k"], 4)
        self.assertGreaterEqual(C.purity(res["assignments"], self.labels), 0.9)

    def test_other_seeds_also_separate(self):
        for seed in (4, 5):
            texts, labels = make_comments(50, seed=seed)
            res = C.cluster_comments(texts)
            self.assertGreaterEqual(C.purity(res["assignments"], labels), 0.7, seed)

    def test_deterministic_for_same_seed(self):
        a = C.cluster_comments(self.texts, seed=11)
        b = C.cluster_comments(self.texts, seed=11)
        self.assertEqual(a["assignments"], b["assignments"])
        self.assertEqual([c["name"] for c in a["clusters"]], [c["name"] for c in b["clusters"]])
        self.assertEqual(C.format_top(a), C.format_top(b))

    # D-5 (4)
    def test_names_without_llm_come_from_topic_ngrams(self):
        res = C.cluster_comments(self.texts, k=4)
        all_words = {t: set(ns) for t, ns in TOPICS.items()}
        for c in res["clusters"]:
            self.assertTrue(c["name"] and c["name"] != "(이름 없음)")
            self.assertTrue(c["ngrams"])
            # 대표 n-gram은 그 군집 정답 주제어의 일부여야 한다
            members_labels = {self.labels[i] for i in c["members"]}
            topic = max(members_labels, key=lambda t: sum(1 for i in c["members"] if self.labels[i] == t))
            self.assertTrue(any(g in w for g in c["ngrams"] for w in all_words[topic]), (c["name"], topic))

    def test_top5_ranking_and_size(self):
        res = C.cluster_comments(self.texts, k=6, top=5)
        self.assertEqual(len(res["top"]), 5)
        sizes = [t["size"] + 2 * t["requests"] for t in res["top"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual([t["rank"] for t in res["top"]], [1, 2, 3, 4, 5])

    def test_requests_boost_rank(self):
        clusters = [{"id": 0, "size": 10, "requests": 0, "questions": 0, "name": "a", "samples": []},
                    {"id": 1, "size": 8, "requests": 4, "questions": 0, "name": "b", "samples": []}]
        top = C.top_topics(clusters, 18)
        self.assertEqual([t["name"] for t in top], ["b", "a"])

    def test_llm_names_applied_and_fallback(self):
        res = C.cluster_comments(self.texts, k=4)
        cl = res["clusters"]
        client = FakeLLM({"names": [{"id": cl[0]["id"], "name": "층간소음 갈등 사연"}, {"id": 99, "name": "x"}]})
        names = C.llm_names(client, cl)
        self.assertEqual(names, {cl[0]["id"]: "층간소음 갈등 사연"})
        C.apply_names(res, names)
        self.assertIn("층간소음 갈등 사연", C.format_top(res))
        self.assertEqual(C.llm_names(FakeLLM(RuntimeError("boom")), cl), {})
        self.assertEqual(C.llm_names(FakeLLM("not a dict"), cl), {})
        self.assertEqual(C.llm_names(None, cl), {})

    def test_too_few_comments(self):
        res = C.cluster_comments(["하나", "둘"])
        self.assertIn("note", res)
        self.assertEqual(res["clusters"], [])
        self.assertIn("너무 적습니다", C.format_top(res))

    def test_spam_is_excluded_and_counted(self):
        texts = self.texts[:40] + ["맞구독 해요", "무료 상담 클릭", "대박 https://spam.example/a"]
        res = C.cluster_comments(texts, k=4)
        self.assertEqual(res["kinds"]["spam"], 3)
        self.assertEqual(res["total"], 43)

    def test_kind_rules(self):
        self.assertEqual(C.kind_of("다음 편 올려 주세요"), "request")
        self.assertEqual(C.kind_of("이거 어떻게 됐나요"), "question")
        self.assertEqual(C.kind_of("진짜 최고예요"), "praise")
        self.assertEqual(C.kind_of("텔레그램으로 오세요"), "spam")
        self.assertEqual(C.kind_of("그렇군요"), "other")


class LoadCase(unittest.TestCase):
    def test_api_json_reads_text_only(self):
        got = C.load_comments(FIX / "comments_api.json")
        self.assertEqual(len(got), 3)
        self.assertIn("윗집 층간소음 얘기 다음 편도 올려 주세요", got)
        joined = " ".join(got)
        for name in ("김철수실명", "이영희실명", "박스팸실명", "UCfakechannelid0001"):
            self.assertNotIn(name, joined)

    def test_takeout_csv_segments(self):
        got = C.load_comments(FIX / "comments_takeout.csv")
        self.assertEqual(got, ["시댁 명절 이야기 너무 공감돼요", "야근 수당 후기 궁금합니다?"])

    def test_csv_with_named_column_ignores_author(self):
        got = C.load_comments(FIX / "comments_author.csv")
        self.assertEqual(len(got), 2)
        self.assertNotIn("홍길동실명", " ".join(got))

    def test_paste_lines(self):
        self.assertEqual(len(C.load_comments(FIX / "comments_paste.txt")), 3)

    def test_json_string_list_and_cp949_csv(self):
        self.assertEqual(C.parse_comments('["가나다 라마", "바사"]'), ["가나다 라마", "바사"])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.csv"
            p.write_bytes("댓글,작성자\n시댁 이야기,홍\n".encode("cp949"))
            self.assertEqual(C.load_comments(p), ["시댁 이야기"])

    def test_normalize_and_clean(self):
        self.assertEqual(C.normalize("ㅋㅋㅋㅋ 대박!!! <b>윗집</b> @user1 1:23 https://x.example"), "대박 윗집")
        cleaned = C.clean_text("연락 010-1234-5678 @핸들 https://x.example 윗집")
        self.assertNotIn("010-1234", cleaned)
        self.assertNotIn("@", cleaned)
        self.assertNotIn("http", cleaned)

    # WP11 인수 (7)
    def test_public_view_has_no_author_or_members_or_raw_phone(self):
        texts, _ = make_comments(20, seed=3)
        rows = [{"authorDisplayName": "작성자%d실명" % i, "textOriginal": t + (" 010-1234-5678" if i == 0 else "")}
                for i, t in enumerate(texts)]
        loaded = C.parse_comments(json.dumps(rows, ensure_ascii=False))
        res = C.cluster_comments(loaded, k=4)
        dumped = json.dumps(C.public_view(res), ensure_ascii=False)
        self.assertNotIn("실명", dumped)
        self.assertNotIn("010-1234-5678", dumped)
        self.assertNotIn("members", dumped)
        self.assertNotIn("_items", dumped)


if __name__ == "__main__":
    unittest.main()
