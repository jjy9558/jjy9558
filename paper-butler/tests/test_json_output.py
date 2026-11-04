# -*- coding: utf-8 -*-
"""기계용 JSON 출력: due --json, refunds --json (스키마 v1)."""
import contextlib
import datetime as dt
import io
import json
import unittest

import samples as S
from helpers import TmpCase
from paperbutler import cli, contract
from paperbutler.masking import mask_text

TODAY = "2026-11-04"
RAW_PHONE = "010-1234-5678"
RAW_ACCT = "123456-04-123456"
RAW_CUST = "0123-4567-89"


class JsonCase(TmpCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        args = ["--home", str(self.home), "--today", TODAY] + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(args)
        return rc, out.getvalue(), err.getvalue()

    def run_json(self, *argv):
        rc, out, _ = self.run_cli(*argv)
        self.assertEqual(rc, 0)
        obj = json.loads(out)          # stdout 에는 JSON 외에 아무것도 없어야 한다
        contract.validate(obj)
        return obj

    def add(self, text):
        b = self.butler(today=dt.date(2026, 11, 4))
        return b.add_text(text, "t.txt")


class TestSchema(JsonCase):
    def test_version_constant(self):
        self.assertEqual(contract.SCHEMA_VERSION, 1)

    def test_top_level_and_item_shape(self):
        self.add(S.GAS)
        self.add(S.RECEIPT_ONLINE)
        obj = self.run_json("due", "--days", "30", "--json")
        self.assertEqual(set(obj), {"version", "generated_for", "items"})
        self.assertEqual(obj["version"], 1)
        self.assertEqual(obj["generated_for"], TODAY)
        self.assertEqual(len(obj["items"]), 2)
        keys = {"kind", "title", "merchant", "amount", "due", "days_left", "auto_debit", "doc_id"}
        for it in obj["items"]:
            self.assertEqual(set(it), keys)
            self.assertIn(it["kind"], ("bill", "refund"))
            self.assertIsInstance(it["title"], str)
            self.assertIsInstance(it["merchant"], str)
            self.assertIsInstance(it["amount"], int)
            self.assertRegex(it["due"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertIsInstance(it["days_left"], int)
            self.assertIsInstance(it["auto_debit"], bool)
            self.assertIsInstance(it["doc_id"], int)

    def test_bill_and_refund_values(self):
        self.add(S.GAS)
        self.add(S.RECEIPT_ONLINE)
        obj = self.run_json("due", "--days", "30", "--json")
        refund, bill = obj["items"]
        self.assertEqual(refund["kind"], "refund")
        self.assertEqual((refund["due"], refund["days_left"], refund["amount"]), ("2026-11-09", 5, 29900))
        self.assertEqual(refund["merchant"], "쿠팡")
        self.assertEqual(bill["kind"], "bill")
        self.assertEqual((bill["due"], bill["days_left"], bill["amount"]), ("2026-11-20", 16, 38900))
        self.assertEqual(bill["title"], "가스요금")
        self.assertIs(bill["auto_debit"], False)

    def test_auto_debit_flag(self):
        self.add(S.ELECTRIC)
        obj = self.run_json("due", "--days", "30", "--json")
        self.assertEqual(len(obj["items"]), 1)
        self.assertIs(obj["items"][0]["auto_debit"], True)

    def test_validate_accepts_valid_payload(self):
        obj = {"version": 1, "generated_for": "2026-11-04", "items": [
            {"kind": "bill", "title": "t", "merchant": "m", "amount": None, "due": "2026-11-04",
             "days_left": 0, "auto_debit": False, "doc_id": 1}]}
        self.assertIs(contract.validate(obj), obj)

    def test_validate_rejects_violations(self):
        good = {"kind": "bill", "title": "t", "merchant": "m", "amount": 100, "due": "2026-11-10",
                "days_left": 6, "auto_debit": True, "doc_id": 1}

        def payload(**over):
            item = dict(good)
            item.update(over)
            return {"version": 1, "generated_for": "2026-11-04", "items": [item]}

        bad_cases = [
            {"version": 2, "generated_for": "2026-11-04", "items": []},
            {"generated_for": "2026-11-04", "items": []},
            {"version": 1, "generated_for": "2026-11-04", "items": [], "extra": 1},
            {"version": 1, "generated_for": "2026/11/04", "items": []},
            {"version": 1, "generated_for": "2026-13-01", "items": []},
            payload(kind="other"),
            payload(amount="100"),
            payload(amount=True),
            payload(due="2026-11-4"),
            payload(days_left=7),
            payload(days_left=True),
            payload(auto_debit=1),
            payload(doc_id="1"),
            payload(title=None),
            dict(version=1, generated_for="2026-11-04", items=[dict(good, due="2026-11-09", days_left=5),
                                                              dict(good, due="2026-11-05", days_left=1)]),
        ]
        for case in bad_cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    contract.validate(case)
        with self.assertRaises(ValueError):
            contract.validate(["not", "a", "dict"])


class TestSorting(JsonCase):
    def test_sorted_by_due_across_kinds(self):
        self.add(S.CAR_TAX)          # 12-31
        self.add(S.ELECTRIC)         # 11-25
        self.add(S.GAS)              # 11-20
        self.add(S.RECEIPT_ONLINE)   # refund 11-09
        obj = self.run_json("due", "--days", "60", "--json")
        dues = [it["due"] for it in obj["items"]]
        self.assertEqual(dues, sorted(dues))
        self.assertEqual(dues, ["2026-11-09", "2026-11-20", "2026-11-25", "2026-12-31"])
        self.assertEqual([it["kind"] for it in obj["items"]], ["refund", "bill", "bill", "bill"])

    def test_same_due_date_is_deterministic(self):
        self.add(S.GAS)
        self.add(S.GAS.replace("38,900", "11,000").replace("고객번호 : 7001234567", "고객번호 : 7009999999"))
        obj = self.run_json("due", "--days", "30", "--json")
        ids = [it["doc_id"] for it in obj["items"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), 2)


class TestWindowAndEmpty(JsonCase):
    def test_days_option_limits_window(self):
        self.add(S.GAS)              # 11-20 = D-16
        self.add(S.RECEIPT_ONLINE)   # refund 11-09 = D-5
        short = self.run_json("due", "--json")                 # 기본 14일
        self.assertEqual([it["kind"] for it in short["items"]], ["refund"])
        longer = self.run_json("due", "--days", "20", "--json")
        self.assertEqual([it["kind"] for it in longer["items"]], ["refund", "bill"])
        none = self.run_json("due", "--days", "3", "--json")
        self.assertEqual(none["items"], [])

    def test_overdue_items_follow_overdue_days(self):
        self.add("[서울도시가스] 도시가스 요금 청구서 청구금액 10,000원 납부기한 2026.10.30")
        obj = self.run_json("due", "--json")
        self.assertEqual(len(obj["items"]), 1)
        self.assertEqual(obj["items"][0]["days_left"], -5)
        self.assertEqual(self.run_json("due", "--json", "--overdue-days", "3")["items"], [])

    def test_empty_list(self):
        obj = self.run_json("due", "--json")
        self.assertEqual(obj, {"version": 1, "generated_for": TODAY, "items": []})
        obj = self.run_json("refunds", "--json")
        self.assertEqual(obj, {"version": 1, "generated_for": TODAY, "items": []})

    def test_done_items_are_excluded(self):
        self.add(S.GAS)
        self.run_cli("done", "1")
        self.assertEqual(self.run_json("due", "--days", "30", "--json")["items"], [])


class TestRefundsJson(JsonCase):
    def test_refunds_only_with_refund_kind(self):
        self.add(S.GAS)
        self.add(S.RECEIPT_ONLINE)
        obj = self.run_json("refunds", "--json")
        self.assertEqual(len(obj["items"]), 1)
        it = obj["items"][0]
        self.assertEqual(it["kind"], "refund")
        self.assertEqual((it["due"], it["days_left"], it["doc_id"]), ("2026-11-09", 5, 2))

    def test_days_option(self):
        self.add(S.RECEIPT_ONLINE)
        self.assertEqual(len(self.run_json("refunds", "--days", "7", "--json")["items"]), 1)
        self.assertEqual(self.run_json("refunds", "--days", "3", "--json")["items"], [])

    def test_refunds_do_not_include_overdue(self):
        self.add(S.RECEIPT_ONLINE)
        self.assertEqual(self.run_json("refunds", "--days", "30", "--json")["items"][0]["days_left"], 5)
        self.run_cli("done", "1")
        self.assertEqual(self.run_json("refunds", "--json")["items"], [])


class TestMasking(JsonCase):
    def test_masked_like_human_output(self):
        b = self.butler(today=dt.date(2026, 11, 4))
        r = b.add_text(S.GAS, "t.txt")[0]
        # 기관명에 개인번호가 섞인 경우를 흉내낸다(DB 에는 원본이 들어 있을 수 있음)
        raw_issuer = "서울도시가스 %s 계좌 %s" % (RAW_PHONE, RAW_ACCT)
        b.db.update_document(r.doc_id, issuer=raw_issuer)
        b.close()
        masked = mask_text(raw_issuer)
        self.assertNotEqual(masked, raw_issuer)

        rc, human, _ = self.run_cli("due", "--days", "30")
        self.assertIn(masked, human)
        obj = self.run_json("due", "--days", "30", "--json")
        self.assertEqual(obj["items"][0]["merchant"], masked)
        for raw in (RAW_PHONE, RAW_ACCT):
            self.assertNotIn(raw, json.dumps(obj, ensure_ascii=False))

    def test_no_raw_pii_in_any_json(self):
        self.add(S.ELECTRIC)
        self.add(S.RECEIPT_ONLINE)
        rc, out1, _ = self.run_cli("due", "--days", "60", "--json")
        rc, out2, _ = self.run_cli("refunds", "--days", "60", "--json")
        blob = out1 + out2
        self.assertEqual(rc, 0)
        self.assertNotIn(RAW_CUST, blob)
        self.assertNotIn("0123-4567", blob)

    def test_customer_number_not_in_items(self):
        self.add(S.ELECTRIC)
        blob = json.dumps(self.run_json("due", "--days", "60", "--json"), ensure_ascii=False)
        self.assertNotIn(RAW_CUST, blob)
        self.assertNotIn("7001234567", blob)


class TestStdoutEncoding(JsonCase):
    def test_utf8_bytes_on_stdout_buffer(self):
        self.add(S.GAS)
        raw = io.BytesIO()
        wrapper = io.TextIOWrapper(raw, encoding="utf-8", write_through=True)
        err = io.StringIO()
        args = ["--home", str(self.home), "--today", TODAY, "due", "--days", "30", "--json"]
        with contextlib.redirect_stdout(wrapper), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(args), 0)
        wrapper.flush()
        obj = json.loads(raw.getvalue().decode("utf-8"))
        self.assertEqual(obj["items"][0]["title"], "가스요금")


if __name__ == "__main__":
    unittest.main()
