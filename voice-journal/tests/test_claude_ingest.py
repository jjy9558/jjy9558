# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from datetime import datetime, timezone

from vjournal import claude_ingest as CI
from vjournal.redact import Redactor
from tests.helpers import msg, write_jsonl

UTC = timezone.utc
START = datetime(2026, 11, 1, 13, 0, tzinfo=UTC)
END = datetime(2026, 11, 1, 23, 0, tzinfo=UTC)


def tool_use(name, **inp):
    return {"type": "tool_use", "id": "t1", "name": name, "input": inp}


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.pdir = os.path.join(self.root, "projects")

    def test_report_three_lines_per_project(self):
        a = "/home/alice/work/proj-a"
        write_jsonl(os.path.join(self.pdir, "-proj-a", "s1.jsonl"), [
            msg("user", "2026-11-01T14:00:00Z", "로그인 버그 고치고 테스트 돌려줘", cwd=a),
            msg("assistant", "2026-11-01T14:01:00Z", [
                {"type": "text", "text": "수정하겠습니다."},
                tool_use("Edit", file_path=a + "/auth.py", old_string="a", new_string="b"),
                tool_use("Write", file_path=a + "/tests/test_auth.py", content="x")], cwd=a),
            msg("assistant", "2026-11-01T14:02:00Z", [
                tool_use("Bash", command="python -m pytest -q"),
                tool_use("Bash", command="git commit -m fix")], cwd=a),
            msg("user", "2026-11-01T14:03:00Z", "다음엔 문서도 정리해줘", cwd=a),
            msg("assistant", "2026-11-01T14:04:00Z",
                [{"type": "text", "text": "로그인 버그를 고쳤고 테스트가 통과했습니다. 문서는 다음에 하겠습니다."}], cwd=a),
        ])
        b = "/home/alice/work/proj-b"
        write_jsonl(os.path.join(self.pdir, "-proj-b", "s2.jsonl"), [
            msg("user", "2026-11-01T15:00:00Z", "배포 스크립트 만들어줘", cwd=b, sid="s2"),
            msg("assistant", "2026-11-01T15:01:00Z",
                [{"type": "text", "text": "AWS 권한이 없어서 막혔습니다. 키를 설정해 주실 수 있나요?"}], cwd=b, sid="s2"),
        ])
        stats = {}
        rep = CI.build_report(self.pdir, START, END, stats=stats)
        self.assertEqual([r["project"] for r in rep], ["proj-a", "proj-b"])
        for r in rep:
            self.assertEqual(len(r["lines"]), 3)
        a_lines = rep[0]["lines"]
        self.assertIn("로그인 버그", a_lines[0])
        self.assertIn("외 1건", a_lines[0])
        self.assertIn("auth.py", a_lines[1])
        self.assertIn("커밋 1회", a_lines[1])
        self.assertIn("테스트 1회", a_lines[1])
        self.assertIn("막힘: 없음", a_lines[2])
        self.assertIn("막힘", rep[1]["lines"][2])
        self.assertIn("권한", rep[1]["lines"][2])
        text = CI.render_report(rep, START, END)
        self.assertIn("■ proj-a", text)

    def test_window_filter_malformed_and_noise(self):
        c = "/x/proj"
        write_jsonl(os.path.join(self.pdir, "p", "s.jsonl"), [
            "{ this is not json",
            "[1,2,3]",
            "",
            msg("user", "2026-11-01T10:00:00Z", "창 밖의 요청", cwd=c),               # 윈도우 이전
            msg("user", "2026-11-01T14:00:00Z", "<command-name>/clear</command-name>", cwd=c),
            msg("user", "2026-11-01T14:00:01Z", "<system-reminder>숨김</system-reminder>", cwd=c),
            msg("user", "2026-11-01T14:00:02Z", "진짜 요청", cwd=c, isSidechain=True),
            msg("user", "2026-11-01T14:00:03Z", "진짜 요청", cwd=c),
            {"type": "user", "timestamp": "깨진시각", "message": {"content": "x"}},
            {"type": "summary", "summary": "무시"},
            msg("assistant", "2026-11-01T14:01:00Z", [{"type": "thinking", "thinking": "비밀생각"},
                                                      {"type": "text", "text": "끝났습니다."}], cwd=c),
        ])
        stats = {}
        rep = CI.build_report(self.pdir, START, END, stats=stats)
        self.assertEqual(stats["malformed"], 2)
        self.assertEqual(len(rep), 1)
        self.assertIn("진짜 요청", rep[0]["lines"][0])
        self.assertNotIn("외", rep[0]["lines"][0])
        self.assertNotIn("창 밖", "\n".join(rep[0]["lines"]))
        self.assertNotIn("비밀생각", "\n".join(rep[0]["lines"]))

    def test_redaction_everywhere(self):
        c = "/home/alice/secret-proj"
        key = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1"
        write_jsonl(os.path.join(self.pdir, "p", "s.jsonl"), [
            msg("user", "2026-11-01T14:00:00Z",
                "API_KEY=%s 로 alice@example.com 에게 보내고 서버 10.1.2.3 확인해줘 010-1234-5678" % key, cwd=c),
            msg("assistant", "2026-11-01T14:01:00Z", [
                {"type": "text", "text": "토큰 %s 는 /home/alice/secret-proj/.env 에 있어서 막혔습니다." % key},
                tool_use("Edit", file_path=c + "/a.py")], cwd=c),
        ])
        rep = CI.build_report(self.pdir, START, END)
        blob = "\n".join(rep[0]["lines"]) + CI.render_report(rep, START, END)
        for leak in (key, "alice@example.com", "10.1.2.3", "010-1234-5678", "/home/alice"):
            self.assertNotIn(leak, blob)

    def test_llm_receives_only_redacted_and_falls_back(self):
        c = "/p/proj"
        key = "ghp_" + "A" * 30
        write_jsonl(os.path.join(self.pdir, "p", "s.jsonl"), [
            msg("user", "2026-11-01T14:00:00Z", "토큰 %s 써서 배포" % key, cwd=c),
            msg("assistant", "2026-11-01T14:01:00Z", [{"type": "text", "text": "배포했습니다."}], cwd=c)])
        seen = []

        class Fake(object):
            def __init__(self, reply):
                self.reply = reply

            def chat(self, messages, **kw):
                seen.append(messages[0]["content"])
                if isinstance(self.reply, Exception):
                    raise self.reply
                return self.reply

        rep = CI.build_report(self.pdir, START, END, llm=Fake("요청은 배포\n완료는 배포\n막힘 없음"))
        self.assertEqual(rep[0]["lines"], ["요청은 배포", "완료는 배포", "막힘 없음"])
        self.assertNotIn(key, seen[0])
        # 형식 불량/예외 -> 규칙 기반
        for bad in ("한 줄뿐", RuntimeError("down")):
            rep = CI.build_report(self.pdir, START, END, llm=Fake(bad))
            self.assertTrue(rep[0]["lines"][0].startswith("요청:"))

    def test_empty_dir_and_missing_dir(self):
        self.assertEqual(CI.build_report(os.path.join(self.root, "nope"), START, END), [])
        self.assertIn("활동이 없었", CI.render_report([], START, END))

    def test_unanswered_prompt_is_blocked(self):
        c = "/p/q"
        write_jsonl(os.path.join(self.pdir, "p", "s.jsonl"), [
            msg("user", "2026-11-01T14:00:00Z", "이거 해줘", cwd=c)])
        rep = CI.build_report(self.pdir, START, END)
        self.assertIn("응답이 없음", rep[0]["lines"][2])

    def test_redactor_basics(self):
        r = Redactor(words=["비밀프로젝트"])
        out = r.redact("Bearer abcdefgh12345678 password=hunter2hunter 비밀프로젝트 -----BEGIN RSA PRIVATE KEY-----\nxx\n-----END RSA PRIVATE KEY-----")
        for leak in ("abcdefgh12345678", "hunter2hunter", "비밀프로젝트", "BEGIN RSA"):
            self.assertNotIn(leak, out)
        self.assertGreaterEqual(sum(r.counts.values()), 4)


if __name__ == "__main__":
    unittest.main()
