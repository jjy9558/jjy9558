# -*- coding: utf-8 -*-
import contextlib
import io
import logging
import os
import sys
import unittest
from unittest import mock

import samples as S
from helpers import OLD_MTIME, TmpCase, read_bytes, read_text
from paperbutler import cli
from paperbutler.masking import MaskingFilter

RAW_PII = ("0123-4567-89", "900101-1234567", "010-1234-5678", "1234-5678-9012-3456", "110-123-456789",
           "123456-04-123456")


class CLICase(TmpCase):
    def run_cli(self, *argv, stdin=None, llm=None):
        out, err = io.StringIO(), io.StringIO()
        args = ["--home", str(self.home), "--today", "2026-11-02"] + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if stdin is not None:
                with mock.patch.object(sys, "stdin", io.StringIO(stdin)):
                    rc = cli.main(args, llm=llm)
            else:
                rc = cli.main(args, llm=llm)
        self.all_output.append(out.getvalue() + err.getvalue())
        return rc, out.getvalue(), err.getvalue()

    def setUp(self):
        super().setUp()
        self.all_output = []


class TestCLI(CLICase):
    def test_add_file_shows_summary_todo_verdict(self):
        p = self.write("gas.txt", S.GAS)
        rc, out, _ = self.run_cli("add", str(p))
        self.assertEqual(rc, 0)
        self.assertIn("[고지서·가스요금]", out)
        self.assertIn("요약: 서울도시가스 가스요금 38,900원", out)
        self.assertIn("할 일: 11/20까지 38,900원 납부 — 직접 납부 필요 (D-18)", out)
        self.assertIn("판정: 처리 필요", out)
        self.assertNotIn("7001234567", out)
        self.assertIn("*******567", out)

    def test_add_stdin_and_duplicate(self):
        rc, out, _ = self.run_cli("add", "-", stdin=S.SMS_SAMSUNG)
        self.assertEqual(rc, 0)
        self.assertIn("스타벅스 강남점 12,340원 일시불 승인", out)
        rc, out, _ = self.run_cli("add", "-", stdin=S.SMS_SAMSUNG)
        self.assertIn("이미 추가된", out)

    def test_add_empty_stdin_and_missing_file(self):
        rc, _, err = self.run_cli("add", "-", stdin="  ")
        self.assertEqual(rc, 1)
        rc, out, _ = self.run_cli("add", os.path.join(self.tmp, "none.txt"))
        self.assertEqual(rc, 1)
        self.assertIn("파일이 없습니다", out)

    def test_add_type_option(self):
        rc, out, _ = self.run_cli("add", "-", "--type", "영수증", stdin="스타벅스 4,500원")
        self.assertEqual(rc, 0)
        self.assertIn("[영수증]", out)

    def test_bad_type_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                cli.main(["add", "x", "--type", "banana"])
        self.assertEqual(cm.exception.code, 2)

    def test_due_list(self):
        self.run_cli("add", "-", stdin=S.GAS)
        self.run_cli("add", "-", stdin=S.ELECTRIC)
        rc, out, _ = self.run_cli("due", "--days", "20")
        self.assertIn("[D-18] 11/20 서울도시가스 가스요금 38,900원", out)
        self.assertNotIn("11/25", out)
        rc, out, _ = self.run_cli("due", "--days", "30")
        self.assertIn("[D-23] 11/25 한국전력공사 전기요금 45,230원 [자동이체]", out)
        rc, out, _ = self.run_cli("due", "--days", "3")
        self.assertIn("(없음)", out)

    def test_due_shows_overdue(self):
        self.run_cli("add", "-", stdin="[서울도시가스] 도시가스 요금 청구서 청구금액 10,000원 납부기한 2026.10.30")
        rc, out, _ = self.run_cli("due")
        self.assertIn("3일 지남", out)

    def test_ledger_command(self):
        for t in (S.SMS_SAMSUNG, S.SMS_HYUNDAI_CANCEL, S.SMS_NH, S.SMS_KB_CHECK):
            self.run_cli("add", "-", stdin=t)
        rc, out, _ = self.run_cli("ledger", "--month", "2026-11")
        self.assertEqual(rc, 0)
        self.assertIn("총 지출 22,500원 (2건)", out)
        self.assertIn("식비", out)
        self.assertIn("교통", out)
        self.assertIn("취소됨, 합계 제외", out)
        rc, out, _ = self.run_cli("ledger", "--month", "2026-11", "--no-detail")
        self.assertNotIn("-- 내역 --", out)
        rc, out, _ = self.run_cli("ledger")                     # 기본: 오늘이 속한 달
        self.assertIn("== 2026-11 가계부 ==", out)
        rc, _, err = self.run_cli("ledger", "--month", "2026/11")
        self.assertEqual(rc, 2)

    def test_export_csv(self):
        self.run_cli("add", "-", stdin=S.SMS_SAMSUNG)
        out_path = os.path.join(self.tmp, "x.csv")
        rc, out, _ = self.run_cli("export-csv", "-o", out_path)
        self.assertEqual(rc, 0)
        self.assertTrue(read_bytes(out_path).startswith(b"\xef\xbb\xbf"))
        self.assertIn("스타벅스 강남점", read_text(out_path, "utf-8-sig"))
        rc, out, _ = self.run_cli("export-csv", "--what", "docs", "-o", out_path)
        self.assertIn("1행", out)
        rc, out, _ = self.run_cli("export-csv", "--month", "2026-11")
        self.assertTrue(os.path.exists(os.path.join(str(self.home), "export-ledger-2026-11.csv")))

    def test_ics_command(self):
        self.run_cli("add", "-", stdin=S.GAS)
        p = os.path.join(self.tmp, "o.ics")
        rc, out, _ = self.run_cli("ics", "-o", p)
        self.assertEqual(rc, 0)
        self.assertIn("일정 1개", out)
        data = read_bytes(p)
        self.assertTrue(data.startswith(b"BEGIN:VCALENDAR\r\n"))
        self.assertIn(b"TRIGGER:-P3D", data)

    def test_find(self):
        self.run_cli("add", "-", stdin=S.GAS)
        rc, out, _ = self.run_cli("find", "가스")
        self.assertIn("검색 결과 1건", out)
        rc, out, _ = self.run_cli("find", "없는말")
        self.assertIn("검색 결과가 없습니다", out)

    def test_show_and_done(self):
        self.run_cli("add", "-", stdin=S.CAR_TAX)
        rc, out, _ = self.run_cli("show", "1")
        self.assertIn("납부기한: 2026-12-31", out)
        self.assertIn("판정: 처리 필요", out)
        rc, out, _ = self.run_cli("done", "1")
        self.assertIn("납부/처리 완료", out)
        rc, out, _ = self.run_cli("show", "1")
        self.assertIn("판정: 보관 5년", out)
        rc, out, _ = self.run_cli("due", "--days", "90")
        self.assertIn("(없음)", out)
        rc, out, _ = self.run_cli("done", "1", "--undo")
        rc, out, _ = self.run_cli("due", "--days", "90")
        self.assertIn("12/31", out)
        rc, _, err = self.run_cli("show", "99")
        self.assertEqual(rc, 1)

    def test_refunds_command(self):
        self.run_cli("add", "-", stdin=S.RECEIPT_ONLINE)
        rc, out, _ = self.run_cli("refunds")
        self.assertIn("[D-7] 11/9 쿠팡 29,900원", out)
        self.assertIn("법률 자문이 아닙니다", out)

    def test_inbox_command_idempotent(self):
        d = os.path.join(self.tmp, "inbox")
        os.makedirs(d)
        self.write("inbox/a.txt", S.SMS_SAMSUNG, 1_000_000)
        self.write("inbox/b.txt", S.GAS, 1_000_000)
        rc, out, _ = self.run_cli("inbox", d)
        self.assertEqual(rc, 0)
        self.assertIn("새로 추가 2", out)
        rc, out, _ = self.run_cli("inbox", d)
        self.assertIn("새로 추가 0", out)
        self.assertIn("이미 처리한 파일 2", out)
        rc, _, err = self.run_cli("inbox", os.path.join(self.tmp, "nope"))
        self.assertEqual(rc, 1)

    def test_doctor(self):
        rc, out, _ = self.run_cli("doctor")
        self.assertEqual(rc, 0)
        self.assertIn("pdftotext:", out)
        self.assertIn("LLM/VLM:", out)

    def test_no_command_prints_help(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([]), 2)

    def test_options_after_subcommand(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["ledger", "--home", str(self.home), "--today", "2026-11-02", "--month", "2026-11"])
        self.assertEqual(rc, 0)
        self.assertIn("2026-11", out.getvalue())


class TestRedactOnly(CLICase):
    def test_redact_only_prints_masked_and_stores_nothing(self):
        p = self.write("pii.txt", S.PII)
        rc, out, _ = self.run_cli("add", str(p), "--redact-only")
        self.assertEqual(rc, 0)
        for raw in RAW_PII:
            self.assertNotIn(raw, out)
        self.assertIn("[개인정보 마스킹 사본", out)
        self.assertIn("010-****-5678", out)
        self.assertFalse(os.path.exists(os.path.join(str(self.home), "docs")))

    def test_redact_only_output_file_and_stdin(self):
        dest = os.path.join(self.tmp, "masked.txt")
        rc, out, _ = self.run_cli("add", "-", "--redact-only", "-o", dest, stdin=S.PII)
        self.assertEqual(rc, 0)
        text = read_text(dest)
        for raw in RAW_PII:
            self.assertNotIn(raw, text)
        self.assertIn("1588-1234", text)

    def test_redact_only_image_warns_pixels(self):
        fake = mock.Mock()
        fake.transcribe_image.return_value = S.PII
        p = self.write("a.jpg", b"\xff\xd8", binary=True)
        dest = os.path.join(self.tmp, "m.txt")
        rc, out, _ = self.run_cli("add", str(p), "--redact-only", "-o", dest, llm=fake)
        self.assertEqual(rc, 0)
        self.assertIn("픽셀", out)
        self.assertNotIn("900101", read_text(dest))

    def test_redact_only_error(self):
        rc, out, err = self.run_cli("add", os.path.join(self.tmp, "none.txt"), "--redact-only")
        self.assertEqual(rc, 1)


class TestPrivacyOfOutputsAndLogs(CLICase):
    def test_no_command_leaks_raw_pii(self):
        text = S.ELECTRIC + "\n" + S.PII
        self.run_cli("add", "-", stdin=text)
        p = os.path.join(self.tmp, "o.ics")
        self.run_cli("due", "--days", "60")
        self.run_cli("show", "1")
        self.run_cli("find", "고객번호")
        self.run_cli("find", "환불계좌")
        self.run_cli("ics", "-o", p)
        self.run_cli("export-csv", "--what", "docs", "-o", os.path.join(self.tmp, "d.csv"))
        blob = "\n".join(self.all_output)
        blob += read_text(p) + read_text(os.path.join(self.tmp, "d.csv"), "utf-8-sig")
        for raw in RAW_PII:
            self.assertNotIn(raw, blob, raw)
        log = os.path.join(str(self.home), "paperbutler.log")
        self.assertTrue(os.path.exists(log))
        for raw in RAW_PII:
            self.assertNotIn(raw, read_text(log))

    def test_log_file_masks_pii(self):
        with contextlib.redirect_stderr(io.StringIO()) as captured:
            handlers = cli._setup_logging(str(self.home), False)
        try:
            with contextlib.redirect_stderr(captured):
                logging.getLogger("paperbutler.test").warning(
                    "오류: 카드 %s 전화 %s", "1234-5678-9012-3456", "010-1234-5678")
            self.assertNotIn("9012-3456", captured.getvalue())
        finally:
            cli._teardown_logging(handlers)
        text = read_text(os.path.join(str(self.home), "paperbutler.log"))
        self.assertIn("오류", text)
        self.assertNotIn("9012-3456", text)
        self.assertNotIn("1234-5678", text.replace("5678-9012", ""))
        self.assertIn("010-****-5678", text)

    def test_logging_handlers_removed_after_main(self):
        before = list(logging.getLogger("paperbutler").handlers)
        self.run_cli("doctor")
        self.assertEqual(logging.getLogger("paperbutler").handlers, before)

    def test_emit_masks_everything(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.emit("연락처 010-1234-5678")
        self.assertEqual(buf.getvalue().strip(), "연락처 010-****-5678")


if __name__ == "__main__":
    unittest.main()
