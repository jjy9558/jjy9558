# -*- coding: utf-8 -*-
"""월급 3봉투(budget set/show)와 예산 비상벨(budget alarm)."""
import contextlib
import datetime as dt
import io
import json
import unittest

from helpers import TmpCase
from paperbutler import budget as bg
from paperbutler import cli, contract


def sms(kind, amount, when, merchant):
    return "[신한카드] %s %s원 일시불 %s %s" % (kind, "{:,}".format(amount), when, merchant)


class BudgetCase(TmpCase):
    today_s = "2026-11-04"

    def run_cli(self, *argv, today=None, ntfy_send=None):
        out, err = io.StringIO(), io.StringIO()
        args = ["--home", str(self.home), "--today", today or self.today_s] + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(args, ntfy_send=ntfy_send)
        return rc, out.getvalue(), err.getvalue()

    def add(self, text, today=dt.date(2026, 11, 4)):
        b = self.butler(today=today)
        res = b.add_text(text, "t.txt")
        b.close()
        return res

    def setup_budget(self, *extra):
        rc, out, err = self.run_cli("budget", "set", "--income", "2500000", "--fixed", "40%",
                                    "--living", "40%", "--save", "20%", *extra)
        self.assertEqual(rc, 0, err)
        return out


class TestSet(BudgetCase):
    def test_percent_mode(self):
        out = self.setup_budget()
        self.assertIn("1,000,000원", out)
        cfg = bg.load_config(self.home)
        self.assertEqual([bg.envelope_amount(cfg, k) for k in bg.KEYS], [1000000, 1000000, 500000])

    def test_absolute_and_mixed_mode(self):
        rc, _, _ = self.run_cli("budget", "set", "--income", "250만", "--fixed", "800,000",
                                "--living", "50%", "--save", "300000")
        self.assertEqual(rc, 0)
        cfg = bg.load_config(self.home)
        self.assertEqual([bg.envelope_amount(cfg, k) for k in bg.KEYS], [800000, 1250000, 300000])

    def test_absolute_without_income(self):
        rc, _, _ = self.run_cli("budget", "set", "--fixed", "100000", "--living", "200000", "--save", "50000")
        self.assertEqual(rc, 0)

    def test_percent_needs_income_and_max_100(self):
        rc, _, err = self.run_cli("budget", "set", "--living", "40%")
        self.assertEqual(rc, 2)
        self.assertIn("월급", err)
        rc, _, err = self.run_cli("budget", "set", "--income", "1000000", "--fixed", "60%", "--living", "50%")
        self.assertEqual(rc, 2)
        self.assertIn("100", err)

    def test_absolute_over_income_warns(self):
        rc, out, _ = self.run_cli("budget", "set", "--income", "1000000", "--fixed", "900000", "--living", "300000")
        self.assertEqual(rc, 0)
        self.assertIn("주의", out)

    def test_bad_inputs(self):
        for bad in (["--income", "abc"], ["--fixed=-5%"], ["--thresholds", "0,5"], ["--map", "없는봉투=식비"],
                    ["--map", "생활비식비"]):
            rc, _, _ = self.run_cli("budget", "set", *bad)
            self.assertEqual(rc, 2, bad)

    def test_map_editable_and_no_duplicates(self):
        self.setup_budget("--map", "고정비=공과금,구독", "--map", "living=식비,교통,쇼핑,문화,기타,의료,교육")
        cfg = bg.load_config(self.home)
        self.assertIn("의료", cfg["envelopes"]["living"]["categories"])
        rc, _, err = self.run_cli("budget", "set", "--map", "고정비=식비")
        self.assertEqual(rc, 2)
        self.assertIn("두 봉투", err)

    def test_partial_update_keeps_rest(self):
        self.setup_budget()
        self.run_cli("budget", "set", "--living", "500000")
        cfg = bg.load_config(self.home)
        self.assertEqual(bg.envelope_amount(cfg, "living"), 500000)
        self.assertEqual(bg.envelope_amount(cfg, "fixed"), 1000000)

    def test_show_without_budget(self):
        rc, _, err = self.run_cli("budget", "show")
        self.assertEqual(rc, 1)
        self.assertIn("budget set", err)

    def test_corrupt_file(self):
        self.home.mkdir(parents=True)
        (self.home / "budget.json").write_text("{not json", encoding="utf-8")
        rc, _, err = self.run_cli("budget", "show")
        self.assertEqual(rc, 2)
        self.assertIn("budget.json", err)


class TestShow(BudgetCase):
    def test_envelope_math_and_daily(self):
        self.setup_budget()
        self.add(sms("승인", 100000, "11/02 12:00", "GS25역삼점"))          # 식비 -> 생활비
        self.add(sms("승인", 45230, "11/03 12:00", "한국전력"))             # 공과금 -> 고정비
        rc, out, _ = self.run_cli("budget", "show")
        self.assertEqual(rc, 0)
        self.assertIn("100,000원 / 1,000,000원", out)
        self.assertIn("45,230원 / 1,000,000원", out)
        self.assertIn("█", out)
        # 11/04 포함 11/30 까지 27일, 900000 // 27
        self.assertIn("남은 날: 27일", out)
        self.assertIn("하루 쓸 수 있는 돈: %s원" % "{:,}".format(900000 // 27), out)
        self.assertIn("지출 추적 안 함", out)

    def test_cancel_handling(self):
        self.setup_budget()
        self.add(sms("승인", 80000, "11/02 12:00", "이마트"))
        self.add(sms("승인", 20000, "11/02 13:00", "쿠팡이츠"))
        self.add(sms("취소", 80000, "11/02 12:30", "이마트"))
        cfg = bg.load_config(self.home)
        b = self.butler()
        info = bg.compute(cfg, b.ledger("2026-11"), "2026-11", dt.date(2026, 11, 4))
        self.assertEqual(info["envelopes"][1]["spent"], 20000)

    def test_unmatched_cancel_refunds(self):
        self.setup_budget()
        self.add(sms("승인", 50000, "11/02 12:00", "이마트"))
        self.add(sms("취소", 10000, "11/03 12:30", "홈플러스"))        # 짝 없는 취소 -> 환불로 차감
        b = self.butler()
        info = bg.compute(bg.load_config(self.home), b.ledger("2026-11"), "2026-11", dt.date(2026, 11, 4))
        self.assertEqual(info["envelopes"][1]["spent"], 40000)

    def test_overspent_bar_and_zero_daily(self):
        self.setup_budget("--thresholds", "50,100")
        self.add(sms("승인", 1200000, "11/02 12:00", "이마트"))
        rc, out, _ = self.run_cli("budget", "show")
        self.assertIn("초과 200,000원", out)
        self.assertIn("하루 쓸 수 있는 돈: 0원", out)
        self.assertIn(" !", out)

    def test_month_boundaries(self):
        self.setup_budget()
        self.add(sms("승인", 300000, "10/31 23:50", "이마트"), today=dt.date(2026, 10, 31))
        self.add(sms("승인", 100000, "11/01 00:10", "이마트"))
        rc, out, _ = self.run_cli("budget", "show", today="2026-11-30")
        self.assertIn("100,000원 / 1,000,000원", out)
        self.assertIn("남은 날: 1일", out)
        rc, out, _ = self.run_cli("budget", "show", "--month", "2026-10", today="2026-11-30")
        self.assertIn("300,000원 / 1,000,000원", out)
        self.assertIn("지난 달", out)
        rc, out, _ = self.run_cli("budget", "show", "--month", "2026-12", today="2026-11-30")
        self.assertIn("아직 시작 전", out)
        rc, _, _ = self.run_cli("budget", "show", "--month", "2026-13")
        self.assertEqual(rc, 2)

    def test_days_left(self):
        self.assertEqual(bg.month_info("2026-02", dt.date(2026, 2, 28)), (1, "current"))
        self.assertEqual(bg.month_info("2024-02", dt.date(2024, 2, 1)), (29, "current"))
        self.assertEqual(bg.month_info("2026-12", dt.date(2026, 11, 30)), (31, "future"))

    def test_unmapped_category_reported(self):
        self.setup_budget("--map", "생활비=식비")
        self.add(sms("승인", 9000, "11/02 12:00", "쿠팡"))                  # 쇼핑: 매핑 없음
        rc, out, _ = self.run_cli("budget", "show")
        self.assertIn("매핑 안 된", out)
        self.assertIn("쇼핑", out)

    def test_no_raw_card_digits(self):
        self.setup_budget()
        self.add("신한(1234)승인 홍*동 12,340원(일시불)11/02 14:32 GS25역삼점 누적123,000원")
        _, out, err = self.run_cli("budget", "show")
        self.assertNotIn("1234", out + err.replace("1,234", ""))


class TestAlarm(BudgetCase):
    def spend(self, amount, when="11/02 12:00", merchant="이마트"):
        self.add(sms("승인", amount, when, merchant))

    def test_thresholds_dedupe(self):
        self.setup_budget()
        self.spend(600000)
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("새 경보 없음", out)
        self.spend(150000, "11/03 12:00", "롯데마트")                  # 75%
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("[생활비 70%]", out)
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("새 경보 없음", out)                             # 반복 없음
        self.spend(160000, "11/03 13:00", "홈플러스")                  # 91%
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("[생활비 90%]", out)
        self.assertIn("편의점 출입 금지 권고", out)
        self.assertNotIn("70%]", out)
        self.spend(100000, "11/03 14:00", "이마트")                    # 101%
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("[생활비 100%]", out)
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("새 경보 없음", out)

    def test_jump_alerts_only_highest_and_records_lower(self):
        self.setup_budget()
        self.spend(950000)
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("[생활비 90%]", out)
        self.assertEqual(out.count("["), 1)
        state = json.loads((self.home / "budget-alarms.json").read_text(encoding="utf-8"))
        self.assertEqual(state["2026-11"]["living"], [70, 90])

    def test_new_month_resets(self):
        self.setup_budget()
        self.spend(800000)
        self.run_cli("budget", "alarm")
        self.add(sms("승인", 800000, "12/02 12:00", "이마트"), today=dt.date(2026, 12, 2))
        rc, out, _ = self.run_cli("budget", "alarm", today="2026-12-02")
        self.assertIn("[생활비 70%]", out)

    def test_month_boundary_ignores_other_month_spend(self):
        self.setup_budget()
        self.add(sms("승인", 900000, "10/31 23:59", "이마트"), today=dt.date(2026, 10, 31))
        rc, out, _ = self.run_cli("budget", "alarm", today="2026-11-01")
        self.assertIn("새 경보 없음", out)

    def test_cancel_lowers_below_threshold(self):
        self.setup_budget()
        self.spend(800000)
        self.add(sms("취소", 800000, "11/02 12:30", "이마트"))
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("새 경보 없음", out)

    def test_fixed_envelope_and_custom_thresholds(self):
        self.setup_budget("--thresholds", "50,100")
        self.add(sms("승인", 500000, "11/02 12:00", "한국전력"))
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("[고정비 50%]", out)
        self.assertIn("고정비 봉투 50% 소진", out)

    def test_save_envelope_not_alarmed(self):
        self.setup_budget()
        rc, out, _ = self.run_cli("budget", "alarm", "--json")
        obj = json.loads(out)
        self.assertEqual(obj["alerts"], [])
        self.assertEqual(obj["envelopes"][2]["crossed"], [])

    def test_dry_run_keeps_no_state(self):
        self.setup_budget()
        self.spend(800000)
        rc, out, _ = self.run_cli("budget", "alarm", "--dry-run")
        self.assertIn("[생활비 70%]", out)
        rc, out, _ = self.run_cli("budget", "alarm")
        self.assertIn("[생활비 70%]", out)

    def test_ntfy_sent(self):
        self.setup_budget()
        self.spend(800000)
        sent = []
        rc, out, _ = self.run_cli("budget", "alarm", "--ntfy", "my-topic", "--json",
                                  ntfy_send=lambda t, m, title: sent.append((t, m, title)))
        self.assertEqual(rc, 0)
        self.assertEqual(sent[0][0], "my-topic")
        self.assertIn("70%", sent[0][2])
        self.assertEqual(json.loads(out)["alerts"][0]["ntfy"], "sent")

    def test_ntfy_failure_is_safe_and_retried(self):
        self.setup_budget()
        self.spend(800000)

        def boom(t, m, title):
            raise OSError("network down")
        rc, out, err = self.run_cli("budget", "alarm", "--ntfy", "t", ntfy_send=boom)
        self.assertEqual(rc, 0)
        self.assertIn("[생활비 70%]", out)
        self.assertIn("ntfy 전송에 실패", err)
        self.assertFalse((self.home / "budget-alarms.json").exists())
        rc, out, _ = self.run_cli("budget", "alarm", "--ntfy", "t", ntfy_send=lambda *a: None)
        self.assertIn("[생활비 70%]", out)                              # 재시도됨
        rc, out, _ = self.run_cli("budget", "alarm", "--ntfy", "t", ntfy_send=boom)
        self.assertIn("새 경보 없음", out)

    def test_ntfy_default_sender_failure_with_fake_network(self):
        # 기본 전송기(jjkit notify)가 네트워크 오류를 내도 안전해야 한다 (실제 접속 없이 호출을 가로챔)
        from paperbutler._jjkit import notify
        self.setup_budget()
        self.spend(800000)
        orig = notify.ntfy

        def fail(*a, **k):
            raise notify.NotifyError("ntfy 전송 실패: fake")
        notify.ntfy = fail
        self.addCleanup(setattr, notify, "ntfy", orig)
        rc, out, err = self.run_cli("budget", "alarm", "--ntfy", "t")
        self.assertEqual(rc, 0)
        self.assertIn("실패", err)

    def test_json_validates(self):
        self.setup_budget()
        self.spend(950000)
        rc, out, _ = self.run_cli("budget", "alarm", "--json")
        obj = json.loads(out)                          # stdout 은 JSON 만
        contract.validate_budget(obj)
        self.assertEqual(obj["version"], 1)
        self.assertEqual(obj["generated_for"], "2026-11-04")
        self.assertEqual(obj["alerts"][0]["threshold"], 90)
        self.assertEqual(obj["alerts"][0]["ntfy"], "off")
        self.assertEqual(contract.SCHEMA_VERSION, 1)

    def test_validate_budget_rejects(self):
        self.setup_budget()
        rc, out, _ = self.run_cli("budget", "alarm", "--json")
        good = json.loads(out)
        contract.validate_budget(good)
        for mut in (lambda o: o.pop("kind"), lambda o: o.update(version=2),
                    lambda o: o["envelopes"][0].update(remaining=5),
                    lambda o: o.update(month="2026-1"), lambda o: o.update(daily_allowance=-1),
                    lambda o: o.update(alerts=[{"envelope": "x"}])):
            bad = json.loads(out)
            mut(bad)
            with self.assertRaises(ValueError):
                contract.validate_budget(bad)

    def test_old_payload_schema_untouched(self):
        with self.assertRaises(ValueError):
            contract.validate(json.loads('{"version":1,"kind":"budget"}'))


if __name__ == "__main__":
    unittest.main()
