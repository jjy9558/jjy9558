import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from cool_off import contract, core
from cool_off.cli import App
from cool_off.core import Ledger
from cool_off.store import Store
from tests.fakes import FakeOpener, at


def no_input(prompt):
    raise AssertionError(f"--json 실행 중 입력을 받으면 안 돼요: {prompt!r}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data.json"
        self.opener = FakeOpener()

    def write_state(self, build):
        ledger = Ledger()
        build(ledger)
        Store(self.path).save(ledger.state)

    def run_json(self, now, *extra, interactive=True):
        out = io.StringIO()
        app = App(self.path, now=now, out=out, input_fn=no_input,
                  interactive=interactive, opener=self.opener)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = app.run(["check", "--json", *extra])
        return code, out.getvalue(), err.getvalue()

    def run_json_ok(self, now, *extra):
        code, out, err = self.run_json(now, *extra)
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        payload = json.loads(out)
        contract.validate(payload)
        return payload


class CoolingEndedTests(Base):
    def test_lists_only_wishes_whose_cooling_has_ended(self):
        def build(ledger):
            ledger.add_wish("에어팟 프로", 359000, at(2026, 11, 2, 10))   # 11-05 10:00 종료
            ledger.add_wish("키보드", 120000, at(2026, 11, 4, 10))        # 11-07 10:00 종료
        self.write_state(build)
        payload = self.run_json_ok(at(2026, 11, 5, 10))
        self.assertEqual(payload["cooling_ended"], [
            {"id": "1", "name": "에어팟 프로", "price": 359000, "ended": "2026-11-05"},
        ])
        self.assertEqual(payload["generated_for"], "2026-11-05")

    def test_before_end_time_nothing_listed(self):
        self.write_state(lambda ledger: ledger.add_wish("에어팟 프로", 359000, at(2026, 11, 2, 10)))
        payload = self.run_json_ok(at(2026, 11, 5, 9, 59))
        self.assertEqual(payload["cooling_ended"], [])


class FollowupTests(Base):
    def test_followup_due_thirty_days_after_purchase_and_unanswered(self):
        def build(ledger):
            ledger.record_purchase("에어팟 프로", 329000, dt.date(2026, 10, 1))
            answered, _ = ledger.record_purchase("가방", 90000, dt.date(2026, 10, 1))
            ledger.record_followup(answered, 4, "매일", at(2026, 10, 20))
        self.write_state(build)
        self.assertEqual(self.run_json_ok(at(2026, 10, 30))["followups_due"], [])
        payload = self.run_json_ok(at(2026, 10, 31))
        self.assertEqual(payload["followups_due"], [
            {"id": "1", "name": "에어팟 프로", "bought": "2026-10-01"},
        ])


class RenewalTests(Base):
    def test_renewal_within_three_days_with_days_left(self):
        self.write_state(lambda ledger: ledger.add_subscription(
            "넷플릭스", 13500, core.CYCLE_MONTHLY, 15, None, dt.date(2026, 11, 1)))
        self.assertEqual(self.run_json_ok(at(2026, 11, 11))["renewals"], [])
        payload = self.run_json_ok(at(2026, 11, 12))
        self.assertEqual(payload["renewals"], [
            {"name": "넷플릭스", "amount": 13500, "date": "2026-11-15", "days_left": 3},
        ])
        self.assertEqual(self.run_json_ok(at(2026, 11, 15))["renewals"][0]["days_left"], 0)

    def test_month_end_renewal_clamped_in_february(self):
        self.write_state(lambda ledger: ledger.add_subscription(
            "도메인", 1000, core.CYCLE_MONTHLY, 31, None, dt.date(2026, 1, 1)))
        payload = self.run_json_ok(at(2026, 2, 26))
        self.assertEqual(payload["renewals"], [
            {"name": "도메인", "amount": 1000, "date": "2026-02-28", "days_left": 2},
        ])
        payload = self.run_json_ok(at(2026, 2, 27))
        self.assertEqual(payload["renewals"][0]["date"], "2026-02-28")
        self.assertEqual(payload["renewals"][0]["days_left"], 1)

    def test_month_end_renewal_clamped_to_leap_day(self):
        self.write_state(lambda ledger: ledger.add_subscription(
            "도메인", 1000, core.CYCLE_MONTHLY, 31, None, dt.date(2028, 1, 1)))
        payload = self.run_json_ok(at(2028, 2, 27))
        self.assertEqual(payload["renewals"][0]["date"], "2028-02-29")
        self.assertEqual(payload["renewals"][0]["days_left"], 2)

    def test_month_end_renewal_in_april_and_yearly_february(self):
        def build(ledger):
            ledger.add_subscription("월간", 5000, core.CYCLE_MONTHLY, 31, None, dt.date(2026, 1, 1))
            ledger.add_subscription("연간", 60000, core.CYCLE_YEARLY, 29, 2, dt.date(2026, 1, 1))
        self.write_state(build)
        payload = self.run_json_ok(at(2026, 4, 28))
        self.assertEqual(payload["renewals"], [
            {"name": "월간", "amount": 5000, "date": "2026-04-30", "days_left": 2},
        ])
        payload = self.run_json_ok(at(2027, 2, 26))
        by_name = {r["name"]: r for r in payload["renewals"]}
        self.assertEqual(by_name["연간"], {"name": "연간", "amount": 60000,
                                           "date": "2027-02-28", "days_left": 2})
        self.assertEqual(by_name["월간"]["date"], "2027-02-28")


class ApprovedNotBoughtTests(Base):
    def test_approved_but_not_bought_listed(self):
        def build(ledger):
            approved = ledger.add_wish("모니터", 250000, at(2026, 11, 1))
            ledger.decide_wish(approved, "y", at(2026, 11, 5))
            bought = ledger.add_wish("마우스", 45000, at(2026, 11, 1))
            ledger.decide_wish(bought, "y", at(2026, 11, 5))
            ledger.record_purchase("마우스", None, dt.date(2026, 11, 6))
            skipped = ledger.add_wish("의자", 300000, at(2026, 11, 1))
            ledger.decide_wish(skipped, "n", at(2026, 11, 5))
        self.write_state(build)
        payload = self.run_json_ok(at(2026, 11, 7))
        self.assertEqual(payload["approved_not_bought"], [
            {"id": "1", "name": "모니터", "price": 250000},
        ])


class NonMutationTests(Base):
    def setUp(self):
        super().setUp()

        def build(ledger):
            wish = ledger.add_wish("에어팟 프로", 359000, at(2026, 11, 2, 10))
            ledger.add_wish("키보드", 120000, at(2026, 11, 2, 10))
            ledger.decide_wish(wish, "y", at(2026, 11, 3))
            ledger.record_purchase("가방", 90000, dt.date(2026, 10, 1))
            ledger.add_subscription("넷플릭스", 13500, core.CYCLE_MONTHLY, 15, None, dt.date(2026, 10, 1))
            ledger.mark_notified("wish:2:2026-11-05T10:00", at(2026, 11, 5))
        self.write_state(build)

    def test_data_file_bytes_unchanged_and_no_ntfy(self):
        before = self.path.read_bytes()
        payload = self.run_json_ok(at(2026, 11, 12, 10))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.path.parent)), ["data.json"])
        self.assertEqual(self.opener.requests, [])
        self.assertEqual(len(payload["cooling_ended"]), 1)
        self.assertEqual(len(payload["followups_due"]), 1)
        self.assertEqual(len(payload["renewals"]), 1)
        self.assertEqual(len(payload["approved_not_bought"]), 1)

    def test_repeated_runs_do_not_change_notified_or_wishes(self):
        before = self.path.read_bytes()
        for _ in range(3):
            self.run_json_ok(at(2026, 11, 12, 10))
        self.assertEqual(self.path.read_bytes(), before)

    def test_never_prompts_even_when_interactive(self):
        code, out, _ = self.run_json(at(2026, 11, 12, 10), interactive=True)
        self.assertEqual(code, 0)
        json.loads(out)

    def test_ntfy_is_rejected_and_nothing_is_sent(self):
        before = self.path.read_bytes()
        code, out, err = self.run_json(at(2026, 11, 12, 10), "--ntfy", "cool-off-abc123")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--ntfy", err)
        self.assertEqual(self.opener.requests, [])
        self.assertEqual(self.path.read_bytes(), before)


class EmptyStoreTests(Base):
    def test_missing_file_gives_empty_lists_and_creates_nothing(self):
        payload = self.run_json_ok(at(2026, 11, 2, 10))
        self.assertEqual(payload, {
            "version": 1,
            "generated_for": "2026-11-02",
            "cooling_ended": [],
            "followups_due": [],
            "renewals": [],
            "approved_not_bought": [],
        })
        self.assertFalse(self.path.exists())

    def test_empty_saved_state_gives_empty_lists_and_keeps_bytes(self):
        self.write_state(lambda ledger: None)
        before = self.path.read_bytes()
        payload = self.run_json_ok(at(2026, 11, 2, 10))
        self.assertEqual(payload["cooling_ended"], [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_file_is_error_on_stderr_only(self):
        self.path.write_text("{not json", encoding="utf-8")
        code, out, err = self.run_json(at(2026, 11, 2, 10))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("오류", err)


class ContractTests(unittest.TestCase):
    def sample(self):
        ledger = Ledger()
        wish = ledger.add_wish("에어팟 프로", 359000, at(2026, 11, 2, 10))
        ledger.decide_wish(wish, "y", at(2026, 11, 3))
        ledger.add_subscription("넷플릭스", 13500, core.CYCLE_MONTHLY, 15, None, dt.date(2026, 10, 1))
        return contract.build(ledger, at(2026, 11, 12, 10))

    def test_schema_version_is_one(self):
        self.assertEqual(contract.SCHEMA_VERSION, 1)

    def test_build_output_validates(self):
        payload = self.sample()
        self.assertIs(contract.validate(payload), payload)
        self.assertEqual(list(payload), list(contract.TOP_KEYS))

    def test_rejects_wrong_version_and_missing_or_extra_keys(self):
        payload = self.sample()
        bad = dict(payload, version=2)
        with self.assertRaises(contract.ContractError):
            contract.validate(bad)
        missing = dict(payload)
        del missing["renewals"]
        with self.assertRaises(contract.ContractError):
            contract.validate(missing)
        with self.assertRaises(contract.ContractError):
            contract.validate(dict(payload, extra=1))

    def test_rejects_bad_types_and_dates(self):
        payload = self.sample()
        with_bool_version = dict(payload, version=True)
        with self.assertRaises(contract.ContractError):
            contract.validate(with_bool_version)
        bad_date = json.loads(json.dumps(payload))
        bad_date["generated_for"] = "2026-1-5"
        with self.assertRaises(contract.ContractError):
            contract.validate(bad_date)
        impossible = json.loads(json.dumps(payload))
        impossible["renewals"][0]["date"] = "2026-02-30"
        with self.assertRaises(contract.ContractError):
            contract.validate(impossible)
        str_price = json.loads(json.dumps(payload))
        str_price["approved_not_bought"] = [{"id": "1", "name": "x", "price": "359000"}]
        with self.assertRaises(contract.ContractError):
            contract.validate(str_price)
        int_id = json.loads(json.dumps(payload))
        int_id["approved_not_bought"] = [{"id": 1, "name": "x", "price": 1}]
        with self.assertRaises(contract.ContractError):
            contract.validate(int_id)

    def test_rejects_days_left_out_of_window(self):
        payload = self.sample()
        payload["renewals"][0]["days_left"] = core.RENEWAL_WINDOW_DAYS + 1
        with self.assertRaises(contract.ContractError):
            contract.validate(payload)


if __name__ == "__main__":
    unittest.main()
