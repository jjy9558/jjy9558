"""시나리오 8: decision-pet 결정 의회가 cool-off / steam-buddy / paper-butler (+ voice-journal) 의
실제 저장 파일(각자 CLI 로 만든 것)을 읽어 의원별 근거와 표결을 낸다.
"""
import unittest

from homelabe2e.case import E2ECase
from homelabe2e.seed import Seeder


class DecisionCouncil(E2ECase):
    needs = ("decision-pet", "cool-off", "steam-buddy", "paper-butler", "voice-journal")

    def setUp(self):
        super().setUp()
        self.seed = Seeder(self.stack)
        self.steam = self.seed.steam_games()
        self.seed.cooloff_from_steam()
        self.seed.cooloff_want("엘든링 DLC", 45000)
        self.pb = self.seed.paper_payments()
        self.run_cli("voice-journal", ["--db", self.seed.vj_db, "add", "이번 달은 충동구매 안 산다. 지출 줄이기!"])

    def council(self, question, *extra):
        p = self.run_cli("decision-pet", ["decide", "--mode", "council", question] + list(extra))
        return p.stdout

    def test_council_with_explicit_paths(self):
        out = self.council("엘든링 DLC 살까?",
                           "--cool-off", self.seed.cool_home / "data.json", "--steam-buddy", self.steam,
                           "--paper-butler", self.pb, "--voice-journal", self.seed.vj_db,
                           "--life-nagger", self.stack.path("no-nagger"))
        self.assertIn("[쿨링오프 의원] '엘든링 DLC'는 지금 쿨링오프 중이에요", out)
        self.assertIn("[스팀 의원] 아직 안 켜본 게임이 3개", out)
        self.assertIn("(반대)", out)
        self.assertIn("[가계부 의원] 이번 달 지출 50,340원", out)         # 38,000 + 12,340 승인
        self.assertIn("[일기 의원]", out)
        self.assertIn("충동구매 안 산다", out)
        self.assertNotIn("[엄마봇 의원]", out)                            # 데이터 없으면 결석
        self.assertIn("땅땅땅! 안건은 부결! (찬성 1 : 반대 3, 기권 0)", out)
        self.assertIn("python -m cool_off want", out)                    # 제안만, 실행 안 함
        self.assertEqual(len(self.load_json(self.seed.cool_home / "data.json")["wishes"]), 1)

    def test_council_default_locations_from_env(self):
        """경로 옵션 없이 각 프로젝트의 문서화된 환경변수(COOLOFF_HOME, STEAM_BUDDY_HOME, PAPERBUTLER_HOME,
        VJOURNAL_DB)만으로 찾아야 한다."""
        self.stack.extra_env["VJOURNAL_DB"] = str(self.seed.vj_db)
        out = self.council("스팀 게임 하나 더 살까?")
        for member in ("쿨링오프 의원", "스팀 의원", "가계부 의원", "일기 의원"):
            self.assertIn("[%s]" % member, out)
        self.assertIn("부결", out)
        # 결정 기록에도 council 모드로 남음 (만족도는 다음 날 followup 에서 매김)
        state = self.load_json(self.seed.pet_home / "state.json")
        self.assertTrue([d for d in state["decisions"] if d.get("mode") == "council"], state["decisions"])

    def test_mombot_member_reads_life_nagger_state(self):
        """life-nagger 엔진은 Windows 플랫폼만 있어 CLI(run)로는 리눅스에서 상태 파일을 못 만든다.
        그래서 life-nagger 자기 테스트처럼 FakePlatform/FakeClock 으로 엔진을 이 프로세스에서 돌려
        status.json / work.json 을 만들고, 의회는 하위 프로세스로 그 폴더를 읽는다."""
        import datetime as dt
        from homelabe2e.harness import have, import_from
        if have("life-nagger"):
            self.skipTest("life-nagger 없음")
        cfgmod = import_from("life-nagger", "nagger.config")
        engine = import_from("life-nagger", "nagger.engine")
        fakes = import_from("life-nagger", "nagger.fakes")
        outputs = import_from("life-nagger", "nagger.outputs")
        state = self.stack.path("nagger-state/.keep").parent
        cfg = cfgmod.load_config(None, {"state_dir": str(state), "games": ["game.exe"],
                                        "outputs": {"console": {"enabled": False}, "toast": {"enabled": False}}})
        clock = fakes.FakeClock(dt.datetime.combine(self.today, dt.time(1, 55)))
        plat = fakes.FakePlatform()
        plat.processes = {"game.exe"}
        plat.foreground = "game.exe"
        n = engine.Nagger(cfg, plat, clock, outputs.Notifier([]))
        n.tick()
        for _ in range(6):            # 02:00 넘어서 엄마봇 1단계 경고
            clock.advance(minutes=1)
            n.tick()
        self.assertEqual(self.load_json(state / "status.json")["mombot"]["state"], "warning")
        out = self.council("새 게임 살까?", "--life-nagger", state, "--cool-off", self.stack.path("none.json"),
                           "--steam-buddy", self.stack.path("none.json"), "--paper-butler", self.stack.path("x.db"),
                           "--voice-journal", self.stack.path("x.db"))
        self.assertIn("[엄마봇 의원] 지금도 새벽 게임으로 엄마봇 경고 1단계예요", out)

    @unittest.skip("FINDINGS F-6: decision-pet LLM 주소는 state.json 의 config.llm.endpoint 에만 있고 "
                   "CLI/환경변수로 바꿀 길이 없어, 상태 파일을 손으로 고치지 않고는 --llm 을 가짜 LLM 으로 검증할 수 없음")
    def test_council_llm_phrasing_with_fake_llm(self):
        pass

    def test_non_game_question_steam_abstains(self):
        out = self.council("점심에 비싼 초밥 먹을까?", "--steam-buddy", self.steam,
                           "--cool-off", self.seed.cool_home / "data.json", "--paper-butler", self.pb)
        self.assertIn("[스팀 의원] 게임 얘기가 아니라서", out)
        self.assertIn("(기권)", out)


if __name__ == "__main__":
    unittest.main()
