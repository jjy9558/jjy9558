import io
import json
import tempfile
import unittest
from pathlib import Path

from tokendiet import claudemd, logscan, mcpweigh, repeats, route
from tokendiet.cli import main
from tokendiet.report import to_html, to_markdown

from helpers import B


def fake_secret() -> str:
    return "sk-" + "ab12" * 10     # 런타임에 조립한 가짜 키 모양


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def sessions(self):
        return logscan.load_sessions(self.dir)

    def test_repeat_reads_detected(self):
        b = B()
        for _ in range(3):
            b.call("Read", {"file_path": "/p/a.py"}, "x" * 4000)
        b.call("Read", {"file_path": "/p/b.py"}, "y" * 400)
        b.save(self.dir / "s1.jsonl")
        rr = logscan.find_repeat_reads(self.sessions()[0])
        self.assertEqual(len(rr), 1)
        self.assertEqual((rr[0][0], rr[0][1], rr[0][2]), ("/p/a.py", 3, 2))
        self.assertEqual(rr[0][3], 2000)      # 4000자/4 x 2회

    def test_reread_after_edit_is_not_waste(self):
        b = B()
        b.call("Read", {"file_path": "/p/a.py"}, "x" * 400)
        b.call("Edit", {"file_path": "/p/a.py"}, "done")
        b.call("Read", {"file_path": "/p/a.py"}, "x" * 400)
        b.save(self.dir / "s1.jsonl")
        self.assertEqual(logscan.find_repeat_reads(self.sessions()[0]), [])

    def test_sidechain_excluded(self):
        b = B()
        b.call("Read", {"file_path": "/p/a.py"}, "x" * 400)
        b.call("Read", {"file_path": "/p/a.py"}, "x" * 400, sidechain=True)
        b.call("Bash", {"command": "big"}, "z" * 40000, sidechain=True)
        b.save(self.dir / "s1.jsonl")
        s = self.sessions()[0]
        self.assertEqual(len(s.calls), 1)
        self.assertEqual(logscan.find_repeat_reads(s), [])
        self.assertEqual(logscan.find_big_outputs(s, 1000), [])

    def test_big_output_and_retry_loop(self):
        b = B()
        b.call("Bash", {"command": "cat huge"}, "z" * 20000)
        for i in range(3):
            b.call("Bash", {"command": f"fail {i}"}, "boom", error=True)
        b.save(self.dir / "s1.jsonl")
        s = self.sessions()[0]
        big = logscan.find_big_outputs(s, 2000)
        self.assertEqual(len(big), 1)
        self.assertEqual(big[0][1], 5000)
        self.assertEqual(len(logscan.find_retry_loops(s)), 1)
        self.assertEqual(s.usage["cache_read_input_tokens"], 40)   # usage 실측, 중복 제거 없이 4개 메시지

    def test_report_labels_estimates_and_masks_secret(self):
        b = B()
        b.call("Bash", {"command": f"curl -H 'Authorization: {fake_secret()}' x"}, "z" * 20000)
        b.save(self.dir / "s1.jsonl")
        rep = logscan.build_report(self.sessions(), source=str(self.dir))
        md, html = to_markdown(rep), to_html(rep)
        self.assertIn("추정", md)
        for out in (md, html):
            self.assertNotIn(fake_secret(), out)
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))   # 외부 리소스 없음
        self.assertNotIn("<script src", html)


class McpTests(unittest.TestCase):
    def test_ranking_from_schemas(self):
        big = {"name": "t", "description": "d" * 8000, "inputSchema": {"type": "object"}}
        small = {"name": "s", "description": "short", "inputSchema": {"type": "object"}}
        data = {"mcpServers": {"light": {"command": "x", "tools": [small]},
                               "heavy": {"command": "y", "tools": [big, big]},
                               "unknown": {"command": "z"}}}
        rows = mcpweigh.weigh(data)
        self.assertEqual([r["server"] for r in rows], ["heavy", "unknown", "light"])
        self.assertEqual(rows[0]["method"], "schema")
        self.assertEqual(rows[1]["method"], "assumed")
        self.assertEqual(rows[1]["tokens"], mcpweigh.DEFAULT_TOKENS_PER_TOOL * mcpweigh.DEFAULT_TOOLS_PER_SERVER)

    def test_tools_json_list_groups_by_prefix(self):
        data = [{"name": "mcp__a__x", "description": "q" * 400}, {"name": "mcp__a__y", "description": "q"},
                {"name": "mcp__b__z", "description": "q"}]
        rows = mcpweigh.weigh(data)
        self.assertEqual(rows[0]["server"], "a")
        self.assertEqual(rows[0]["tools"], 2)

    def test_cli_with_files_and_observed(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / ".mcp.json"
            cfg.write_text(json.dumps({"mcpServers": {"gh": {"command": "x"}}}), encoding="utf-8")
            b = B()
            b.call("mcp__other__a", {}, "r")
            b.call("mcp__other__b", {}, "r")
            b.save(Path(td) / "logs" / "s.jsonl")
            out = io.StringIO()
            self.assertEqual(main(["mcp-weigh", str(cfg), "--logs", str(Path(td) / "logs")], out=out), 0)
            self.assertIn("other", out.getvalue())
            self.assertIn("gh", out.getvalue())


class RepeatTests(unittest.TestCase):
    def test_cluster_across_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            for i in range(3):
                b = B(f"s{i}")
                b.user(f"테스트 돌리고 실패하면 고쳐서 커밋해줘 {i}번째")
                b.user(["날씨 알려줘 오늘 서울", "파이썬 데코레이터 설명 부탁", "점심 메뉴 추천 한식 위주"][i])
                b.save(Path(td) / f"s{i}.jsonl")
            ps = repeats.collect_prompts(td)
            cs = [c for c in repeats.cluster(ps) if len(c.items) >= 3]
            self.assertEqual(len(cs), 1)
            self.assertEqual(len(cs[0].sessions), 3)
            md = to_markdown(repeats.build_report(ps))
            self.assertIn("테스트 돌리고", md)

    def test_prompt_clipped_to_120_and_secret_masked(self):
        with tempfile.TemporaryDirectory() as td:
            for i in range(3):
                b = B(f"s{i}")
                b.user(f"배포 전에 항상 점검 목록 확인 {fake_secret()} " + "긴내용 " * 80)
                b.save(Path(td) / f"s{i}.jsonl")
            md = to_markdown(repeats.build_report(repeats.collect_prompts(td)))
            self.assertNotIn(fake_secret(), md)
            row = [l for l in md.splitlines() if "배포 전에" in l][0]
            self.assertNotIn("긴내용 " * 30, row)


class LintTests(unittest.TestCase):
    def lint(self, text, root=None):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "CLAUDE.md"
            p.write_text(text, encoding="utf-8")
            return claudemd.lint(p, root)

    def test_duplicate_rule(self):
        r = self.lint("# 규칙\n- 커밋 메시지는 한국어로 작성한다\n- 응답은 짧게\n- 커밋 메시지는 한국어로 작성한다\n")
        self.assertTrue(any(x[1] == "중복 규칙" and x[2] == 4 for x in r))

    def test_contradiction_empty_section_stale_path_size(self):
        text = ("# 규칙\n- 테스트는 항상 mock 서버를 사용한다\n- 테스트에서 mock 서버를 사용하지 마라\n\n## 비어 있음\n\n## 경로\n- `docs/missing_file.md` 참고\n"
                + "- 채움 줄 번호 %d\n" * 1 % 1 + "".join(f"- 규칙 {i} 고유 내용 {i*7}\n" for i in range(250)))
        kinds = {x[1] for x in self.lint(text)}
        self.assertTrue({"모순 의심", "빈 섹션", "낡은 경로 의심", "크기"} <= kinds)

    def test_clean_file_has_no_findings(self):
        self.assertEqual(self.lint("# 규칙\n\n- 한국어로 답한다\n- 테스트를 먼저 돌린다\n"), [])


class RouteTests(unittest.TestCase):
    def test_no_prices_in_code_and_config_used(self):
        md = to_markdown(route.build_report(None))
        self.assertIn("단가 없음", md)
        cfg = {"models": {"small": {"in_per_mtok": 1, "out_per_mtok": 2}}}
        md2 = to_markdown(route.build_report(cfg, runs=10))
        self.assertIn("추정", md2)
        # 8000*1 + 500*2 = 9000 / 1e6 * 10 = 0.09
        self.assertIn("0.09", md2)


class CliSmoke(unittest.TestCase):
    def test_scan_writes_md_and_html(self):
        with tempfile.TemporaryDirectory() as td:
            b = B()
            b.call("Read", {"file_path": "/p/a.py"}, "x" * 4000)
            b.call("Read", {"file_path": "/p/a.py"}, "x" * 4000)
            b.save(Path(td) / "logs" / "s.jsonl")
            md, html = Path(td) / "r.md", Path(td) / "r.html"
            out = io.StringIO()
            self.assertEqual(main(["scan", str(Path(td) / "logs"), "--md", str(md), "--html", str(html)], out=out), 0)
            self.assertIn("반복 읽기", md.read_text(encoding="utf-8"))
            self.assertIn("<table>", html.read_text(encoding="utf-8"))

    def test_lint_missing_file(self):
        self.assertEqual(main(["claude-md-lint", "/nonexistent/CLAUDE.md"], out=io.StringIO()), 2)


if __name__ == "__main__":
    unittest.main()
