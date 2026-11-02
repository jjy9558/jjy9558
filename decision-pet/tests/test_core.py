import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta

from decision_pet import core, decide
from decision_pet.core import Pet, new_task, pick, epitaph, parse_markdown_todos
from decision_pet.experiments import DEFAULT_EXPERIMENTS, experiment_for, load_experiments
from decision_pet.storage import Store, home_dir

NOW = datetime(2026, 11, 20, 12, 0, 0)


def mk(i, title, effort=15, days_old=0, skipped=0, last=None, status="todo"):
    t = new_task(i, title, effort, now=NOW - timedelta(days=days_old))
    t["times_skipped"] = skipped
    t["status"] = status
    if last is not None:
        t["last_suggested"] = core.fmt(NOW - last)
    return t


class FakeResp:
    def __init__(self, content):
        self._b = json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    def read(self):
        return self._b

    def close(self):
        pass


class PetCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock_now = NOW
        self.opener = None
        self.pet = self.make()

    def make(self, **kw):
        return Pet(Store(self.tmp.name), rng=random.Random(1), clock=lambda: self.clock_now, **kw)


class PickTests(unittest.TestCase):
    def test_prefers_15_min(self):
        tasks = [mk(1, "a", 60), mk(2, "b", 15), mk(3, "c", 5)]
        r = pick(tasks, NOW, random.Random(0))
        self.assertEqual(r["task"]["id"], 2)

    def test_penalizes_recently_suggested(self):
        tasks = [mk(1, "a", 15, last=timedelta(minutes=10)), mk(2, "b", 15)]
        self.assertEqual(pick(tasks, NOW, random.Random(0))["task"]["id"], 2)

    def test_boosts_old(self):
        tasks = [mk(1, "a", 15, days_old=9), mk(2, "b", 15)]
        r = pick(tasks, NOW, random.Random(0))
        self.assertEqual(r["task"]["id"], 1)
        self.assertIn(("9일째 미뤄서", 6.0), r["reasons"])

    def test_explanation_text(self):
        r = pick([mk(1, "a", 15, days_old=3)], NOW, random.Random(0))
        self.assertIn("3일째 미뤄서 +2점", r["explanation"])
        self.assertIn("15분짜리라 부담이 적어서 +3점", r["explanation"])

    def test_penalty_in_explanation(self):
        r = pick([mk(1, "a", 15, last=timedelta(minutes=5))], NOW, random.Random(0))
        self.assertIn("-4점", r["explanation"])

    def test_seeded_reproducible(self):
        tasks = [mk(i, "t%d" % i, 15) for i in range(6)]
        a = pick(tasks, NOW, random.Random(42))["task"]["id"]
        b = pick(tasks, NOW, random.Random(42))["task"]["id"]
        self.assertEqual(a, b)

    def test_randomness_is_small(self):
        r = pick([mk(1, "a", 15)], NOW, random.Random(7))
        self.assertTrue(3.0 <= r["score"] <= 3.5)

    def test_injected_rng_breaks_ties(self):
        class Rng:
            def __init__(self, seq):
                self.seq = iter(seq)

            def uniform(self, a, b):
                return next(self.seq)

        tasks = [mk(1, "a"), mk(2, "b")]
        self.assertEqual(pick(tasks, NOW, Rng([0.4, 0.1]))["task"]["id"], 1)
        self.assertEqual(pick(tasks, NOW, Rng([0.1, 0.4]))["task"]["id"], 2)

    def test_ignores_non_todo_and_empty(self):
        self.assertIsNone(pick([mk(1, "a", status="done"), mk(2, "b", status="buried")], NOW))
        self.assertIsNone(pick([], NOW))


class GraveyardTests(PetCase):
    def test_bury_by_skips(self):
        t = self.pet.add("블로그 시작하기")
        for _ in range(4):
            _, b = self.pet.skip(t["id"])
            self.assertFalse(b)
        _, b = self.pet.skip(t["id"])
        self.assertTrue(b)
        self.assertEqual(t["status"], "buried")
        self.assertEqual(t["bury_reason"], "skips")

    def test_bury_by_age(self):
        t = self.pet.add("오래된 일")
        self.clock_now = NOW + timedelta(days=31)
        buried = self.pet.sweep()
        self.assertEqual([x["id"] for x in buried], [t["id"]])
        self.assertEqual(t["bury_reason"], "age")

    def test_not_buried_at_exactly_30_days(self):
        self.pet.add("x")
        self.clock_now = NOW + timedelta(days=30)
        self.assertEqual(self.pet.sweep(), [])

    def test_epitaph_format(self):
        t = mk(1, "블로그 시작하기", days_old=0)
        t["created"] = "2026-10-20T09:00:00"
        t["times_skipped"] = 7
        core.bury(t, datetime(2026, 11, 20, 9, 0, 0), "skips")
        self.assertEqual(epitaph(t), "여기 잠들다: 블로그 시작하기 (2026.10.20~2026.11.20, 미룬 횟수 7)")

    def test_resurrect(self):
        t = self.pet.add("부활할 일")
        for _ in range(5):
            self.pet.skip(t["id"])
        self.assertEqual(len(self.pet.graveyard()), 1)
        self.clock_now = NOW + timedelta(days=40)
        self.pet.resurrect(t["id"])
        self.assertEqual(t["status"], "todo")
        self.assertEqual(t["times_skipped"], 0)
        self.assertEqual(self.pet.sweep(), [])  # 바로 다시 묻히지 않는다
        self.assertEqual(self.pet.graveyard(), [])

    def test_resurrect_non_buried_fails(self):
        t = self.pet.add("x")
        with self.assertRaises(ValueError):
            self.pet.resurrect(t["id"])

    def test_suggest_sweeps_first(self):
        self.pet.add("old")
        self.clock_now = NOW + timedelta(days=45)
        self.assertIsNone(self.pet.suggest())
        self.assertEqual(len(self.pet.graveyard()), 1)

    def test_custom_thresholds(self):
        self.pet.config["max_skips"] = 2
        t = self.pet.add("x")
        self.pet.skip(t["id"])
        _, b = self.pet.skip(t["id"])
        self.assertTrue(b)


class FlowTests(PetCase):
    def test_suggest_done_skip_current(self):
        a = self.pet.add("a")
        res = self.pet.suggest()
        self.assertEqual(res["task"]["id"], a["id"])
        self.assertEqual(self.pet.current_task()["id"], a["id"])
        self.pet.done()
        self.assertEqual(a["status"], "done")
        self.assertIsNone(self.pet.current_task())
        with self.assertRaises(LookupError):
            self.pet.done()

    def test_skip_current(self):
        self.pet.add("a")
        self.pet.suggest()
        t, b = self.pet.skip()
        self.assertEqual((t["times_skipped"], b), (1, False))

    def test_add_validation(self):
        with self.assertRaises(ValueError):
            self.pet.add("  ")
        with self.assertRaises(ValueError):
            self.pet.add("x", effort=7)


class DecideTests(PetCase):
    def test_option_parsing(self):
        self.assertEqual(decide.parse_options("라면 vs 김밥 vs 굶기"), ["라면", "김밥", "굶기"])
        self.assertEqual(decide.parse_options("a, b"), ["a", "b"])
        with self.assertRaises(ValueError):
            decide.parse_options("하나만")
        with self.assertRaises(ValueError):
            decide.parse_options("a,b,c,d,e,f")
        with self.assertRaises(ValueError):
            decide.parse_options("a vs a")

    def test_coin_seeded(self):
        r = decide.decide_coin(["a", "b", "c"], random.Random(3))
        self.assertIn(r["choice"], "abc")
        self.assertEqual(r["mode"], "coin")
        self.assertTrue(r["reason"])
        self.assertEqual(r["choice"], decide.decide_coin(["a", "b", "c"], random.Random(3))["choice"])

    def test_weighted_score_math(self):
        crit = [("맛", 3), ("가격", 1)]
        scores = {"라면": {"맛": 3, "가격": 5}, "김밥": {"맛": 5, "가격": 1}}
        s = decide.score_options(["라면", "김밥"], crit, scores)
        self.assertAlmostEqual(s["라면"], (3 * 3 + 5) / 4)
        self.assertAlmostEqual(s["김밥"], (3 * 5 + 1) / 4)
        r = decide.decide_scores(["라면", "김밥"], crit, scores)
        self.assertEqual(r["choice"], "김밥")
        self.assertIn("맛", r["reason"])

    def test_weights_flip_result(self):
        scores = {"라면": {"맛": 3, "가격": 5}, "김밥": {"맛": 5, "가격": 1}}
        r = decide.decide_scores(["라면", "김밥"], [("맛", 1), ("가격", 3)], scores)
        self.assertEqual(r["choice"], "라면")

    def test_score_validation(self):
        scores = {"a": {"x": 6, "y": 1}, "b": {"x": 1, "y": 1}}
        with self.assertRaises(ValueError):
            decide.decide_scores(["a", "b"], [("x", 1), ("y", 1)], scores)
        with self.assertRaises(ValueError):
            decide.decide_scores(["a", "b"], [("x", 1)], scores)
        with self.assertRaises(ValueError):
            decide.decide_scores(["a", "b"], [("x", 0), ("y", 1)],
                                 {"a": {"x": 1, "y": 1}, "b": {"x": 1, "y": 1}})

    def test_score_tie_uses_rng(self):
        scores = {"a": {"x": 3, "y": 3}, "b": {"x": 3, "y": 3}}
        r = decide.decide_scores(["a", "b"], [("x", 1), ("y", 1)], scores, random.Random(0))
        self.assertIn(r["choice"], ("a", "b"))
        self.assertIn("동점", r["reason"])

    def test_llm_success_json(self):
        seen = {}

        def opener(req, timeout=None):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode())
            return FakeResp('{"choice": "김밥", "reason": "든든해요"}')

        r = decide.decide_llm(["라면", "김밥"], {"endpoint": "http://x/v1", "model": "m"}, opener=opener)
        self.assertEqual((r["mode"], r["choice"], r["reason"]), ("llm", "김밥", "든든해요"))
        self.assertEqual(seen["url"], "http://x/v1/chat/completions")
        self.assertEqual(seen["body"]["model"], "m")

    def test_llm_plain_text_answer(self):
        r = decide.decide_llm(["라면", "김밥"], {"endpoint": "http://x/v1"},
                              opener=lambda req, timeout=None: FakeResp("김밥이 좋겠어요"))
        self.assertEqual(r["choice"], "김밥")

    def test_llm_fallback_unreachable(self):
        def boom(req, timeout=None):
            raise OSError("connection refused")

        r = decide.decide_llm(["라면", "김밥"], {"endpoint": "http://x/v1"}, random.Random(0), opener=boom)
        self.assertEqual(r["mode"], "llm-fallback")
        self.assertIn(r["choice"], ["라면", "김밥"])
        self.assertIn("연결하지 못해", r["reason"])

    def test_llm_fallback_garbage(self):
        r = decide.decide_llm(["라면", "김밥"], {"endpoint": "http://x/v1"}, random.Random(0),
                              opener=lambda req, timeout=None: FakeResp("글쎄요"))
        self.assertEqual(r["mode"], "llm-fallback")

    def test_pet_decide_records(self):
        r = self.pet.decide(["라면", "김밥", "굶기"])
        self.assertEqual(len(self.pet.state["decisions"]), 1)
        self.assertEqual(self.pet.state["decisions"][0]["choice"], r["choice"])

    def test_pet_decide_llm_uses_config_and_falls_back(self):
        def boom(req, timeout=None):
            raise OSError("x")

        pet = self.make(opener=boom)
        r = pet.decide(["a", "b"], "llm")
        self.assertEqual(r["mode"], "llm-fallback")

    def test_pet_decide_score(self):
        r = self.pet.decide(["a", "b"], "score", [("x", 1), ("y", 1)],
                            {"a": {"x": 5, "y": 5}, "b": {"x": 1, "y": 1}})
        self.assertEqual(r["choice"], "a")

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            self.pet.decide(["a", "b"], "magic")


class SatisfactionTests(PetCase):
    def test_followup_next_day_only(self):
        r = self.pet.decide(["a", "b"])
        self.assertEqual(self.pet.pending_followups(), [])
        self.clock_now = NOW + timedelta(days=1)
        self.assertEqual([d["id"] for d in self.pet.pending_followups()], [r["id"]])

    def test_rate_and_history(self):
        a = self.pet.decide(["a", "b"])
        b = self.pet.decide(["a", "b"])
        c = self.pet.decide(["a", "b"], "score", [("x", 1), ("y", 1)],
                            {"a": {"x": 5, "y": 5}, "b": {"x": 1, "y": 1}})
        self.pet.rate_decision(a["id"], 4)
        self.pet.rate_decision(b["id"], 2)
        self.pet.rate_decision(c["id"], 5)
        h = self.pet.satisfaction_history()
        self.assertEqual(h["coin"], (3.0, 2))
        self.assertEqual(h["score"], (5.0, 1))
        self.assertAlmostEqual(h["all"][0], 3.67)
        self.clock_now = NOW + timedelta(days=2)
        self.assertEqual(self.pet.pending_followups(), [])

    def test_dismiss(self):
        a = self.pet.decide(["a", "b"])
        self.clock_now = NOW + timedelta(days=1)
        self.pet.dismiss_followup(a["id"])
        self.assertEqual(self.pet.pending_followups(), [])
        self.assertEqual(self.pet.satisfaction_history(), {})

    def test_rate_range(self):
        a = self.pet.decide(["a", "b"])
        with self.assertRaises(ValueError):
            self.pet.rate_decision(a["id"], 6)


class MarkdownTests(PetCase):
    MD = """# TODO
- [ ] 블로그 초안 다듬기 [15m] #글쓰기
- [x] 이미 끝낸 일
  - [ ] 들여쓴 하위 항목 (5분)
* [ ] 별표 항목
- 그냥 목록
- [ ]
"""

    def test_parse(self):
        items = parse_markdown_todos(self.MD)
        self.assertEqual([i["title"] for i in items], ["블로그 초안 다듬기", "들여쓴 하위 항목", "별표 항목"])
        self.assertEqual(items[0]["tags"], ["글쓰기"])
        self.assertEqual([i["effort"] for i in items], [15, 5, 15])

    def test_import_dedupes(self):
        self.assertEqual(len(self.pet.import_markdown(self.MD)), 3)
        self.assertEqual(len(self.pet.import_markdown(self.MD)), 0)
        self.assertEqual(len(self.pet.tasks("todo")), 3)


class ExperimentTests(PetCase):
    def test_default_count_and_korean(self):
        self.assertGreaterEqual(len(DEFAULT_EXPERIMENTS), 20)
        self.assertIn("안 써본 단축키 1개 익히기", DEFAULT_EXPERIMENTS)

    def test_same_day_same_experiment(self):
        d = NOW.date()
        self.assertEqual(experiment_for(d, DEFAULT_EXPERIMENTS), experiment_for(d, DEFAULT_EXPERIMENTS))

    def test_varies_over_days(self):
        seen = {experiment_for((NOW + timedelta(days=i)).date(), DEFAULT_EXPERIMENTS) for i in range(30)}
        self.assertGreater(len(seen), 5)

    def test_user_file_overrides(self):
        with open(self.pet.store.experiments_path, "w", encoding="utf-8") as f:
            f.write("# 주석\n나만의 실험\n\n")
        self.assertEqual(load_experiments(self.pet.store.experiments_path), ["나만의 실험"])
        self.assertEqual(self.pet.experiment_today()[0], "나만의 실험")

    def test_missing_file_uses_default(self):
        self.assertEqual(load_experiments(os.path.join(self.tmp.name, "nope.txt")), DEFAULT_EXPERIMENTS)

    def test_complete(self):
        self.assertFalse(self.pet.experiment_today()[1])
        self.pet.complete_experiment()
        self.assertTrue(self.pet.experiment_today()[1])


class StorageTests(PetCase):
    def test_roundtrip(self):
        t = self.pet.add("한글 제목", 5, ["a"])
        self.pet.decide(["가", "나"])
        pet2 = self.make()
        self.assertEqual(pet2.get(t["id"])["title"], "한글 제목")
        self.assertEqual(len(pet2.state["decisions"]), 1)
        with open(Store(self.tmp.name).path, encoding="utf-8") as f:
            self.assertIn("한글 제목", f.read())  # ensure_ascii=False

    def test_ids_increment_after_reload(self):
        self.pet.add("a")
        pet2 = self.make()
        self.assertEqual(pet2.add("b")["id"], 2)

    def test_home_dir_env_override(self):
        self.assertEqual(str(home_dir({"DECISION_PET_HOME": "/x/y", "APPDATA": "/a"})), "/x/y")
        self.assertEqual(home_dir({"APPDATA": "/a"}), home_dir({"APPDATA": "/a"}))
        self.assertTrue(str(home_dir({"APPDATA": "/a"})).replace("\\", "/").endswith("a/decision-pet"))
        self.assertTrue(str(home_dir({})).endswith(".decision-pet"))

    def test_config_defaults_merged_for_old_file(self):
        Store(self.tmp.name).dir.mkdir(parents=True, exist_ok=True)
        with open(Store(self.tmp.name).path, "w", encoding="utf-8") as f:
            json.dump({"tasks": [], "config": {"nudge_minutes": 5, "llm": {"model": "m"}}}, f)
        st = Store(self.tmp.name).load()
        self.assertEqual(st["config"]["nudge_minutes"], 5)
        self.assertEqual(st["config"]["max_skips"], 5)
        self.assertEqual(st["config"]["llm"]["model"], "m")
        self.assertIn("endpoint", st["config"]["llm"])

    def test_empty_dir_default_state(self):
        self.assertEqual(Store(os.path.join(self.tmp.name, "new")).load()["tasks"], [])


class CliTests(PetCase):
    def run_cli(self, *argv, inputs=()):
        from decision_pet.cli import run
        lines = []
        it = iter(inputs)
        code = run(list(argv), pet=self.pet, out=lines.append, inp=lambda prompt="": next(it))
        return code, "\n".join(lines)

    def test_flow(self):
        code, out = self.run_cli("add", "블로그 초안 다듬기", "--effort", "15")
        self.assertEqual(code, 0)
        self.assertIn("블로그 초안 다듬기", out)
        _, out = self.run_cli("now")
        self.assertIn("오늘은 이거 해", out)
        self.assertIn("이유", out)
        _, out = self.run_cli("done")
        self.assertIn("끝냈어요", out)
        code, _ = self.run_cli("done")
        self.assertEqual(code, 1)

    def test_decide_and_graveyard(self):
        _, out = self.run_cli("decide", "라면", "김밥", "굶기")
        self.assertIn("결정!", out)
        _, out = self.run_cli("decide", "a", "b", "--mode", "score", "--criteria", "x:1,y:2",
                              inputs=["5", "5", "1", "1"])
        self.assertIn("결정! → a", out)
        self.run_cli("add", "x")
        for _ in range(5):
            self.run_cli("skip", "1")
        _, out = self.run_cli("graveyard")
        self.assertIn("여기 잠들다: x", out)
        _, out = self.run_cli("graveyard", "--resurrect", "1")
        self.assertIn("부활", out)

    def test_decide_error(self):
        code, _ = self.run_cli("decide", "혼자")
        self.assertEqual(code, 1)

    def test_import(self):
        p = os.path.join(self.tmp.name, "t.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("- [ ] 하나\n- [ ] 둘\n")
        _, out = self.run_cli("import-todos", p)
        self.assertIn("2개 가져왔어요", out)

    def test_core_does_not_import_tkinter(self):
        import subprocess, sys
        code = "import decision_pet.cli, decision_pet.core, sys; sys.exit(1 if 'tkinter' in sys.modules else 0)"
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(subprocess.call([sys.executable, "-c", code], cwd=root), 0)


if __name__ == "__main__":
    unittest.main()
