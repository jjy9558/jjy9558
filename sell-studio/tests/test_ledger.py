import csv
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sellstudio import ledger as lg
from tests.helpers import run


class LedgerCoreTests(unittest.TestCase):
    def test_profit_math(self):
        data = {"items": []}
        lg.add(data, name="RTX 3060", buy_price="15만", buy_fee=3000, repair="1만", buy_date="2026-10-01")
        item = data["items"][0]
        lg.sell(data, item["id"], price="22만", fee=7700, shipping=3500, sold_date="2026-10-11")
        m = lg.metrics(item, date(2026, 11, 1))
        self.assertEqual(m["cost_total"], 163000)
        self.assertEqual(m["profit"], 220000 - 163000 - 7700 - 3500)
        self.assertEqual(m["hold_days"], 10)
        self.assertAlmostEqual(m["roi_pct"], round(45800 * 100 / 163000, 1))

    def test_fee_rate_and_market_rate(self):
        data = {"items": []}
        lg.add(data, name="A", buy_price=100000, buy_date="2026-10-01")
        item, _ = lg.sell(data, "A", price=200000, fee_rate=3.5, sold_date="2026-10-02")
        self.assertEqual(item["sold"]["fee"], 7000)
        item, st = lg.sell(data, "A", price=200000, market_rate=0.055, market="smartstore", sold_date="2026-10-02")
        self.assertEqual(item["sold"]["fee"], 11000)
        self.assertEqual(st, "updated")

    def test_add_idempotent(self):
        data = {"items": []}
        _, st1 = lg.add(data, name="X", buy_price=50000, buy_date="2026-10-01")
        _, st2 = lg.add(data, name="X", buy_price=50000, buy_date="2026-10-01")
        _, st3 = lg.add(data, name="X", buy_price=50000, today=date(2027, 1, 1))  # 날짜 생략 + 다른 날 실행
        self.assertEqual((st1, st2, st3), ("added", "unchanged", "unchanged"))
        self.assertEqual(len(data["items"]), 1)

    def test_same_name_different_ids(self):
        data = {"items": []}
        lg.add(data, name="X", buy_price=50000, item_id="a1")
        lg.add(data, name="X", buy_price=50000, item_id="a2")
        self.assertEqual(len(data["items"]), 2)
        with self.assertRaises(lg.LedgerError):
            lg.sell(data, "X", price=1)  # 이름이 겹치면 ID 를 요구

    def test_sold_keeps_date_on_rerun(self):
        data = {"items": []}
        lg.add(data, name="X", buy_price=50000, buy_date="2026-10-01")
        lg.sell(data, "X", price=80000, fee=0, sold_date="2026-10-05")
        _, st = lg.sell(data, "X", price=80000, fee=0, today=date(2027, 3, 3))
        self.assertEqual(st, "unchanged")
        self.assertEqual(data["items"][0]["sold"]["date"], "2026-10-05")

    def test_validation(self):
        data = {"items": []}
        with self.assertRaises(lg.LedgerError):
            lg.add(data, name="X", buy_price="abc")
        lg.add(data, name="X", buy_price=1000, buy_date="2026-10-05")
        with self.assertRaises(lg.LedgerError):
            lg.sell(data, "X", price=2000, sold_date="2026-10-01")  # 매입일보다 빠름
        with self.assertRaises(lg.LedgerError):
            lg.sell(data, "없음", price=1)

    def test_hourly(self):
        data = {"items": []}
        lg.add(data, name="X", buy_price=100000, hours=2, buy_date="2026-10-01")
        lg.sell(data, "X", price=150000, fee=0, sold_date="2026-10-02")
        self.assertEqual(lg.metrics(data["items"][0], date(2026, 10, 3))["hourly"], 25000)


class LedgerCliTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.f = str(self.dir / "l.json")

    def tearDown(self):
        self._t.cleanup()

    def sha(self):
        return hashlib.sha256(Path(self.f).read_bytes()).hexdigest()

    def test_flow_and_idempotence(self):
        base = ["ledger"]
        c, o, _ = run(base + ["add", "--ledger", self.f, "--name", "그래픽카드", "--buy", "15만", "--date", "2026-10-01",
                              "--repair", "1만", "--hours", "1.5"])
        self.assertEqual(c, 0)
        self.assertIn("추가했습니다", o)
        iid = json.loads(Path(self.f).read_text(encoding="utf-8"))["items"][0]["id"]
        c, o, _ = run(base + ["sold", "--ledger", self.f, iid, "--price", "22만", "--market", "bunjang", "--shipping", "3500",
                              "--date", "2026-10-11"])
        self.assertEqual(c, 0)
        # 22만 * 3.5% = 7700 -> 220000-160000-7700-3500 = 48800
        self.assertIn("순이익 48,800원", o)
        after_first = self.sha()
        # 재실행: 같은 명령을 다시 해도 장부 파일이 그대로
        run(base + ["add", "--ledger", self.f, "--name", "그래픽카드", "--buy", "15만", "--date", "2026-10-01",
                    "--repair", "1만", "--hours", "1.5"])
        c, o, _ = run(base + ["sold", "--ledger", self.f, iid, "--price", "22만", "--market", "bunjang", "--shipping", "3500",
                              "--date", "2026-10-11"])
        self.assertIn("바꾸지 않았습니다", o)
        self.assertEqual(self.sha(), after_first)
        self.assertEqual(len(json.loads(Path(self.f).read_text(encoding="utf-8"))["items"]), 1)
        # 보고서는 같은 입력이면 같은 출력
        _, r1, _ = run(base + ["report", "--ledger", self.f, "--today", "2026-11-01"])
        _, r2, _ = run(base + ["report", "--ledger", self.f, "--today", "2026-11-01"])
        self.assertEqual(r1, r2)
        self.assertIn("48,800", r1)
        self.assertIn("사업자등록", r1)

    def test_report_csv_json(self):
        run(["ledger", "add", "--ledger", self.f, "--name", "A", "--buy", "10000", "--date", "2026-10-01"])
        run(["ledger", "add", "--ledger", self.f, "--name", "B", "--buy", "20000", "--date", "2026-10-02"])
        run(["ledger", "sold", "--ledger", self.f, "A", "--price", "15000", "--fee", "500", "--date", "2026-10-03"])
        csv_p, js_p = self.dir / "o.csv", self.dir / "o.json"
        c, o, _ = run(["ledger", "report", "--ledger", self.f, "--csv", str(csv_p), "--json", str(js_p), "--today", "2026-11-01"])
        self.assertEqual(c, 0)
        with open(csv_p, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        a = next(r for r in rows if r["name"] == "A")
        self.assertEqual(a["profit"], "4500")
        b = next(r for r in rows if r["name"] == "B")
        self.assertEqual(b["profit"], "")  # 보유중
        js = json.loads(js_p.read_text(encoding="utf-8"))
        self.assertEqual(js["totals"]["profit"], 4500)
        self.assertEqual(js["totals"]["open"], 1)
        self.assertIn("보유 재고 비용 20,000원", o)

    def test_loss_warning(self):
        run(["ledger", "add", "--ledger", self.f, "--name", "A", "--buy", "30000", "--date", "2026-10-01"])
        c, o, _ = run(["ledger", "sold", "--ledger", self.f, "A", "--price", "20000", "--date", "2026-10-02"])
        self.assertIn("손해", o)

    def test_errors_exit_2(self):
        c, _, e = run(["ledger", "sold", "--ledger", self.f, "없는물건", "--price", "1000"])
        self.assertEqual(c, 2)
        self.assertIn("오류", e)
        c, _, e = run(["ledger", "add", "--ledger", self.f, "--name", "A", "--buy", "xx"])
        self.assertEqual(c, 2)

    def test_corrupt_ledger(self):
        Path(self.f).write_text("{broken", encoding="utf-8")
        c, _, e = run(["ledger", "list", "--ledger", self.f])
        self.assertEqual(c, 2)


if __name__ == "__main__":
    unittest.main()
