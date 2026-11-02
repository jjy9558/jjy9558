import os
import tempfile
import unittest

from shortslint import metadata as md
from shortslint.config import load_config


def by_name(rs):
    return {r.name: r for r in rs}


class MetaTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_good(self):
        meta = {"title": "시어머니의 비밀 #shorts", "hashtags": ["#사연", "쇼츠"], "ai_disclosure": True}
        r = by_name(md.check_metadata(meta, self.cfg))
        self.assertEqual(r["제목 길이"].status, "ok")
        self.assertEqual(r["해시태그"].status, "ok")
        self.assertEqual(r["금지 단어"].status, "ok")
        self.assertEqual(r["AI 콘텐츠 고지"].status, "ok")

    def test_title_length(self):
        r = by_name(md.check_metadata({"title": "가" * 60}, self.cfg))
        self.assertEqual(r["제목 길이"].status, "warn")
        r = by_name(md.check_metadata({"title": "가" * 101}, self.cfg))
        self.assertEqual(r["제목 길이"].status, "fail")
        r = by_name(md.check_metadata({}, self.cfg))
        self.assertEqual(r["제목"].status, "fail")

    def test_hashtags(self):
        r = by_name(md.check_metadata({"title": "a", "hashtags": ["#a"]}, self.cfg))
        self.assertIn("#shorts 없음", r["해시태그"].message)
        many = {"title": "a #shorts", "hashtags": ["#t%d" % i for i in range(6)]}
        self.assertEqual(by_name(md.check_metadata(many, self.cfg))["해시태그"].status, "warn")
        huge = {"title": "a", "hashtags": ["#t%d" % i for i in range(16)]}
        self.assertEqual(by_name(md.check_metadata(huge, self.cfg))["해시태그"].status, "fail")

    def test_hashtag_dedupe_from_title(self):
        self.assertEqual(md.collect_hashtags({"title": "x #Shorts", "hashtags": ["shorts"]}), ["#shorts"])

    def test_words(self):
        meta = {"title": "충격 실화", "claims": ["카지노 후기"]}
        r = by_name(md.check_metadata(meta, self.cfg))
        self.assertEqual(r["낚시성 단어"].status, "warn")
        self.assertIn("충격", r["낚시성 단어"].message)
        self.assertEqual(r["금지 단어"].status, "fail")

    def test_custom_words(self):
        self.cfg["metadata"]["clickbait_words"] = ["대박"]
        r = by_name(md.check_metadata({"title": "충격 대박"}, self.cfg))
        self.assertIn("대박", r["낚시성 단어"].message)
        self.assertNotIn("충격", r["낚시성 단어"].message)

    def test_ai_reminder(self):
        r = by_name(md.check_metadata({"title": "a"}, self.cfg))
        self.assertEqual(r["AI 콘텐츠 고지"].status, "warn")
        self.assertIn("support.google.com", r["AI 콘텐츠 고지"].message)

    def test_thumbnail(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "t.jpg"), "wb") as f:
                f.write(b"x" * 100)
            r = by_name(md.check_metadata({"title": "a", "thumbnail": "t.jpg"}, self.cfg, d))
            self.assertEqual(r["썸네일 파일"].status, "ok")
            r = by_name(md.check_metadata({"title": "a", "thumbnail": "nope.jpg"}, self.cfg, d))
            self.assertEqual(r["썸네일 파일"].status, "fail")


class DuplicateTests(unittest.TestCase):
    def test_duplicates(self):
        d = md.find_duplicates([("a.mp4", "시어머니의 비밀!"), ("b.mp4", "시어머니의  비밀"),
                                ("c.mp4", "완전히 다른 제목입니다"), ("d.mp4", None)])
        self.assertEqual(d["a.mp4"][0].status, "fail")
        self.assertIn("b.mp4", d["a.mp4"][0].message)
        self.assertNotIn("c.mp4", d)

    def test_similar(self):
        d = md.find_duplicates([("a.mp4", "시어머니가 숨긴 3가지 비밀 결말"), ("b.mp4", "시어머니가 숨긴 3가지 비밀 결말은")])
        self.assertEqual(d["a.mp4"][0].status, "warn")


if __name__ == "__main__":
    unittest.main()
