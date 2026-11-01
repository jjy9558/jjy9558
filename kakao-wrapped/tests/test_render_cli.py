import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from helpers import M, NAMES, fixture_path
from kakao_wrapped import cli, parser, render, stats


def many_messages():
    """A synthetic multi-day chat that exercises every card."""
    msgs = []
    people = list(NAMES)
    for day in range(1, 29):
        for i in range(14):
            who = people[(day + i) % 3]
            text = "철수야 오늘 점심 뭐 먹을까 영화 보러 가자ㅋㅋㅋ" if i % 3 == 0 else "맛집 갔다 올게ㅠㅠ"
            msgs.append(M("2026-02-%02d 1%d:%02d:%02d" % (day, i % 10, (i * 4) % 60, i), who, text))
        msgs.append(M("2026-02-%02d 02:%02d" % (day, day), "민수", "새벽에 안 자고 뭐해"))
        msgs.append(M("2026-02-%02d 20:00" % day, "영희", "https://www.youtube.com/watch?v=%d 봐봐" % day))
        msgs.append(M("2026-02-%02d 20:01" % day, "철수", "사진 2장"))
        msgs.append(M("2026-02-%02d 20:02" % day, "철수", "이모티콘"))
    return msgs


class HtmlOutput(unittest.TestCase):
    def render(self, **kw):
        return render.render_html(stats.compute_stats(many_messages(), 2026, **kw))

    def test_self_contained(self):
        html = self.render()
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertNotRegex(html, r"<script[^>]+src=")
        self.assertNotRegex(html, r"<link[^>]+href=")
        self.assertNotRegex(html, r"""(src|href)=["']https?://""")
        self.assertNotIn("@import", html)
        self.assertNotRegex(html, r"url\(\s*['\"]?https?:")
        self.assertIn("viewport", html)
        self.assertIn("prefers-color-scheme", html)
        self.assertGreaterEqual(html.count('<section class="card"'), 10)

    def test_contains_names_when_not_anonymous(self):
        html = self.render()
        for n in NAMES:
            self.assertIn(n, html)

    def test_no_raw_names_when_anonymized(self):
        html = self.render(anonymize=True, title="철수와 영희의 방")
        for n in NAMES:
            self.assertNotIn(n, html)
        self.assertIn("멤버1", html)
        self.assertNotIn("점심 뭐 먹을까", html)       # no message quotes
        self.assertNotIn("새벽에 안 자고", html)

    def test_html_escaping(self):
        msgs = [M("2026-01-01 10:00", "<b>해커</b>", "안녕"), M("2026-01-01 10:01", "영희", "응")]
        html = render.render_html(stats.compute_stats(msgs, 2026))
        self.assertNotIn("<b>해커</b>", html)
        self.assertIn("&lt;b&gt;해커&lt;/b&gt;", html)

    def test_empty_year(self):
        html = render.render_html(stats.compute_stats(many_messages(), 2030))
        self.assertIn("대화가 없어요", html)

    def test_duration_format(self):
        self.assertEqual(render.fmt_dur(20), "1분 미만")
        self.assertEqual(render.fmt_dur(150), "2분 30초")
        self.assertEqual(render.fmt_dur(3 * 3600 + 120), "3시간 2분")
        self.assertEqual(render.fmt_dur(3 * 86400 + 7200), "3일 2시간")


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_end_to_end_with_json(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "sub", "w.html")
            code, stdout, stderr = self.run_cli(fixture_path("android.txt"), "--year", "2026", "--out", out, "--json")
            self.assertEqual(code, 0, stderr)
            self.assertTrue(os.path.isfile(out))
            with open(os.path.join(d, "sub", "w.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["totals"]["messages"], 11)
            self.assertIn("동의", stderr)  # consent reminder when not anonymised

    def test_default_year_is_latest(self):
        with tempfile.TemporaryDirectory() as d:
            code, stdout, _ = self.run_cli(fixture_path("mobile.csv"), "--out", os.path.join(d, "w.html"))
            self.assertEqual(code, 0)
            self.assertIn("2026년", stdout)

    def test_anonymize_flag(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "w.html")
            code, _, stderr = self.run_cli(fixture_path("pc.txt"), "--anonymize", "--out", out, "--json")
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as fh:
                html = fh.read()
            with open(os.path.join(d, "w.json"), encoding="utf-8") as fh:
                js = fh.read()
            for n in NAMES:
                self.assertNotIn(n, html)
                self.assertNotIn(n, js)

    def test_errors(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = self.run_cli(os.path.join(d, "nope.txt"))
            self.assertEqual(code, 2)
            bad = os.path.join(d, "bad.txt")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("이건 카톡 파일이 아니에요\n")
            code, _, err = self.run_cli(bad, "--out", os.path.join(d, "x.html"))
            self.assertEqual(code, 2)
            code, _, err = self.run_cli(fixture_path("android.txt"), "--year", "1999", "--out", os.path.join(d, "x.html"))
            self.assertEqual(code, 2)
            self.assertIn("2026", err)


if __name__ == "__main__":
    unittest.main()
