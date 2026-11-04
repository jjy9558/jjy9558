"""시나리오 7: life-nagger / decision-pet / cool-off(← steam-buddy) 를 각자 CLI 로 심고
story-factory run → shorts-linter 하위 프로세스 결과(titles/promise/disclose/similar)가 보고서와 산출물에 있는지.

LLM 은 가짜 OpenAI 호환 서버. 덤: --submit-jarvis 가 진짜 jarvis-hub 에 story.batch 작업을 올리는지.
"""
import json
import re
import unittest

from homelabe2e import services as S
from homelabe2e.case import E2ECase
from homelabe2e.harness import FakeLLM, have, http_json
from homelabe2e.seed import Seeder


def story_llm(body: dict) -> str:
    text = FakeLLM.text_of(body)
    if "쇼츠 사연 대본" not in text:
        return "{}"
    m = re.search(r"소재 종류: (\S+)", text)
    kind = m.group(1) if m else "기록"
    title = "작은 습관 이야기 %s" % kind.replace("_", " ")
    lines = ["오늘은 %s 기록을 들고 왔습니다." % kind, "제목 그대로 %s 입니다." % title,
             "처음에는 별일 아니라고 생각했습니다.", "그런데 기록을 모아 보니 달랐습니다.",
             "작은 습관이 쌓이면 이야기가 됩니다.", "여러분의 작은 습관 이야기도 궁금합니다."]
    return json.dumps({"title": title, "title_candidates": [title, "기록으로 본 작은 습관"],
                       "thumbnail_text": "작은 습관", "claims": ["작은 습관"], "script_lines": lines},
                      ensure_ascii=False)


def synthetic_kakao_pc(year: int) -> str:
    """카카오톡 PC 내보내기 형식을 흉내 낸 가짜 대화 (가상의 이름, 6일 연속, 120개 메시지)."""
    names = ["박서준", "이하늘", "최도윤"]
    out = ["카카오톡 대화", "저장한 날짜 : %d-03-01 10:00:00", ""]
    out[1] = out[1] % year
    days = ["월", "화", "수", "목", "금", "토", "일"]
    import datetime as _dt
    start = _dt.date(year, 2, 1)
    n = 0
    for d in range(6):
        day = start + _dt.timedelta(days=d)
        out.append("--------------- %d년 %d월 %d일 %s요일 ---------------"
                   % (day.year, day.month, day.day, days[day.weekday()]))
        for i in range(20):
            who = names[(i + d) % 3]
            hour = 9 + (i % 3)
            out.append("[%s] [오후 %d:%02d] %s" % (who, hour - 8 if hour > 12 else hour, i, "오늘 라면 먹을 사람" if i == 0
                                                 else "메시지 %d" % n))
            n += 1
        out.append("")
    return "\n".join(out) + "\n"


class StoryFactoryNightShift(E2ECase):
    needs = ("story-factory", "shorts-linter", "life-nagger", "decision-pet", "cool-off", "steam-buddy",
             "voice-journal")

    def setUp(self):
        super().setUp()
        self.seed = Seeder(self.stack)
        self.seed.nagger_counts(sigh=6, pushup=20)
        self.seed.pet_graveyard()
        self.seed.steam_games()
        self.seed.cooloff_from_steam()
        self.llm = self.stack.fake_llm(story_llm)
        self.out = self.stack.path("story/out/.keep").parent

    def story_run(self, pet_arg, extra=()):
        args = ["run", "--out", self.out, "--date", self.today.isoformat(), "--seed", "17",
                "--life-nagger", self.seed.nagger_state, "--cool-off", self.seed.cool_home,
                "--decision-pet", pet_arg, "--llm-url", self.llm.url, "--llm-model", "fake",
                "--name", "김민수"] + list(extra)
        p = self.run_cli("story-factory", args, timeout=120)
        day = self.out / self.today.isoformat()
        return p, day, self.load_json(day / "run.json")

    def test_run_with_linter_results(self):
        # decision-pet 은 실제 파일(state.json)을 직접 넘긴다 (폴더 지정은 아래 xfail 테스트)
        p, day, run = self.story_run(self.seed.pet_home / "state.json")
        self.assertTrue(run["linter"], run)
        self.assertTrue(run["llm"], run)
        self.assertEqual(run["warnings"], [], run)
        self.assertEqual(len(run["candidates"]), 2, run)
        metas = sorted(day.glob("*.meta.json"))
        sources = {self.load_json(m)["story_factory"]["source"] for m in metas}
        self.assertTrue({"nagger", "cooloff", "pet"} <= sources, sources)
        for m in metas:
            meta = self.load_json(m)
            self.assertTrue((day / m.name.replace(".meta.json", ".txt")).is_file())
            self.assertIsInstance(meta["ai_used"], dict)
            self.assertIn("script", meta["ai_used"])
        self.assertTrue(any(self.load_json(m)["ai_used"]["script"] for m in metas))   # 가짜 LLM 이 쓴 대본
        self.assertTrue(self.llm.requests)
        report = (day / "report.md").read_text(encoding="utf-8")
        self.assertIn("린트: shorts-linter 사용", report)
        for label in ("약속 일치(promise)", "제목 점수(titles)", "유사도(similar)", "AI 공개 문구 초안(`disclose`)"):
            self.assertIn(label, report)
        self.assertNotIn("생략", report.split("## 후보 1")[1].split("대본:")[0], report)
        self.assertRegex(report, r"제목 점수\(titles\): \d+점")
        self.assertNotIn("김민수", "".join(t.read_text(encoding="utf-8") for t in day.glob("*.txt")))
        # README 의 주장: 산출물 폴더에 shorts-linter 를 그대로 쓸 수 있다
        sl = self.run_cli("shorts-linter", ["similar", day], check=False)
        self.assertIn(sl.returncode, (0, 1), sl.stderr)
        self.assertTrue(sl.stdout.strip(), sl.stderr)
        tl = self.run_cli("shorts-linter", ["titles", metas[0]], check=False)
        self.assertIn(tl.returncode, (0, 1), tl.stderr)
        self.assertRegex(tl.stdout, r"\d+")
        # 산출물만 다시 린트
        self.run_cli("story-factory", ["lint", day])

    def test_decision_pet_folder_as_documented(self):
        """(F-1 수정 확인) README 대로 --decision-pet ~/.decision-pet(폴더)를 주면 data.json 을 찾지만
        decision-pet 은 state.json 에 저장한다 → 묘지 카드가 하나도 안 나온다."""
        p, day, run = self.story_run(self.seed.pet_home)
        self.assertNotIn("decision-pet: 파일이 없어 건너뜀", run["warnings"])
        sources = {self.load_json(m)["story_factory"]["source"] for m in day.glob("*.meta.json")}
        self.assertIn("pet", sources)

    def test_kakao_wrapped_anonymized_stats_only(self):
        """kakao-wrapped --anonymize --json 통계 → story-factory --kakao. 익명화 안 된 통계는 거부,
        멤버 실명/원문은 산출물에 없어야 한다."""
        if have("kakao-wrapped"):
            self.skipTest("kakao-wrapped 없음")
        st = self.stack
        export = st.path("kakao/chat.txt")
        export.write_text(synthetic_kakao_pc(self.today.year), encoding="utf-8")
        anon = st.path("kakao/anon.json")
        self.run_cli("kakao-wrapped", [export, "--year", self.today.year, "--anonymize", "--json",
                                       "--out", st.path("kakao/anon.html"), "--json-out", anon])
        raw = st.path("kakao/raw.json")
        self.run_cli("kakao-wrapped", [export, "--year", self.today.year, "--json",
                                       "--out", st.path("kakao/raw.html"), "--json-out", raw])
        p, day, run = self.story_run(self.seed.pet_home / "state.json", ["--kakao", anon])
        kinds = {self.load_json(m)["story_factory"]["kind"] for m in day.glob("*.meta.json")
                 if self.load_json(m)["story_factory"]["source"] == "kakao"}
        p2 = self.run_cli("story-factory", ["seeds", "--kakao", anon, "--json", "--date", self.today.isoformat()])
        seeds = json.loads(p2.stdout)
        self.assertTrue([c for c in seeds["cards"] if c["source"] == "kakao"], seeds)
        self.assertTrue(kinds or [c for c in seeds["cards"] if c["source"] == "kakao"])
        blob = "".join(f.read_text(encoding="utf-8") for f in day.iterdir() if f.is_file())
        for name in ("박서준", "이하늘", "오늘 라면 먹을 사람"):
            self.assertNotIn(name, blob)
        p3 = self.run_cli("story-factory", ["seeds", "--kakao", raw, "--json", "--date", self.today.isoformat()])
        seeds_raw = json.loads(p3.stdout)
        self.assertFalse([c for c in seeds_raw["cards"] if c["source"] == "kakao"], seeds_raw)
        self.assertTrue(any("익명화" in w for w in seeds_raw["warnings"]), seeds_raw)

    def test_submit_jarvis_story_batch(self):
        if have("jarvis-hub"):
            self.skipTest("jarvis-hub 없음")
        hub = S.jarvis_hub(self.stack, extra_devices={"pc": {"token": "pc-" + "z" * 24 + self.today.strftime("%d"),
                                                            "submit": ["story.batch"], "trusted": True}})
        p, day, run = self.story_run(self.seed.pet_home / "state.json",
                                     ["--submit-jarvis", "--jarvis-url", hub.url, "--jarvis-device", "pc",
                                      "--jarvis-token", "pc-" + "z" * 24 + self.today.strftime("%d")])
        code, data = http_json("GET", hub.url + "/api/jobs?limit=20", headers=S.jarvis_headers(hub))
        batches = [j for j in data["jobs"] if j["type"] == "story.batch"]
        self.assertEqual(len(batches), 1, (p.stdout, data))
        payload = json.dumps(batches[0]["payload"], ensure_ascii=False)
        for cid in run["candidates"]:
            self.assertIn(cid, payload)
        self.assertNotIn("오늘은", payload)                 # 대본 본문은 보내지 않음


if __name__ == "__main__":
    unittest.main()
