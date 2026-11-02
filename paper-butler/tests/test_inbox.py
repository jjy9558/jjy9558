# -*- coding: utf-8 -*-
import os
import unittest

import samples as S
from helpers import NOW, FakeLLM, TmpCase
from paperbutler.acquire import AcquireError

OLD = NOW.timestamp() - 3600


class TestInbox(TmpCase):
    def make_inbox(self):
        d = os.path.join(self.tmp, "inbox")
        os.makedirs(d)
        self.write("inbox/a_sms.txt", S.SMS_SAMSUNG, OLD)
        self.write("inbox/b_gas.txt", S.GAS, OLD)
        self.write("inbox/c_photo.jpg", b"\xff\xd8\xff\xe0fakejpeg", OLD, binary=True)
        self.write("inbox/.hidden.txt", "숨김", OLD)
        self.write("inbox/d.txt.part", "다운로드 중", OLD)
        self.write("inbox/e.xyz", "모르는 형식", OLD)
        return d

    def test_first_run_and_idempotent_second_run(self):
        b = self.butler()
        d = self.make_inbox()
        r1 = b.inbox(d, min_age=0)
        self.assertEqual(len(r1.added), 2)
        self.assertEqual(len(r1.errors), 1)                      # 이미지: 백엔드 없음
        self.assertIn("tesseract", r1.errors[0].message)
        self.assertEqual(r1.ignored, 3)
        docs, ledger = b.db.count_documents(), b.ledger("2026-11")
        r2 = b.inbox(d, min_age=0)
        self.assertEqual(len(r2.added), 0)
        self.assertEqual(r2.skipped_seen, 2)
        self.assertEqual(len(r2.errors), 1)                      # 실패한 파일은 다시 시도
        self.assertEqual(b.db.count_documents(), docs)
        self.assertEqual(b.ledger("2026-11")["total"], ledger["total"])
        self.assertEqual(len(os.listdir(os.path.join(self.home, "docs", "2026", "11"))), 2)

    def test_new_file_only_processed(self):
        b = self.butler()
        d = self.make_inbox()
        b.inbox(d, min_age=0)
        self.write("inbox/f_nh.txt", S.SMS_NH, OLD)
        r = b.inbox(d, min_age=0)
        self.assertEqual([x.name for x in r.added], ["f_nh.txt"])

    def test_same_content_other_name_is_duplicate(self):
        b = self.butler()
        d = self.make_inbox()
        b.inbox(d, min_age=0)
        self.write("inbox/copy_of_sms.txt", S.SMS_SAMSUNG, OLD)
        r = b.inbox(d, min_age=0)
        self.assertEqual(len(r.added), 0)
        self.assertEqual(len(r.duplicates), 1)
        self.assertEqual(b.ledger("2026-11")["count"], 1)

    def test_modified_file_is_new_document(self):
        b = self.butler()
        d = self.make_inbox()
        b.inbox(d, min_age=0)
        self.write("inbox/a_sms.txt", S.SMS_NH, OLD + 5)
        r = b.inbox(d, min_age=0)
        self.assertEqual(len(r.added), 1)

    def test_young_files_skipped_until_synced(self):
        b = self.butler()
        d = os.path.join(self.tmp, "inbox")
        os.makedirs(d)
        self.write("inbox/new.txt", S.SMS_SAMSUNG, NOW.timestamp() - 1)
        r = b.inbox(d, min_age=3)
        self.assertEqual((len(r.results), r.skipped_young), (0, 1))
        r = b.inbox(d, min_age=0)
        self.assertEqual(len(r.added), 1)

    def test_image_via_vision_fake_client(self):
        fake = FakeLLM(fields={}, image_text=S.SMS_SHINHAN)
        b = self.butler(llm=fake)
        d = os.path.join(self.tmp, "inbox")
        os.makedirs(d)
        self.write("inbox/shot.jpg", b"\xff\xd8\xff\xe0fakejpeg", OLD, binary=True)
        r = b.inbox(d, min_age=0)
        self.assertEqual(len(r.added), 1)
        self.assertEqual(len(fake.image_calls), 1)
        self.assertEqual(b.ledger("2026-11")["total"], 12340)
        row = b.db.get_document(r.added[0].doc_id)
        self.assertEqual(row["method"], "vision")
        self.assertTrue(row["stored_path"].endswith("shot.jpg"))

    def test_multi_message_export_file(self):
        b = self.butler()
        d = os.path.join(self.tmp, "inbox")
        os.makedirs(d)
        content = "\n\n".join([S.SMS_SAMSUNG, S.SMS_TAXI, "[Web발신]\n" + S.SMS_SHINHAN])
        self.write("inbox/sms_export.txt", content, OLD)
        r = b.inbox(d, min_age=0)
        self.assertEqual(len(r.added), 3)
        self.assertEqual(b.ledger("2026-11")["count"], 3)
        r2 = b.inbox(d, min_age=0)
        self.assertEqual((len(r2.added), r2.skipped_seen), (0, 1))
        again = b.add_file(os.path.join(d, "sms_export.txt"))
        self.assertEqual([x.status for x in again], ["duplicate"] * 3)

    def test_recursive(self):
        b = self.butler()
        d = os.path.join(self.tmp, "inbox")
        os.makedirs(d)
        self.write("inbox/sub/x.txt", S.SMS_SAMSUNG, OLD)
        self.assertEqual(len(b.inbox(d, min_age=0).added), 0)
        self.assertEqual(len(b.inbox(d, recursive=True, min_age=0).added), 1)

    def test_missing_folder(self):
        with self.assertRaises(AcquireError):
            self.butler().inbox(os.path.join(self.tmp, "nope"))

    def test_originals_left_in_inbox(self):
        b = self.butler()
        d = self.make_inbox()
        b.inbox(d, min_age=0)
        self.assertTrue(os.path.exists(os.path.join(d, "a_sms.txt")))


if __name__ == "__main__":
    unittest.main()
