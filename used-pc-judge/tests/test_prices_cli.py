import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date

from tests.helpers import Base, ROOT, make_table
from usedpc import catalog
from usedpc.cli import main
from usedpc.prices import DEFAULT_PATH, PriceError, PriceTable

LISTING = """[급처] 12400f 3060ti 완본체 팝니다
인텔 i5-12400F / RTX 3060 Ti 8G / 램 16기가(8x2) DDR4 3200 / NVMe 500G
메인보드 B660M 박격포, 파워 시소닉 포커스 GX-650 80+ 골드
쿨러 소음 약간 있어요. 박스 없음. 모니터 별도.
직거래만 합니다. 가격 60만 (네고 가능)
"""


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def run_cli(argv, stdin_bytes=None):
    out, err = io.StringIO(), io.StringIO()
    old_stdin = sys.stdin
    if stdin_bytes is not None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin_bytes), encoding="utf-8")
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
    finally:
        sys.stdin = old_stdin
    return code, out.getvalue(), err.getvalue()


class ShippedPriceTable(Base):
    def setUp(self):
        self.t = PriceTable.load(DEFAULT_PATH)

    def test_has_big_warning_and_placeholder(self):
        self.assertTrue(self.t.placeholder)
        warn = " ".join(self.t.data["_WARNING"])
        self.assertIn("PLACEHOLDER", warn)
        self.assertIn("거래완료", warn)
        self.assertTrue(self.t.warnings(date(2026, 11, 3))[0].startswith("!!!"))

    def test_as_of_is_valid_date(self):
        self.assertIsNotNone(self.t.as_of)

    def test_every_catalog_part_has_a_price(self):
        self.assertEqual([n for n in catalog.GPUS if n not in self.t.data["gpu"]], [])
        self.assertEqual([n for n in catalog.CPUS if n not in self.t.data["cpu"]], [])
        self.assertEqual([c for c in catalog.CHIPSETS if c not in self.t.data["board"]], [])

    def test_prices_are_positive_ints(self):
        for sec in ("gpu", "cpu", "board"):
            for k, v in self.t.data[sec].items():
                self.assertIsInstance(v, int, k)
                self.assertGreater(v, 0, k)

    def test_price_ordering_is_sane(self):
        g = self.t.data["gpu"]
        self.assertLess(g["RTX 3060 12GB"], g["RTX 4070 Super"])
        self.assertLess(g["RTX 4070 Super"], g["RTX 4090"])
        self.assertLess(g["RX 6600"], g["RX 7900 XTX"])

    def test_adjust_defaults_present(self):
        from usedpc.prices import DEFAULT_ADJUST
        for k in DEFAULT_ADJUST:
            self.assertIn(k, self.t.data["adjust"], k)


class PriceTableOps(Base):
    def test_lookup_helpers(self):
        t = make_table()
        self.assertEqual(t.cpu("i5-12400F"), (100000, False))
        price, approx = t.cpu("i5-12490F")      # 카탈로그에 없음 -> 가장 가까운 모델
        self.assertTrue(approx)
        self.assertIsNotNone(price)
        self.assertEqual(t.gpu("RTX 9999"), (None, False))
        self.assertEqual(t.ram(32, "DDR5"), (160000, False))
        self.assertEqual(t.ram(16, None), (40000, True))
        self.assertEqual(t.storage("nvme", 1000), 80000)
        self.assertEqual(t.storage("ssd", 100), 15000)      # 최소가
        self.assertEqual(t.storage("hdd", 2000), 40000)
        self.assertEqual(t.board("b660"), 100000)

    def test_psu_pricing(self):
        t = make_table()
        self.assertEqual(t.psu(650, "Bronze", "B"), 40000)
        self.assertEqual(t.psu(650, "Gold", "A"), 62500)
        self.assertEqual(t.psu(600, "Bronze", "B"), 40000)       # 600W -> 650W 구간
        self.assertEqual(t.psu(650, "Bronze", None, noname=True), 24000)
        self.assertEqual(t.psu(3000, "Bronze", "B"), 105000)      # 표 최대 구간
        self.assertLess(t.psu(650, None, None), 40000)

    def test_monitor_and_misc(self):
        t = make_table()
        self.assertEqual(t.monitor({"inch": 27, "hz": 144}), 98000)
        self.assertEqual(t.monitor({"inch": 27, "hz": 60}), 70000)
        self.assertEqual(t.monitor(None), 50000)
        self.assertEqual(t.case("프랙탈 노스"), 80000)
        self.assertEqual(t.case("아무거나"), 30000)
        self.assertEqual(t.cooler({"kind": "aio", "size": 360}), 80000)
        self.assertEqual(t.cooler({"kind": "stock"}), 0)
        self.assertEqual(t.peripherals(["키보드", "마우스"]), 18000)

    def test_new_price_fallback(self):
        t = make_table()
        self.assertEqual(t.new_price("gpu", "RTX 4090", 2600000), 3000000)       # 신품 표에 있음
        self.assertEqual(t.new_price("gpu", "RTX 3070", 330000), int(330000 / 0.7))
        self.assertIsNone(t.new_price("gpu", "RTX 3070", None))

    def test_resolve_names(self):
        t = make_table()
        self.assertEqual(t.resolve("RTX 4070 Super"), ("gpu", "RTX 4070 Super"))
        self.assertEqual(t.resolve("rtx 4070 super"), ("gpu", "RTX 4070 Super"))
        self.assertEqual(t.resolve("4070 슈퍼"), ("gpu", "RTX 4070 Super"))
        self.assertEqual(t.resolve("3060ti"), ("gpu", "RTX 3060 Ti"))
        self.assertEqual(t.resolve("12400f"), ("cpu", "i5-12400F"))
        self.assertEqual(t.resolve("라이젠 5600"), ("cpu", "Ryzen 5 5600"))
        self.assertEqual(t.resolve("B660"), ("board", "B660"))
        self.assertEqual(t.resolve("ram_per_gb.DDR4"), ("ram_per_gb", "DDR4"))
        with self.assertRaises(PriceError):
            t.resolve("없는부품")

    def test_set_price_and_save_roundtrip(self):
        t = make_table()
        sec, key, old = t.set_price("RTX 4070 Super", 650000)
        self.assertEqual((sec, key, old), ("gpu", "RTX 4070 Super", 680000))
        self.assertEqual(t.gpu("RTX 4070 Super")[0], 650000)
        t.set_price("RTX 4070 Super", 700000, new=True)
        self.assertEqual(t.new_price("gpu", "RTX 4070 Super", 1), 700000)
        with self.assertRaises(PriceError):
            t.set_price("B660", 1, new=True)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            t.save(path)
            t2 = PriceTable.load(path)
            self.assertEqual(t2.gpu("RTX 4070 Super")[0], 650000)

    def test_load_errors(self):
        with self.assertRaises(PriceError):
            PriceTable.load("/nonexistent/prices.json")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.json")
            with open(path, "w") as f:
                f.write("{not json")
            with self.assertRaises(PriceError):
                PriceTable.load(path)

    def test_stamp(self):
        t = make_table()
        t.data["placeholder"] = True
        t.stamp(date(2026, 12, 1))
        self.assertEqual(t.data["as_of"], "2026-12-01")
        self.assertFalse(t.placeholder)


class CliTests(Base):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prices = os.path.join(self.tmp, "prices.json")
        shutil.copy(str(DEFAULT_PATH), self.prices)
        self.listing = os.path.join(self.tmp, "listing.txt")
        with open(self.listing, "w", encoding="utf-8") as f:
            f.write(LISTING)

    def test_judge_output(self):
        code, out, err = run_cli(["judge", self.listing, "--prices", self.prices, "--today", "2026-11-02"])
        self.assertEqual(code, 0, err)
        for word in ("판매가", "적정가 범위", "판정", "[판정 이유]", "[판매자에게 물어볼 것]",
                     "혹시 파워 브랜드/모델", "[복사해서 보낼 메시지 템플릿]", "임시 추정치"):
            if word == "혹시 파워 브랜드/모델":
                continue    # 파워 정보가 있는 매물이므로 해당 질문은 없음
            self.assertIn(word, out)
        self.assertIn("60만원", out)

    def test_judge_with_use_and_budget_and_md(self):
        md = os.path.join(self.tmp, "r.md")
        code, out, err = run_cli(["judge", self.listing, "--prices", self.prices, "--today", "2026-11-02",
                                  "--use", "게임 1440p", "--budget", "120", "--md", md])
        self.assertEqual(code, 0, err)
        self.assertIn("[업그레이드 추천]", out)
        self.assertIn("→ 총", out)
        with open(md, encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("# 중고 PC 판정"))
        self.assertIn("## 판정 이유", text)
        self.assertIn("## 업그레이드 추천", text)
        self.assertIn("```", text)

    def test_judge_stdin(self):
        code, out, err = run_cli(["judge", "-", "--prices", self.prices, "--today", "2026-11-02"],
                                 stdin_bytes=LISTING.encode("utf-8"))
        self.assertEqual(code, 0, err)
        self.assertIn("판정", out)

    def test_judge_cp949_file(self):
        path = os.path.join(self.tmp, "cp949.txt")
        with open(path, "wb") as f:
            f.write(LISTING.encode("cp949"))
        code, out, err = run_cli(["judge", path, "--prices", self.prices, "--today", "2026-11-02"])
        self.assertEqual(code, 0, err)
        self.assertIn("RTX 3060 Ti", out)

    def test_judge_errors(self):
        code, _, err = run_cli(["judge", os.path.join(self.tmp, "none.txt"), "--prices", self.prices])
        self.assertEqual(code, 2)
        code, _, err = run_cli(["judge", self.listing, "--prices", self.prices, "--use", "요리"])
        self.assertEqual(code, 2)
        self.assertIn("알 수 없는 용도", err)
        code, _, err = run_cli(["judge", self.listing, "--prices", self.prices, "--use", "게임", "--budget", "abc"])
        self.assertEqual(code, 2)
        code, _, err = run_cli(["judge", self.listing, "--prices", "/nonexistent.json"])
        self.assertEqual(code, 2)

    def test_judge_no_questions_flag(self):
        code, out, _ = run_cli(["judge", self.listing, "--prices", self.prices, "--no-questions"])
        self.assertEqual(code, 0)
        self.assertNotIn("[판매자에게 물어볼 것]", out)

    def test_compare(self):
        path = os.path.join(self.tmp, "many.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(LISTING + "\n---\n" + "라이젠 5600 + RX 6600 본체 램 16G ssd 500g 파워 500w 45만\n")
        md = os.path.join(self.tmp, "c.md")
        code, out, err = run_cli(["compare", path, "--prices", self.prices, "--today", "2026-11-02", "--md", md])
        self.assertEqual(code, 0, err)
        self.assertIn("순위", out)
        self.assertIn("라이젠 5600", out)
        with open(md, encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("# 중고 PC 매물 비교"))

    def test_multiple_listings_in_judge_warns(self):
        path = os.path.join(self.tmp, "many.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(LISTING + "\n---\n" + LISTING)
        code, out, _ = run_cli(["judge", path, "--prices", self.prices])
        self.assertEqual(code, 0)
        self.assertIn("compare", out)

    def test_prices_show(self):
        code, out, _ = run_cli(["prices", "show", "--prices", self.prices])
        self.assertEqual(code, 0)
        self.assertIn("[gpu]", out)
        self.assertIn("RTX 4070 Super", out)
        self.assertIn("임시 추정치", out)
        code, out, _ = run_cli(["prices", "show", "adjust", "--prices", self.prices])
        self.assertIn("assembled_factor", out)

    def test_prices_set_variants(self):
        code, out, err = run_cli(["prices", "set", "RTX 4070 Super", "68만", "--prices", self.prices])
        self.assertEqual(code, 0, err)
        data = load_json(self.prices)
        self.assertEqual(data["gpu"]["RTX 4070 Super"], 680000)
        code, _, _ = run_cli(["prices", "set", "i5-12400F", "85", "--prices", self.prices])      # 85 = 85만원
        self.assertEqual(load_json(self.prices)["cpu"]["i5-12400F"], 850000)
        run_cli(["prices", "set", "3060ti", "27.5만", "--prices", self.prices])
        self.assertEqual(load_json(self.prices)["gpu"]["RTX 3060 Ti"], 275000)
        run_cli(["prices", "set", "ram_per_gb.DDR4", "3000", "--prices", self.prices])
        self.assertEqual(load_json(self.prices)["ram_per_gb"]["DDR4"], 3000)
        run_cli(["prices", "set", "RTX 4090", "3000000", "--new", "--prices", self.prices])
        self.assertEqual(load_json(self.prices)["new"]["gpu"]["RTX 4090"], 3000000)
        run_cli(["prices", "set", "adjust.assembled_factor", "0.9", "--prices", self.prices])
        self.assertEqual(load_json(self.prices)["adjust"]["assembled_factor"], 0.9)

    def test_set_price_changes_judgment(self):
        _, before, _ = run_cli(["judge", self.listing, "--prices", self.prices, "--today", "2026-11-02"])
        run_cli(["prices", "set", "RTX 3060 Ti", "50만", "--prices", self.prices])
        _, after, _ = run_cli(["judge", self.listing, "--prices", self.prices, "--today", "2026-11-02"])
        self.assertNotEqual(before, after)
        self.assertIn("적정가 범위", after)

    def test_prices_set_errors(self):
        code, _, err = run_cli(["prices", "set", "없는부품", "10만", "--prices", self.prices])
        self.assertEqual(code, 2)
        code, _, err = run_cli(["prices", "set", "RTX 4070 Super", "abc", "--prices", self.prices])
        self.assertEqual(code, 2)
        code, _, err = run_cli(["prices", "set", "RTX 4070 Super", "-5", "--prices", self.prices])
        self.assertEqual(code, 2)
        code, _, err = run_cli(["prices", "set", "adjust.assembled_factor", "많이", "--prices", self.prices])
        self.assertEqual(code, 2)

    def test_prices_stamp(self):
        code, out, _ = run_cli(["prices", "stamp", "--prices", self.prices])
        self.assertEqual(code, 0)
        data = load_json(self.prices)
        self.assertFalse(data["placeholder"])
        self.assertEqual(data["as_of"], date.today().isoformat())
        run_cli(["prices", "stamp", "--keep-placeholder", "--prices", self.prices])
        self.assertTrue(load_json(self.prices)["placeholder"])

    def test_no_command(self):
        code, out, _ = run_cli([])
        self.assertEqual(code, 2)

    def test_python_dash_m_smoke(self):
        r = subprocess.run([sys.executable, "-m", "usedpc", "judge", self.listing, "--prices", self.prices,
                            "--today", "2026-11-02"], cwd=ROOT, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn("판정", r.stdout.decode("utf-8"))

    def test_shipped_prices_file_is_not_modified_by_tests(self):
        before = read_bytes(str(DEFAULT_PATH))
        run_cli(["prices", "set", "RTX 4070 Super", "1만", "--prices", self.prices])
        self.assertEqual(before, read_bytes(str(DEFAULT_PATH)))


if __name__ == "__main__":
    unittest.main()
