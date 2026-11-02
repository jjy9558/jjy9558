import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from storyfactory import jarvis, lint as lint_mod
from storyfactory._jjkit.llm import LLMClient
from storyfactory.pipeline import Options, run
from storyfactory.seeds import Sources

from tests.helpers import (NOW, REAL_NAME, REAL_PLACE, FakeOpener, chat_reply, make_all, tree_state)

LINTER = lint_mod.find_linter_dir()


def all_text(run_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(run_dir.iterdir()) if p.is_file())


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        self.src = make_all(self.dir)

    def opts(self, src=None, out="out", **kw):
        src = src or self.src
        base = dict(names=[REAL_NAME], places=[REAL_PLACE])
        base.update(kw)
        return Options(sources=src, out_dir=self.dir / out, now=NOW, **base)


class TemplatePipelineTests(Base):
    def test_no_llm_template_complete_and_original_untouched(self):
        o = self.opts(do_lint=False)
        before = tree_state(self.dir)
        res = run(o)
        after = {k: v for k, v in tree_state(self.dir).items() if not k.startswith("out/")}
        self.assertEqual(after, before)                       # 소스 읽기 전용
        self.assertEqual(len(res.candidates), 2)
        self.assertTrue(all(not d.used_llm for d in res.drafts))
        for d, _, _ in res.candidates:
            txt = (res.run_dir / (d.card_id + ".txt")).read_text(encoding="utf-8")
            self.assertGreaterEqual(len(txt.strip().splitlines()), 4)
            meta = json.loads((res.run_dir / (d.card_id + ".meta.json")).read_text(encoding="utf-8"))
            for key in ("title", "claims", "thumbnail_text", "ai_used", "title_candidates"):
                self.assertTrue(meta[key], key)
            self.assertEqual(set(meta["ai_used"]), {"voice", "image", "script"})
            self.assertFalse(meta["ai_used"]["script"])
            self.assertTrue(meta["ai_used"]["voice"])
            self.assertTrue(meta["ai_disclosure"])
        report = res.report_path.read_text(encoding="utf-8")
        self.assertIn("## 후보 1.", report)
        self.assertIn("## 후보 2.", report)
        self.assertNotIn("## 후보 3.", report)
        self.assertIn("생략", report)

    def test_names_never_in_any_output(self):
        res = run(self.opts(do_lint=False, max_drafts=10))
        text = all_text(res.run_dir)
        for orig in (REAL_NAME, "민수", REAL_PLACE, "5678", "010-1234", "XYZZY", "PRIVATEZZ", "멤버1"):
            self.assertNotIn(orig, text, orig)
        self.assertIn("journal-entry-1", [d.card_id for d in res.drafts])    # 일기 카드는 가명화되어 살아남음

    def test_sensitive_cards_excluded(self):
        (self.dir / "more").mkdir()
        src = make_all(self.dir / "more", journal_ids=(1, 3, 4))
        res = run(self.opts(src, do_lint=False, max_drafts=10))
        ex = {c: r for c, _, r in res.excluded}
        self.assertEqual(ex["journal-entry-3"], ["medical"])
        self.assertEqual(ex["journal-entry-4"], ["defamation"])
        ids = {d.card_id for d in res.drafts}
        self.assertNotIn("journal-entry-3", ids)
        self.assertNotIn("journal-entry-4", ids)
        text = all_text(res.run_dir)
        self.assertNotIn("우울증", text)
        self.assertNotIn("사기꾼", text)
        self.assertIn("의료", res.report_path.read_text(encoding="utf-8"))
        run_json = json.loads((res.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(len(run_json["excluded"]), 2)

    def test_only_sensitive_cards_gives_empty_report(self):
        src = Sources(voice_journal=self.src.voice_journal, journal_entries=[3, 4])
        res = run(self.opts(src, do_lint=False))
        self.assertEqual(res.candidates, [])
        self.assertIn("후보가 없습니다", res.report_path.read_text(encoding="utf-8"))

    def test_deterministic_with_fixed_seed(self):
        a = run(self.opts(out="a", do_lint=False, seed=5))
        b = run(self.opts(out="b", do_lint=False, seed=5))
        self.assertEqual(a.report_path.read_text(encoding="utf-8"), b.report_path.read_text(encoding="utf-8"))
        self.assertEqual([d.card_id for d, _, _ in a.candidates], [d.card_id for d, _, _ in b.candidates])
        files_a = {p.name: p.read_bytes() for p in a.run_dir.iterdir() if p.is_file()}
        files_b = {p.name: p.read_bytes() for p in b.run_dir.iterdir() if p.is_file()}
        self.assertEqual(files_a, files_b)
        scripts = {run(self.opts(out="s%d" % s, do_lint=False, seed=s)).candidates[0][0].lines[0] for s in range(6)}
        self.assertGreater(len(scripts), 0)

    def test_rerun_replaces_own_files_only(self):
        o = self.opts(do_lint=False)
        res = run(o)
        stale = res.run_dir / "stale.txt"
        stale.write_text("old", encoding="utf-8")
        mine = res.run_dir / "notes.keep"
        mine.write_text("keep", encoding="utf-8")
        run(o)
        self.assertFalse(stale.exists())
        self.assertTrue(mine.exists())


class LlmTests(Base):
    GOOD = {"title": "설거지 9일 미룬 사람의 결말", "title_candidates": ["설거지 9일 미룬 사람의 결말", "싱크대 9일째"],
            "thumbnail_text": "9일째", "claims": ["설거지 9일"],
            "script_lines": ["설거지를 9일째 안 했습니다.", "그릇이 싱크대를 점령했습니다.", "그래도 내일 한다고 말했습니다.",
                             "결말은 오늘 밤 설거지입니다.", "여러분은 9일까지 가지 마세요."]}

    def client(self, *replies):
        op = FakeOpener(*replies)
        return LLMClient("http://127.0.0.1:1/v1", opener=op), op

    def test_llm_draft_used_and_recorded(self):
        c, op = self.client(chat_reply(self.GOOD))
        res = run(self.opts(do_lint=False, client=c, max_drafts=1, seed=9))
        d = res.drafts[0]
        self.assertTrue(d.used_llm)
        self.assertEqual(d.title, self.GOOD["title"])
        meta = json.loads((res.run_dir / (d.card_id + ".meta.json")).read_text(encoding="utf-8"))
        self.assertTrue(meta["ai_used"]["script"])
        self.assertIn("llm_model", meta["story_factory"])
        sent = json.dumps(op.last_json(), ensure_ascii=False)
        self.assertNotIn(REAL_NAME, sent)
        self.assertEqual(op.last_json()["seed"], 9)
        self.assertIn("후보 1", res.report_path.read_text(encoding="utf-8"))
        self.assertIn("LLM 사연체", res.report_path.read_text(encoding="utf-8"))

    def test_llm_failures_fall_back_to_template(self):
        bad_replies = [
            chat_reply("이건 JSON이 아님"),
            chat_reply({"title": "x", "script_lines": ["a"]}),
            chat_reply(dict(self.GOOD, script_lines=["설거지 얘기", "둘", "셋", "넷"])),          # 제목 숫자 9 가 대본에 없음
            chat_reply(dict(self.GOOD, script_lines=self.GOOD["script_lines"] + ["김민수 씨가 사기꾼이다"])),
            chat_reply(dict(self.GOOD, script_lines=self.GOOD["script_lines"] + ["병원에서 치료를 받았다"])),
            urllib.error.URLError("down"),
        ]
        for i, reply in enumerate(bad_replies):
            with self.subTest(i=i):
                c, _ = self.client(reply)
                res = run(self.opts(out="o%d" % i, do_lint=False, client=c, max_drafts=1))
                d = res.drafts[0]
                self.assertFalse(d.used_llm)
                self.assertFalse(d.ai_used["script"])
                self.assertNotIn("사기꾼", all_text(res.run_dir))


class LinterTests(Base):
    @unittest.skipIf(LINTER is None, "shorts-linter 없음")
    def test_real_linter_via_subprocess(self):
        res = run(self.opts())
        self.assertTrue(res.candidates)
        for d, lr, _ in res.candidates:
            self.assertTrue(lr.available, lr.note)
            self.assertIn(lr.promise_status, ("ok", "warn"))
            self.assertTrue(lr.title_scores)
            self.assertIn("공개 문구", lr.disclose)
            self.assertEqual(d.title, lr.title_scores[0][1])      # 가장 점수 높은 제목이 최종 제목
            meta = json.loads((res.run_dir / (d.card_id + ".meta.json")).read_text(encoding="utf-8"))
            self.assertEqual(meta["title"], d.title)
        report = res.report_path.read_text(encoding="utf-8")
        for token in ("약속 일치(promise)", "제목 점수(titles)", "유사도(similar)", "AI 공개 문구 초안"):
            self.assertIn(token, report)
        # 인용·사연이 약속과 어긋나는 제목은 promise 가 잡아낸다
        meta_path = res.run_dir / (res.candidates[0][0].card_id + ".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["title"] = "99일 동안 숨겨진 반전 결말"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        results = lint_mod.Linter(LINTER, res.run_dir).promise(meta_path)
        self.assertNotEqual({r["status"] for r in results}, {"ok"})

    def test_linter_absent_exits_normally(self):
        with mock.patch.object(lint_mod, "find_linter_dir", return_value=None):
            res = run(self.opts())
        self.assertEqual(len(res.candidates), 2)
        for _, lr, _ in res.candidates:
            self.assertFalse(lr.available)
        report = res.report_path.read_text(encoding="utf-8")
        self.assertIn("생략", report)
        self.assertIn("shorts-linter 를 찾지 못해", report)

    def test_linter_that_crashes_is_skipped(self):
        fake = self.dir / "fake-linter"
        (fake / "shortslint").mkdir(parents=True)
        (fake / "shortslint" / "__init__.py").write_text("", encoding="utf-8")
        (fake / "shortslint" / "__main__.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        res = run(self.opts(linter_dir=str(fake)))
        self.assertEqual(len(res.candidates), 2)
        self.assertTrue(all(not lr.available for _, lr, _ in res.candidates))
        self.assertIn("실행에 실패", res.report_path.read_text(encoding="utf-8"))

    def test_find_linter_dir_env_and_explicit(self):
        fake = self.dir / "lintdir"
        (fake / "shortslint").mkdir(parents=True)
        (fake / "shortslint" / "__init__.py").write_text("", encoding="utf-8")
        self.assertEqual(lint_mod.find_linter_dir(fake), fake)
        self.assertNotEqual(lint_mod.find_linter_dir(self.dir / "none"), self.dir / "none")


class JarvisTests(Base):
    SUMMARY = {"candidates": [{"id": "a", "title": "t", "file": "a.txt"}]}

    def test_submit_ok(self):
        op = FakeOpener({"job": {"id": 7}})
        ok, msg = jarvis.submit(self.SUMMARY, url="http://127.0.0.1:8800", device="pc", token="tok", opener=op)
        self.assertTrue(ok)
        self.assertIn("#7", msg)
        body = op.last_json()
        self.assertEqual(body["type"], "story.batch")
        self.assertEqual(op.requests[-1].get_header("X-jarvis-device"), "pc")
        self.assertNotIn("lines", json.dumps(body))

    def test_submit_unreachable_or_unset_is_skipped(self):
        ok, msg = jarvis.submit(self.SUMMARY, url="http://127.0.0.1:8800", device="pc", token="tok",
                                opener=FakeOpener(urllib.error.URLError("refused")))
        self.assertFalse(ok)
        self.assertIn("건너뜀", msg)
        ok, msg = jarvis.submit(self.SUMMARY, url="http://127.0.0.1:8800", device="", token="")
        self.assertFalse(ok)
        self.assertIn("건너뜀", msg)

    def test_pipeline_submit_jarvis(self):
        op = FakeOpener({"job": {"id": 1}})
        cfg = {"url": "http://127.0.0.1:8800", "device": "pc", "token": "tok", "opener": op}
        res = run(self.opts(do_lint=False, jarvis=cfg))
        self.assertIn("등록 완료", res.jarvis_msg)
        self.assertEqual(len(op.last_json()["payload"]["candidates"]), 2)
        bad = dict(cfg, opener=FakeOpener(urllib.error.URLError("down")))
        res = run(self.opts(out="o2", do_lint=False, jarvis=bad))
        self.assertIn("건너뜀", res.jarvis_msg)
        self.assertEqual(len(res.candidates), 2)


if __name__ == "__main__":
    unittest.main()
