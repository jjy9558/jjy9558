# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from datetime import date, timezone

import helpers as H
from til_drafter.draft import build_draft, candidate_sentences, project_name, trim
from til_drafter.redact import Redactor
from til_drafter.transcripts import load_entries

UTC = timezone.utc
DAY = date(2026, 3, 10)


def build(root, redactor=None, day=DAY):
    redactor = redactor or Redactor(use_os_user=False)
    entries = load_entries(root, day, UTC)
    return build_draft(entries, day, redactor), redactor


class ProjectNameTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(project_name("/home/a/code/my-blog"), "my-blog")
        self.assertEqual(project_name("C:\\Users\\a\\code\\my-blog\\"), "my-blog")
        self.assertEqual(project_name("F:/work/proj"), "proj")
        self.assertEqual(project_name(""), "(알 수 없음)")
        self.assertEqual(project_name("C:\\"), "(루트)")
        self.assertEqual(project_name("/"), "(루트)")

    def test_trim(self):
        self.assertEqual(trim("a  b\n c", 100), "a b c")
        t = trim("가" * 50, 10)
        self.assertEqual(len(t), 10)
        self.assertTrue(t.endswith("…"))


class CandidateTests(unittest.TestCase):
    def test_keyword_sentences(self):
        texts = ["파일을 열었다. 원인은 인코딩이 cp949였기 때문이다. 다음으로 넘어간다.",
                 "I fixed it because the path was wrong. Nothing special here."]
        got = candidate_sentences(texts)
        self.assertEqual(len(got), 2)
        self.assertTrue(any("원인" in s for s in got))
        self.assertTrue(any("fixed" in s for s in got))

    def test_limited_to_three_and_skips_code_fences_and_dupes(self):
        body = "\n".join("- 문제 %d의 원인은 설정이 틀려서였다." % i for i in range(10))
        got = candidate_sentences([body, "```\n# because this is code, fixed\n```", body])
        self.assertEqual(len(got), 3)
        self.assertFalse(any("code" in s for s in got))

    def test_no_prefix_false_positive(self):
        self.assertEqual(candidate_sentences(["The prefix and suffix are fine to keep."]), [])


class DraftTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        blog = "/home/hong/code/blog"
        api = "C:\\Users\\hong\\dev\\api-server"
        H.write_jsonl(os.path.join(self.root, "-blog", "s1.jsonl"), [
            H.user("2026-03-10T01:00:00Z", "tistory 글 목록을 가져오는 스크립트를 만들어줘", cwd=blog, sid="s1"),
            H.assistant("2026-03-10T01:00:05Z", [
                {"type": "text", "text": "먼저 구조를 살펴볼게요."},
                H.tool_use("Read", file_path=blog + "/README.md"),
                H.tool_use("Write", file_path=blog + "/scripts/list_posts.py", content="..."),
                H.tool_use("Write", file_path=blog + "/scripts/list_posts.py", content="..."),
            ], cwd=blog, sid="s1"),
            H.tool_result("2026-03-10T01:00:06Z", "ok", cwd=blog, sid="s1"),
            H.user("2026-03-10T01:05:00Z", "에러가 나는데 확인해줘", cwd=blog, sid="s1"),
            H.assistant("2026-03-10T01:06:00Z", [
                {"type": "text", "text": "원인은 인코딩이 cp949였기 때문입니다. UTF-8로 지정해서 해결했습니다. 완료."},
                H.tool_use("Bash", command="python scripts/list_posts.py"),
            ], cwd=blog, sid="s1"),
            H.user("2026-03-11T01:00:00Z", "다음날 요청", cwd=blog, sid="s1"),
        ])
        H.write_jsonl(os.path.join(self.root, "-api", "s2.jsonl"), [
            H.user("2026-03-10T02:00:00Z", "로그인 API 리팩터링", cwd=api, sid="s2"),
            H.assistant("2026-03-10T02:01:00Z", "리팩터링을 마쳤습니다. 테스트 12개 통과.", cwd=api, sid="s2"),
            H.user("2026-03-10T03:00:00Z", "새 세션 요청", cwd=api, sid="s3"),
            H.assistant("2026-03-10T03:01:00Z", "두번째 세션의 마지막 답변", cwd=api, sid="s3"),
        ])

    def test_grouping_sections_and_content(self):
        md, _ = build(self.root)
        self.assertTrue(md.startswith("# [TIL] 2026-03-10"))
        self.assertIn("## blog", md)
        self.assertIn("## api-server", md)
        self.assertLess(md.index("## blog"), md.index("## api-server"))  # 먼저 나온 프로젝트 순
        for h in ("### 요청한 것", "### 한 일", "### 사용한 도구 / 파일", "### 오늘 배운 것"):
            self.assertEqual(md.count(h), 2, h)
        self.assertIn("tistory 글 목록을 가져오는 스크립트를 만들어줘", md)
        self.assertIn("에러가 나는데 확인해줘", md)
        self.assertNotIn("다음날 요청", md)  # 다른 날짜 제외
        self.assertIn("로그인 API 리팩터링", md)

    def test_last_assistant_text_per_session(self):
        md, _ = build(self.root)
        self.assertIn("UTF-8로 지정해서 해결했습니다", md)
        self.assertNotIn("먼저 구조를 살펴볼게요", md)  # 세션의 마지막 텍스트만
        self.assertIn("리팩터링을 마쳤습니다", md)
        self.assertIn("두번째 세션의 마지막 답변", md)  # 세션이 2개면 각각

    def test_tools_and_files_counted_by_name_only(self):
        md, _ = build(self.root)
        self.assertIn("`Write` ×2", md)
        self.assertIn("`Bash` ×1", md)
        self.assertIn("`list_posts.py` ×2", md)
        self.assertIn("읽은 파일: `README.md` ×1", md)
        self.assertNotIn("/home/hong", md)
        self.assertNotIn("scripts/list_posts.py", md)

    def test_learned_candidates_and_placeholder(self):
        md, _ = build(self.root)
        section = md.split("### 오늘 배운 것")[1]
        self.assertIn("- (후보) ", section)
        self.assertIn("원인은 인코딩이 cp949였기 때문입니다", section)
        self.assertIn("- (직접 작성)", section)

    def test_footer_privacy_note(self):
        md, _ = build(self.root)
        self.assertIn("장소", md.split("게시 전 확인")[1])
        self.assertIn("시간", md.split("게시 전 확인")[1])

    def test_no_session_times_in_body(self):
        md, _ = build(self.root)
        self.assertNotRegex(md, r"\b\d{2}:\d{2}\b")

    def test_empty_day(self):
        md, _ = build(self.root, day=date(2020, 1, 1))
        self.assertIn("기록이 없습니다", md)

    def test_secrets_in_content_are_redacted_everywhere(self):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        tok = "ghp_" + "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k8"
        cwd = "C:\\Users\\secretuser\\proj\\my-app"
        H.write_jsonl(os.path.join(root, "p", "s.jsonl"), [
            H.user("2026-03-10T01:00:00Z",
                   "서버 192.168.10.5 에 %s 로 접속하고 mail me@corp.com 010-2222-3333" % tok, cwd=cwd),
            H.assistant("2026-03-10T01:01:00Z", [
                {"type": "text", "text": "C:\\Users\\secretuser\\Desktop\\key.pem 파일이 원인이었고 secretuser 계정 문제를 해결했다."},
                H.tool_use("Edit", file_path="C:\\Users\\secretuser\\proj\\my-app\\.env"),
            ], cwd=cwd),
        ])
        md, r = build(root)
        for leak in (tok, "192.168.10.5", "me@corp.com", "010-2222-3333", "secretuser", "Desktop"):
            self.assertNotIn(leak, md)
        self.assertIn("my-app", md)
        self.assertIn("`.env`", md)
        self.assertGreater(sum(r.counts.values()), 4)
        self.assertIn("자동 마스킹", md)

    def test_username_in_project_name_is_redacted(self):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        H.write_jsonl(os.path.join(root, "p", "s.jsonl"), [
            H.user("2026-03-10T01:00:00Z", "안녕", cwd="/home/dongsu/dongsu-notes"),
        ])
        md, _ = build(root)
        self.assertNotIn("dongsu", md)

    def test_custom_redact_words(self):
        md, _ = build(self.root, Redactor(words=["tistory"], use_os_user=False))
        self.assertNotIn("tistory", md.lower().replace("<가림>", ""))


if __name__ == "__main__":
    unittest.main()
