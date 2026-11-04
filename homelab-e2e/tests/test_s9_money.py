"""추가 시나리오: 돈 기록이 세 프로젝트(steam-buddy, paper-butler, cool-off)를 오갈 때의 계약.

- steam-buddy 구매 + paper-butler 카드 문자(같은 날 같은 금액의 Steam 결제) → cool-off 에 한 번만 잡힌다.
- steam-buddy 에서 '환불했음'으로 표시한 게임은 cool-off 지출/후회에 들어가면 안 된다 (FINDINGS F-2).
"""
import datetime as dt
import unittest

from homelabe2e.case import E2ECase
from homelabe2e.seed import STEAM_OFFLINE, Seeder


class MoneyAcrossProjects(E2ECase):
    needs = ("steam-buddy", "cool-off", "paper-butler")

    def setUp(self):
        super().setUp()
        self.seed = Seeder(self.stack)

    def steam(self, *args):
        return self.run_cli("steam-buddy", list(args), code=STEAM_OFFLINE)

    def test_steam_and_card_sms_not_double_counted(self):
        self.steam("buy", "9000011", "--name", "할인 액션", "--price", "27000", "--date", self.today.isoformat())
        sms = "[Web발신]\n신한카드 승인 홍*동\n27,000원 일시불\n%s 21:05\nSTEAM\n" % self.today.strftime("%m/%d")
        self.run_cli("paper-butler", ["add", "-", "--type", "card", "--no-llm", "--today", self.today.isoformat()],
                     input=sms)
        csv_path = self.stack.path("exchange/ledger.csv")
        self.run_cli("paper-butler", ["export-csv", "--what", "ledger", "-o", csv_path,
                                      "--today", self.today.isoformat()])
        self.run_cli("cool-off", ["import-steam"])
        p = self.run_cli("cool-off", ["import-ledger", csv_path])
        data = self.load_json(self.seed.cool_home / "data.json")
        same = [x for x in data["purchases"] if x["price"] == 27000]
        self.assertEqual(len(same), 1, (p.stdout, data["purchases"]))
        self.assertEqual(same[0]["source"], "steam")

    def test_refunded_steam_game_not_imported_as_spending(self):
        """(F-2 수정 확인) steam-buddy 에서 환불한 게임은 cool-off import-steam 이 구매로 가져오지 않는다."""
        when = (self.today - dt.timedelta(days=3)).isoformat()
        self.steam("buy", "9000021", "--name", "환불한 게임", "--price", "30000", "--date", when)
        self.steam("rate", "9000021", "1")
        p = self.steam("refunded", "9000021")
        self.assertIn("통계에서 빠져요", p.stdout)                 # steam-buddy 자기 약속
        self.run_cli("cool-off", ["import-steam"])
        path = self.seed.cool_home / "data.json"
        purchases = self.load_json(path)["purchases"] if path.exists() else []   # 가져올 게 없으면 파일도 안 생김
        self.assertEqual([x for x in purchases if x["name"] == "환불한 게임"], [], purchases)


if __name__ == "__main__":
    unittest.main()
