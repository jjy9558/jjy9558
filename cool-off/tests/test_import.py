import csv
import json
import tempfile
import unittest
from pathlib import Path

from cool_off import core, importers
from cool_off.core import Ledger
from tests.fakes import at
from tests.test_cli import Base

HEADER = ["일시", "가맹점", "구분", "금액", "카테고리", "할부", "상태", "집계반영", "카드", "문서ID"]


def write_ledger(path, rows, bom=True):
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerow(HEADER)
        for date, merchant, amount, *rest in rows:
            kind = rest[0] if rest else "승인"
            counted = rest[1] if len(rest) > 1 else "Y"
            w.writerow([date, merchant, kind, amount, "", "일시불", "정상", counted, "", ""])


def write_steam(path, games):
    path.write_text(json.dumps({"games": games, "config": {}}, ensure_ascii=False), encoding="utf-8")


class ImportBase(Base):
    def setUp(self):
        super().setUp()
        self.dir = Path(self.tmp.name)

    def steam_file(self, games):
        p = self.dir / "steam.json"
        write_steam(p, games)
        return p

    def ledger_file(self, rows):
        p = self.dir / "ledger.csv"
        write_ledger(p, rows)
        return p


class NormalizeTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(importers.normalize_merchant(" Netflix.com "), importers.normalize_merchant("NETFLIX COM"))
        self.assertEqual(importers.dedup_key("2026-01-01", 5, "A B"), "2026-01-01|5|ab")


class SteamImportTests(ImportBase):
    GAMES = {
        "1": {"appid": 1, "name": "Hades", "price_paid": 25000, "purchased_at": "2026-09-01T10:00:00",
              "rating": 5, "minutes": 600},
        "2": {"appid": 2, "name": "Free Game", "price_paid": 0, "purchased_at": "2026-09-02T10:00:00"},
        "3": {"appid": 3, "name": "Unrated", "price_paid": 10000, "purchased_at": "2026-09-03T10:00:00",
              "rating": None},
    }

    def test_import_maps_rating_and_source_and_is_read_only(self):
        path = self.steam_file(self.GAMES)
        before = path.read_bytes()
        code, out = self.run_cmd(at(2026, 11, 2), "import-steam", str(path))
        self.assertEqual(code, 0)
        self.assertIn("새로 2건", out)
        purchases = self.state()["purchases"]
        self.assertEqual({p["name"] for p in purchases}, {"Hades", "Unrated"})
        hades = next(p for p in purchases if p["name"] == "Hades")
        self.assertEqual((hades["source"], hades["satisfaction"], hades["price"], hades["date"]),
                         ("steam", 5, 25000, "2026-09-01"))
        self.assertEqual(path.read_bytes(), before)

    def test_reimport_is_idempotent(self):
        path = self.steam_file(self.GAMES)
        self.run_cmd(at(2026, 11, 2), "import-steam", str(path))
        first = self.state()
        code, out = self.run_cmd(at(2026, 11, 3), "import-steam", str(path))
        self.assertIn("새로 0건, 이미 있음 2건", out)
        self.assertEqual(self.state(), first)

    def test_refunded_games_skipped_and_removed_later(self):
        refunded = {k: dict(v) for k, v in self.GAMES.items()}
        refunded["1"]["refunded"] = True
        # 처음부터 환불된 게임은 가져오지 않는다
        code, out = self.run_cmd(at(2026, 11, 2), "import-steam", str(self.steam_file(refunded)))
        self.assertEqual({p["name"] for p in self.state()["purchases"]}, {"Unrated"})
        # 먼저 가져온 뒤 환불되면 지워지고, 다시 가져와도 그대로(멱등)
        self.run_cmd(at(2026, 11, 3), "import-steam", str(self.steam_file(self.GAMES)))
        self.assertEqual({p["name"] for p in self.state()["purchases"]}, {"Hades", "Unrated"})
        path = self.steam_file(refunded)
        self.run_cmd(at(2026, 11, 4), "import-steam", str(path))
        self.assertEqual({p["name"] for p in self.state()["purchases"]}, {"Unrated"})
        first = self.state()
        self.run_cmd(at(2026, 11, 5), "import-steam", str(path))
        self.assertEqual(self.state(), first)

    def test_later_rating_updates_existing(self):
        self.run_cmd(at(2026, 11, 2), "import-steam", str(self.steam_file(self.GAMES)))
        games = json.loads(json.dumps(self.GAMES))
        games["3"]["rating"] = 4
        self.run_cmd(at(2026, 11, 3), "import-steam", str(self.steam_file(games)))
        p = next(p for p in self.state()["purchases"] if p["name"] == "Unrated")
        self.assertEqual(p["satisfaction"], 4)
        self.assertEqual(len(self.state()["purchases"]), 2)

    def test_empty_file_and_missing_file(self):
        code, out = self.run_cmd(at(2026, 11, 2), "import-steam", str(self.steam_file({})))
        self.assertEqual(code, 0)
        self.assertIn("빈 파일", out)
        self.assertEqual(self.state()["purchases"], [])
        code, out = self.run_cmd(at(2026, 11, 2), "import-steam", str(self.dir / "nope.json"))
        self.assertEqual(code, 1)
        (self.dir / "bad.json").write_text("", encoding="utf-8")
        code, out = self.run_cmd(at(2026, 11, 2), "import-steam", str(self.dir / "bad.json"))
        self.assertEqual(code, 1)

    def test_imported_steam_not_asked_for_followup(self):
        self.run_cmd(at(2026, 11, 2), "import-steam", str(self.steam_file(self.GAMES)))
        _, out = self.run_cmd(at(2026, 11, 2), "check")
        self.assertIn("확인할 것이 없어요", out)


class LedgerImportTests(ImportBase):
    def test_import_filters_cancels_and_reimport_same(self):
        path = self.ledger_file([
            ("2026-10-01 12:00", "스타벅스", 5500),
            ("2026-10-02", "카페", 4000, "승인", "N"),
            ("2026-10-03", "환불", 3000, "취소", "N"),
        ])
        code, out = self.run_cmd(at(2026, 11, 2), "import-ledger", str(path))
        self.assertEqual(code, 0)
        self.assertIn("새로 1건", out)
        first = self.state()
        _, out = self.run_cmd(at(2026, 11, 3), "import-ledger", str(path))
        self.assertIn("새로 0건, 이미 있음 1건", out)
        self.assertEqual(self.state(), first)
        self.assertEqual(first["purchases"][0]["source"], "ledger")
        self.assertEqual(first["purchases"][0]["date"], "2026-10-01")

    def test_double_count_with_steam_and_manual(self):
        self.run_cmd(at(2026, 11, 2), "import-steam", str(self.steam_file({
            "1": {"appid": 1, "name": "Hades", "price_paid": 25000, "purchased_at": "2026-09-01T10:00:00",
                  "rating": 5}})))
        self.run_cmd(at(2026, 11, 2), "bought", "이어폰", "--price", "100000", "--date", "2026-09-05")
        path = self.ledger_file([
            ("2026-09-01 10:01", "STEAM PURCHASE", 25000),
            ("2026-09-05", "애플스토어", 100000),
            ("2026-09-06", "마트", 30000),
        ])
        _, out = self.run_cmd(at(2026, 11, 2), "import-ledger", str(path))
        self.assertIn("새로 1건", out)
        self.assertIn("이중 집계라 뺀 것 2건", out)
        total = sum(p["price"] for p in self.state()["purchases"])
        self.assertEqual(total, 25000 + 100000 + 30000)
        _, out = self.run_cmd(at(2026, 11, 3), "report")
        self.assertIn("155,000원", out)

    def test_bom_and_plain_utf8_both_read(self):
        p = self.dir / "plain.csv"
        write_ledger(p, [("2026-10-01", "가게", 1000)], bom=False)
        code, out = self.run_cmd(at(2026, 11, 2), "import-ledger", str(p))
        self.assertIn("새로 1건", out)

    def test_formula_guard_prefix_stripped(self):
        path = self.ledger_file([("2026-10-01", "'=SUM 가게", 1000)])
        self.run_cmd(at(2026, 11, 2), "import-ledger", str(path))
        self.assertEqual(self.state()["purchases"][0]["name"], "=SUM 가게")

    def test_empty_and_header_only_and_wrong_file(self):
        empty = self.dir / "empty.csv"
        empty.write_bytes(b"")
        code, out = self.run_cmd(at(2026, 11, 2), "import-ledger", str(empty))
        self.assertEqual(code, 0)
        self.assertIn("빈 파일", out)
        header_only = self.ledger_file([])
        code, out = self.run_cmd(at(2026, 11, 2), "import-ledger", str(header_only))
        self.assertEqual(code, 0)
        self.assertIn("빈 파일", out)
        wrong = self.dir / "wrong.csv"
        wrong.write_text("a,b\n1,2\n", encoding="utf-8")
        code, out = self.run_cmd(at(2026, 11, 2), "import-ledger", str(wrong))
        self.assertEqual(code, 1)
        self.assertEqual(self.state()["purchases"], [])

    def test_subscription_candidate_suggested_and_stable(self):
        rows = [(f"2026-0{m}-15", "넷플릭스", 13500) for m in (7, 8, 9)]
        rows += [("2026-07-03", "편의점", 3000), ("2026-09-03", "편의점", 3000),
                 ("2026-08-20", "헬스장", 50000), ("2026-10-20", "헬스장", 50000)]
        path = self.ledger_file(rows)
        _, out = self.run_cmd(at(2026, 11, 2), "import-ledger", str(path))
        self.assertIn("구독 후보", out)
        self.assertIn('sub add "넷플릭스" 13500 --day 15', out)
        self.assertNotIn("편의점 3,000원 —", out)
        self.assertNotIn("헬스장 50,000원 —", out)
        _, out2 = self.run_cmd(at(2026, 11, 3), "import-ledger", str(path))
        self.assertIn('sub add "넷플릭스" 13500', out2)
        self.run_cmd(at(2026, 11, 3), "sub", "add", "넷플릭스", "13500", "--day", "15")
        _, out3 = self.run_cmd(at(2026, 11, 3), "import-ledger", str(path))
        self.assertNotIn("구독 후보", out3)


class ReportAndExtrasTests(ImportBase):
    def test_report_source_totals_and_unified_ranking(self):
        self.run_cmd(at(2026, 11, 2), "import-steam", str(self.steam_file({
            "1": {"appid": 1, "name": "Hades", "price_paid": 25000, "purchased_at": "2026-09-01T10:00:00",
                  "rating": 5}})))
        self.run_cmd(at(2026, 9, 5), "bought", "이어폰", "--price", "100000")
        self.answers = ["4", "매일"]
        self.run_cmd(at(2026, 10, 20), "check", interactive=True)
        self.run_cmd(at(2026, 11, 2), "import-ledger", str(self.ledger_file([("2026-10-01", "마트", 30000)])))
        _, out = self.run_cmd(at(2026, 11, 2), "report")
        self.assertIn("출처별 합계", out)
        self.assertIn("스팀", out)
        self.assertIn("가계부", out)
        self.assertIn("직접 기록", out)
        rank = out.split("원당 만족도")[1]
        self.assertLess(rank.index("Hades"), rank.index("이어폰"))

    def test_week_line_and_configurable_chicken_price(self):
        self.run_cmd(at(2026, 11, 2), "bought", "책", "--price", "30000", "--date", "어제")
        self.run_cmd(at(2026, 11, 2), "bought", "옛날", "--price", "99000", "--date", "2026-10-01")
        _, out = self.run_cmd(at(2026, 11, 2), "report", "--week")
        self.assertIn("지출 30,000원 = 치킨 1.5마리 (1마리 20,000원)", out)
        _, out = self.run_cmd(at(2026, 11, 2), "report", "--week", "--chicken-price", "30000")
        self.assertIn("치킨 1.0마리 (1마리 30,000원)", out)
        _, out = self.run_cmd(at(2026, 11, 2), "report")
        self.assertNotIn("치킨 환산", out)

    def test_funeral_on_sub_remove(self):
        self.run_cmd(at(2024, 1, 15), "sub", "add", "넷플릭스", "13500", "--day", "15")
        _, out = self.run_cmd(at(2026, 4, 20), "sub", "remove", "넷플릭스")
        # 2024-01 ~ 2026-04 = 28번 결제
        self.assertIn("고 넷플릭스(2024~2026), 총 37만8천원을 남기고 떠나다", out)
        self.assertIn("결제 28번, 27개월", out)

    def test_funeral_without_payments(self):
        self.run_cmd(at(2026, 11, 1), "sub", "add", "신규", "9900", "--day", "20")
        _, out = self.run_cmd(at(2026, 11, 2), "sub", "remove", "신규")
        self.assertIn("한 푼도 못 받고", out)

    def test_korean_won(self):
        self.assertEqual(core.korean_won(324000), "32만4천원")
        self.assertEqual(core.korean_won(13500), "1만3천5백원")
        self.assertEqual(core.korean_won(100_000_000), "1억원")


if __name__ == "__main__":
    unittest.main()
