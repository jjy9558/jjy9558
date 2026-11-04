"""시나리오 2: paper-butler add(합성 고지서 + 카드 결제 문자) → morning-brief 납부 카드,
paper-butler export-csv --what ledger → cool-off import-ledger.

덤: cool-off 구독(sub add) 갱신 임박도 morning-brief 카드로 나오는지.
"""
import datetime as dt
import unittest

from homelabe2e.case import E2ECase
from homelabe2e import services as S
from homelabe2e.harness import have, http_json, import_from


def gas_bill(due: dt.date) -> str:
    # 실존하지 않는 합성 고지서 (번호는 형식만 흉내)
    return ("[서울도시가스] 도시가스 요금 고지서\n고객번호 : 70%08d\n사용량 25m3\n"
            "이번 달 청구금액 : 38,900원\n납부기한 : %s\n납기후 금액 39,800원\n"
            % (int(due.strftime("%m%d")) * 7, due.strftime("%Y.%m.%d")))


def card_sms(when: dt.datetime) -> str:
    return "[Web발신]\n삼성카드 승인 홍*동\n12,340원 일시불\n%s\n스타벅스 강남점\n" % when.strftime("%m/%d %H:%M")


class PaperButlerToBriefAndCoolOff(E2ECase):
    needs = ("paper-butler", "morning-brief", "cool-off")

    def setUp(self):
        super().setUp()
        st = self.stack
        self.llm = st.fake_llm()
        self.pb_home = st.path("paperbutler/.keep").parent
        st.extra_env.update({"PAPERBUTLER_HOME": str(self.pb_home), "PAPERBUTLER_LLM_URL": self.llm.url,
                             "COOLOFF_HOME": str(st.path("cooloff/.keep").parent)})
        self.due = self.today + dt.timedelta(days=2)
        common = ["--today", self.today.isoformat()]
        p = self.run_cli("paper-butler", ["add", "-", "--type", "bill"] + common, input=gas_bill(self.due))
        self.assertIn("가스요금", p.stdout)
        when = dt.datetime.combine(self.today, dt.time(9, 15))
        p = self.run_cli("paper-butler", ["add", "-", "--type", "card"] + common, input=card_sms(when))
        self.assertIn("12,340원", p.stdout)

    def test_due_bill_becomes_brief_card(self):
        st = self.stack
        # cool-off 구독: 내일 결제 → 갱신 임박 카드
        tomorrow = self.today + dt.timedelta(days=1)
        self.run_cli("cool-off", ["sub", "add", "넷플릭스", "13500", "--cycle", "monthly", "--day", str(tomorrow.day)])
        cfg = self.mbrief_config(paperbutler_db=self.pb_home / "paperbutler.db",
                                 cooloff_data=st.path("cooloff/data.json"))
        self.run_cli("morning-brief", ["run", "--config", cfg, "--date", self.today.isoformat()])
        out = cfg.parent / "out"
        brief, cards = self.load_json(out / "brief-latest.json"), self.load_json(out / "cards-latest.json")
        contract = import_from("morning-brief", "mbrief.contract")
        self.assertEqual(contract.validate_brief(brief), [])
        self.assertEqual(contract.validate_cards(cards), [])
        self.assertEqual(brief["sources"]["paper_butler"]["status"], "ok", brief["sources"])
        bills = [c for c in cards if c["kind"] == "bill"]
        self.assertEqual(len(bills), 1, cards)
        self.assertEqual(bills[0]["due"], self.due.isoformat())
        self.assertIn("가스요금", bills[0]["title"])
        self.assertIn("38,900원", bills[0]["title"])
        # 카드의 다음 행동 문구가 실제 paper-butler 명령으로 동작해야 한다
        action = bills[0]["actions"][0]
        self.assertIsInstance(action, dict, action)
        self.assertTrue(action["id"].startswith("paperbutler.done:"), action)
        self.assertTrue(action.get("text", "").startswith("완료 처리: python -m paperbutler done "), action)
        doc_id = action["id"].split(":", 1)[1]
        self.run_cli("paper-butler", ["done", doc_id, "--today", self.today.isoformat()])
        self.run_cli("morning-brief", ["run", "--config", cfg, "--date", self.today.isoformat()])
        cards2 = self.load_json(out / "cards-latest.json")
        self.assertFalse([c for c in cards2 if c["kind"] == "bill"], cards2)   # 처리하면 사라짐
        # 납부 낭독문에 들어감 (speechify: 3만 8천 9백원)
        self.assertIn("가스요금", brief["speak_text"])
        # cool-off 구독 갱신
        self.assertEqual(brief["sources"]["cool_off"]["status"], "ok", brief["sources"])
        subs = [c for c in cards if c["kind"] == "subscription"]
        self.assertTrue(subs and "넷플릭스" in subs[0]["title"], cards)
        self.assertEqual(subs[0]["due"], tomorrow.isoformat())

    def test_ledger_csv_into_cool_off(self):
        st = self.stack
        csv_path = st.path("exchange/ledger.csv")
        self.run_cli("paper-butler", ["export-csv", "--what", "ledger", "-o", csv_path, "--today",
                                      self.today.isoformat()])
        self.assertTrue(csv_path.read_bytes().startswith(b"\xef\xbb\xbf"))      # 문서화된 UTF-8 BOM
        p = self.run_cli("cool-off", ["import-ledger", csv_path])
        data = self.load_json(st.path("cooloff/data.json"))
        got = [x for x in data["purchases"] if x.get("source") == "ledger"]
        self.assertEqual(len(got), 1, (p.stdout, data["purchases"]))
        self.assertEqual(got[0]["price"], 12340)
        self.assertEqual(got[0]["date"], self.today.isoformat())
        self.assertIn("스타벅스", got[0]["name"])
        # 같은 파일을 다시 가져와도 그대로(중복 방지 키)
        self.run_cli("cool-off", ["import-ledger", csv_path])
        again = self.load_json(st.path("cooloff/data.json"))
        self.assertEqual(len([x for x in again["purchases"] if x.get("source") == "ledger"]), 1)
        # 리포트에 출처(가계부)가 보인다
        rep = self.run_cli("cool-off", ["report", "--week"])
        self.assertIn("가계부", rep.stdout)

    def test_bill_card_action_can_be_whitelisted_on_desk(self):
        """(F-3 수정 확인) morning-brief 납부 카드의 action 은 {id: "paperbutler.done:<doc_id>"} 객체이고,
        phone-home 책상 화이트리스트의 접두사 항목("paperbutler.done:")으로 실행 버튼을 만들 수 있다."""
        if have("phone-home"):
            self.skipTest("phone-home 없음")
        cfg = self.mbrief_config(paperbutler_db=self.pb_home / "paperbutler.db")
        self.run_cli("morning-brief", ["run", "--config", cfg, "--date", self.today.isoformat()])
        cards_path = cfg.parent / "out" / "cards-latest.json"
        bill = [c for c in self.load_json(cards_path) if c["kind"] == "bill"][0]
        aid = bill["actions"][0]["id"]
        prefix, _, suffix = aid.partition(":")
        self.assertEqual(prefix, "paperbutler.done")
        self.assertTrue(suffix.isdigit(), aid)
        ph_cfg = import_from("phone-home", "phonehome.config")
        norm = ph_cfg.normalize({"server": {"token": "t" * 24},
                                 "desk": {"actions": {"paperbutler.done:": {
                                     "label": "완료", "argv": ["python", "-m", "paperbutler", "done", "{arg}"]}}}})
        self.assertIn("paperbutler.done:", norm["desk"]["actions"])

    def test_online_receipt_refund_deadline_reaches_brief_and_departure(self):
        """paper-butler 영수증(온라인 7일 기준) → refunds 표 → morning-brief refund 카드 +
        s21-sidekick 출발 체크 paperbutler_returns provider."""
        if have("s21-sidekick"):
            self.skipTest("s21-sidekick 없음")
        st = self.stack
        bought = self.today - dt.timedelta(days=6)                     # 마감 = 구매일 + 7 = 내일
        receipt = ("[쿠팡] 주문이 완료되었습니다\n주문번호 %s12345\n주문일시 %s 20:15\n상품명 무선 마우스 x 1\n"
                   "배송비 0원\n결제금액 29,900원\n" % (bought.strftime("%Y%m%d"), bought.isoformat()))
        p = self.run_cli("paper-butler", ["add", "-", "--today", self.today.isoformat()], input=receipt)
        self.assertIn("환불/반품", p.stdout)
        db = self.pb_home / "paperbutler.db"
        cfg = self.mbrief_config(paperbutler_db=db)
        self.run_cli("morning-brief", ["run", "--config", cfg, "--date", self.today.isoformat()])
        cards = self.load_json(cfg.parent / "out" / "cards-latest.json")
        refunds = [c for c in cards if c["kind"] == "refund"]
        self.assertEqual(len(refunds), 1, cards)
        self.assertEqual(refunds[0]["due"], (self.today + dt.timedelta(days=1)).isoformat())
        self.assertIn("쿠팡", refunds[0]["title"])
        sk = S.sidekick(st, {"departure": {"providers": [
            {"name": "paperbutler_returns", "enabled": True, "db_path": str(db), "days": 2}]}})
        code, out = http_json("POST", sk.url + "/api/departure", {"source": "button"},
                              {"X-Sidekick-Token": sk.token})
        self.assertEqual(code, 200, out)
        self.assertIn("반품 마감: 쿠팡 내일", out["say"])
        # 카드의 행동 문구(done <doc_id>)를 실행하면 두 곳 모두에서 사라진다
        doc_id = refunds[0]["actions"][0]["id"].split(":", 1)[1]
        self.run_cli("paper-butler", ["done", doc_id, "--today", self.today.isoformat()])
        self.run_cli("morning-brief", ["run", "--config", cfg, "--date", self.today.isoformat()])
        self.assertFalse([c for c in self.load_json(cfg.parent / "out" / "cards-latest.json")
                          if c["kind"] == "refund"])
        code, out = http_json("POST", sk.url + "/api/departure", {"source": "button"},
                              {"X-Sidekick-Token": sk.token})
        self.assertNotIn("반품 마감", out["say"])


if __name__ == "__main__":
    unittest.main()
