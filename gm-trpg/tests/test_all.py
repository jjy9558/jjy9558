import inspect
import json
import threading
import unittest
import urllib.request
import urllib.error

from gmtrpg import context as ctx
from gmtrpg.cli import build_parser, run_wizard
from gmtrpg.dice import Dice
from gmtrpg.engine import GameMaster
from gmtrpg.llm import AnthropicLLM, FakeLLM, OpenAICompatLLM
from gmtrpg.parser import parse_gm_output
from gmtrpg.presets import apply_preset
from gmtrpg.state import World
from gmtrpg.web import make_server


def block(d):
    return "```json\n" + json.dumps(d, ensure_ascii=False) + "\n```"


def make_gm(replies, seed=1, **kw):
    w = World(":memory:")
    apply_preset(w, "판타지 던전", "t")
    return GameMaster(w, FakeLLM(replies), Dice(seed), **kw)


class ParserTests(unittest.TestCase):
    def test_valid(self):
        t = "문이 열린다.\n" + block({"state_updates": {"move_to": "회랑", "hp_change": -2, "add_item": "열쇠"}})
        p = parse_gm_output(t)
        self.assertEqual(p.narration, "문이 열린다.")
        self.assertEqual(p.updates["move_to"], "회랑")
        self.assertEqual(p.updates["add_item"], [{"name": "열쇠", "qty": 1}])
        self.assertEqual(p.updates["hp_change"], -2)

    def test_malformed_keeps_narration(self):
        p = parse_gm_output('이야기\n```json\n{"state_updates": {oops\n```')
        self.assertEqual(p.narration, "이야기")
        self.assertEqual(p.updates, {})
        self.assertTrue(p.errors)

    def test_truncated_fence(self):
        p = parse_gm_output('이야기\n```json\n{"state_updates": {"move_to": "x"')
        self.assertEqual(p.narration, "이야기")
        self.assertTrue(p.errors)

    def test_trailing_comma_and_bare_json(self):
        p = parse_gm_output('서술 {"move_to": "숲", "add_fact": ["a",],}')
        self.assertEqual(p.updates.get("move_to"), None)  # 후행 쉼표는 bare에서는 실패
        p = parse_gm_output('서술\n```json\n{"move_to": "숲", "add_fact": ["a",],}\n```')
        self.assertEqual(p.updates["move_to"], "숲")

    def test_validation_drops_bad(self):
        raw = {"state_updates": {"hp_change": "abc", "add_npc": [{"traits": "x"}, {"name": "철수"}],
                                 "move_to": 5, "quest_add": [{"nope": 1}], "add_fact": [None, "ok"]}}
        p = parse_gm_output(block(raw))
        self.assertNotIn("hp_change", p.updates)
        self.assertEqual([n["name"] for n in p.updates["add_npc"]], ["철수"])
        self.assertEqual(p.updates["move_to"], "5")
        self.assertNotIn("quest_add", p.updates)
        self.assertEqual(p.updates["add_fact"], ["ok"])
        self.assertTrue(p.errors)

    def test_hp_clamped_and_roll(self):
        p = parse_gm_output(block({"roll": {"skill": "설득", "dc": 99, "mode": "유리"}, "state_updates": {"hp_change": -99999}}))
        self.assertEqual(p.roll["dc"], 40)
        self.assertEqual(p.roll["mode"], "advantage")
        self.assertEqual(p.updates["hp_change"], -999)


class DiceTests(unittest.TestCase):
    def test_seeded_deterministic(self):
        self.assertEqual(Dice(5).roll("2d6+1"), Dice(5).roll("2d6+1"))
        a = Dice(5)
        total, rolls, mod = a.roll("3d6-2")
        self.assertEqual(total, sum(rolls) - 2)
        self.assertTrue(all(1 <= r <= 6 for r in rolls))

    def test_bad_expr(self):
        with self.assertRaises(ValueError):
            Dice().roll("abc")
        with self.assertRaises(ValueError):
            Dice().roll("1000d6")

    def test_check_math(self):
        d = Dice(3)
        r = d.check("설득", 12, 2)
        self.assertEqual(r["total"], r["natural"] + 2)
        self.assertEqual(r["success"], r["natural"] == 20 or (r["natural"] != 1 and r["total"] >= 12))

    def test_advantage_disadvantage(self):
        for seed in range(30):
            adv = Dice(seed).check("x", 10, 0, "advantage")
            dis = Dice(seed).check("x", 10, 0, "disadvantage")
            self.assertEqual(len(adv["rolls"]), 2)
            self.assertEqual(adv["natural"], max(adv["rolls"]))
            self.assertEqual(dis["natural"], min(dis["rolls"]))
            self.assertGreaterEqual(adv["natural"], dis["natural"])


class StateAndContextTests(unittest.TestCase):
    def test_context_contents(self):
        gm = make_gm(["x"])
        c = ctx.build_context(gm.world, "마리에게 말을 건다")
        for s in ("달빛 마을 여관", "여관 주인 마리", "잿빛 회랑의 첫 유물", "낡은 단검", "HP 24/24"):
            self.assertIn(s, c)

    def test_retrieval_relevance(self):
        facts = [{"id": 1, "text": "마리는 남편을 기다린다"}, {"id": 2, "text": "용의 보물은 동굴에 있다"},
                 {"id": 3, "text": "시장에서 사과를 판다"}]
        r = ctx.retrieve_facts(facts, "동굴로 들어가 용을 찾는다")
        self.assertEqual(r[0]["id"], 2)
        self.assertNotIn(3, [f["id"] for f in r])
        self.assertEqual(ctx.retrieve_facts(facts, "zzz"), [])

    def test_context_includes_only_relevant_facts(self):
        gm = make_gm(["x"])
        gm.world.add_fact("검은 늑대가 북쪽 숲을 지배한다")
        self.assertIn("검은 늑대", ctx.build_context(gm.world, "늑대를 쫓는다"))
        self.assertNotIn("검은 늑대", ctx.build_context(gm.world, "빵을 먹는다"))

    def test_apply_updates(self):
        gm = make_gm(["x"])
        w = gm.world
        out = gm.apply_updates({"move_to": "지하 창고", "add_npc": [{"name": "쥐왕", "traits": "건방짐"}],
                                "add_item": [{"name": "열쇠", "qty": 2}], "remove_item": [{"name": "빵", "qty": 1}, {"name": "없는것"}],
                                "quest_add": [{"title": "쥐 퇴치"}], "quest_done": ["첫 유물"], "add_fact": ["창고에 쥐가 많다"],
                                "hp_change": -100})
        self.assertEqual(w.location, "지하 창고")
        self.assertIn("달빛 마을 여관", next(l for l in w.locations() if l["name"] == "지하 창고")["links"])
        self.assertEqual(w.get_npc("쥐왕")["location"], "지하 창고")
        self.assertEqual(w.hp, 0)
        self.assertEqual(w.quests("done")[0]["title"], "잿빛 회랑의 첫 유물")
        self.assertEqual({i["name"]: i["qty"] for i in w.items()}["빵"], 1)
        self.assertTrue(out)


class TurnTests(unittest.TestCase):
    def test_turn_applies_updates_and_history(self):
        gm = make_gm(["광장으로 나왔다.\n" + block({"state_updates": {"move_to": "달빛 마을 광장"}})])
        res = gm.turn("밖으로 나간다")
        self.assertEqual(res.narration, "광장으로 나왔다.")
        self.assertEqual(gm.world.location, "달빛 마을 광장")
        self.assertEqual(gm.world.turn, 1)
        self.assertEqual(len(gm.world.history()), 2)

    def test_parse_failure_skips_updates(self):
        gm = make_gm(['서술만\n```json\n{bad\n```'])
        res = gm.turn("행동")
        self.assertEqual(res.narration, "서술만")
        self.assertEqual(gm.world.location, "달빛 마을 여관")
        self.assertTrue(res.errors)

    def test_two_step_roll(self):
        step1 = "마리를 설득해 본다.\n" + block({"roll": {"skill": "설득", "dc": 12}})
        step2 = "마리가 웃는다.\n" + block({"state_updates": {"add_fact": ["마리를 설득했다"]}})
        gm = make_gm([step1, step2], seed=7)
        res = gm.turn("마리를 설득한다")
        self.assertEqual(len(gm.llm.calls), 2)
        self.assertEqual(len(res.rolls), 1)
        last = gm.llm.calls[1][-1]["content"]
        self.assertIn("[판정]", last)
        self.assertIn(res.rolls[0]["grade"], last)
        self.assertIn("마리를 설득해 본다.", res.narration)
        self.assertIn("마리가 웃는다.", res.narration)
        self.assertIn("마리를 설득했다", [f["text"] for f in gm.world.facts()])
        self.assertNotIn("```", res.narration)

    def test_roll_uses_skill_modifier(self):
        gm = make_gm([block({"roll": {"skill": "전투", "dc": 5}}), "끝"])
        res = gm.turn("싸운다")
        self.assertEqual(res.rolls[0]["modifier"], 2)

    def test_second_roll_request_ignored(self):
        gm = make_gm([block({"roll": {"skill": "전투", "dc": 5}}), "끝\n" + block({"roll": {"skill": "x", "dc": 5}})])
        res = gm.turn("싸운다")
        self.assertEqual(len(res.rolls), 1)
        self.assertEqual(len(gm.llm.calls), 2)

    def test_recent_history_window(self):
        gm = make_gm(["응답"], recent_turns=2, summary_batch=100)
        for i in range(5):
            gm.turn("행동%d" % i)
        msgs = gm.llm.calls[-1]
        users = [m["content"] for m in msgs if m["role"] == "user"]
        self.assertEqual(users, ["행동2", "행동3", "행동4"])
        self.assertNotIn("행동1", " ".join(m["content"] for m in msgs))

    def test_undo(self):
        gm = make_gm(["이동\n" + block({"state_updates": {"move_to": "광장", "add_item": "열쇠", "hp_change": -5, "add_fact": ["f"]}})])
        before_items = gm.world.items()
        gm.turn("가자")
        self.assertEqual(gm.world.turn, 1)
        self.assertIn("되감았습니다", gm.command("/되감기"))
        w = gm.world
        self.assertEqual((w.turn, w.location, w.hp, w.items(), w.facts() == [] or len(w.facts())),
                         (0, "달빛 마을 여관", 24, before_items, 1))
        self.assertFalse(w.has_location("광장"))
        self.assertEqual(w.history(), [])
        self.assertIn("없습니다", gm.command("/되감기"))

    def test_undo_multiple(self):
        gm = make_gm(["a\n" + block({"state_updates": {"hp_change": -1}})])
        gm.turn("1"); gm.turn("2"); gm.turn("3")
        self.assertEqual(gm.world.hp, 21)
        gm.command("/되감기"); gm.command("/되감기")
        self.assertEqual((gm.world.hp, gm.world.turn), (23, 1))

    def test_rolling_summary(self):
        def reply(messages):
            if "요약" in messages[0]["content"][:40]:
                return "요약본%d" % len(gm.world.facts())
            return "서술"
        gm = make_gm(reply, recent_turns=2, summary_batch=2)
        for i in range(3):
            gm.turn("t%d" % i)
        self.assertIsNone(gm.world.latest_summary())
        gm.turn("t3")  # 4턴 >= 2+2
        s = gm.world.latest_summary()
        self.assertIsNotNone(s)
        self.assertEqual(s["upto_turn"], 2)
        self.assertIn("요약본", ctx.build_context(gm.world, ""))
        gm.turn("t4")
        self.assertEqual(gm.world.latest_summary()["upto_turn"], 2)
        # 요약 입력에 이전 요약이 포함되는지
        gm.turn("t5")
        self.assertEqual(gm.world.latest_summary()["upto_turn"], 4)

    def test_summary_command_and_save(self):
        gm = make_gm(lambda m: "요약결과" if "요약" in m[0]["content"][:40] else "서술")
        gm.turn("a")
        self.assertEqual(gm.command("/요약"), "요약결과")
        self.assertIn("저장", gm.command("/저장"))

    def test_commands(self):
        gm = make_gm(["x"])
        self.assertIn("HP 24/24", gm.command("/상태"))
        self.assertIn("낡은 단검", gm.command("/인벤"))
        self.assertIn("잿빛 회랑의 첫 유물", gm.command("/퀘스트"))
        m = gm.command("/지도")
        self.assertIn("[*] 달빛 마을 여관", m)
        self.assertIn("달빛 마을 광장", m)
        self.assertIn("분위기", gm.command("/세계관"))
        self.assertIn("알 수 없는", gm.command("/없는명령"))
        self.assertIsNone(gm.command("그냥 말"))

    def test_persistence_across_reopen(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "w.db")
            w = World(p)
            apply_preset(w, "조선 괴담", "w")
            w.add_fact("기억할 것")
            w.commit(); w.close()
            w2 = World(p)
            try:
                self.assertEqual(w2.location, "버드나무 주막")
                self.assertEqual(w2.facts()[-1]["text"], "기억할 것")
            finally:
                w2.close()

    def test_wizard(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            answers = iter(["2", "cyber", "카이"])
            w, path = run_wizard(d, lambda _: next(answers), lambda *_: None)
            self.assertEqual(w.get("player_name"), "카이")
            self.assertIn("청계천", w.location)
            with self.assertRaises(FileExistsError):
                it = iter(["1", "cyber"])
                run_wizard(d, lambda _: next(it), lambda *_: None)
            w.close()


class BackendTests(unittest.TestCase):
    def test_openai_payload(self):
        seen = {}

        def tr(url, headers, payload):
            seen.update(url=url, payload=payload)
            return {"choices": [{"message": {"content": "안녕"}}]}
        llm = OpenAICompatLLM(transport=tr)
        self.assertEqual(llm.chat([{"role": "user", "content": "hi"}]), "안녕")
        self.assertEqual(seen["url"], "http://127.0.0.1:8080/v1/chat/completions")

    def test_anthropic_payload(self):
        seen = {}

        def tr(url, headers, payload):
            seen.update(url=url, headers=headers, payload=payload)
            return {"content": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]}
        llm = AnthropicLLM(api_key="k", transport=tr)
        out = llm.chat([{"role": "system", "content": "S"}, {"role": "user", "content": "u1"},
                        {"role": "user", "content": "u2"}, {"role": "assistant", "content": "a"}])
        self.assertEqual(out, "AB")
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(seen["headers"]["x-api-key"], "k")
        self.assertEqual(seen["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(seen["payload"]["model"], "claude-sonnet-5-5")
        self.assertEqual(seen["payload"]["system"], "S")
        self.assertEqual([m["role"] for m in seen["payload"]["messages"]], ["user", "assistant"])


class WebTests(unittest.TestCase):
    def setUp(self):
        self.gm = make_gm(["서술\n" + block({"state_updates": {"add_item": "열쇠"}})])
        self.srv = make_server(self.gm, "127.0.0.1", 0)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def post(self, path, data):
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        return self.opener.open(req)

    def test_index_and_state(self):
        r = self.opener.open(self.base + "/")
        self.assertIn("<title>GM TRPG</title>", r.read().decode())
        s = json.loads(self.opener.open(self.base + "/api/state").read())
        self.assertEqual(s["location"], "달빛 마을 여관")

    def test_chat_turn_and_command(self):
        r = json.loads(self.post("/api/chat", json.dumps({"text": "둘러본다"}).encode()).read())
        self.assertEqual(r["reply"], "서술")
        self.assertIn("열쇠 x1", r["state"]["inventory"])
        r = json.loads(self.post("/api/chat", json.dumps({"text": "/인벤"}).encode()).read())
        self.assertIn("열쇠", r["reply"])
        self.assertFalse(r["is_turn"])

    def test_errors(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.post("/api/chat", b"not json")
        self.assertEqual(c.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.opener.open(self.base + "/nope")
        self.assertEqual(c.exception.code, 404)


class DefaultPortTests(unittest.TestCase):
    def test_serve_default_port_is_8766(self):
        args = build_parser().parse_args(["serve", "--world", "w"])
        self.assertEqual(args.port, 8766)
        self.assertEqual(inspect.signature(make_server).parameters["port"].default, 8766)


if __name__ == "__main__":
    unittest.main()
