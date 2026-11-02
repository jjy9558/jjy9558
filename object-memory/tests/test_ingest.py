import os
import unittest

import helpers
from helpers import Clock, FakeVlm, at, jpeg, make_app, obj
from objmem import ask as askmod
from objmem import config as cfgmod
from objmem import core
from objmem.store import Store, fts5_available


class HashTest(unittest.TestCase):
    def test_hamming_and_parse(self):
        self.assertEqual(core.hamming(0, 0), 0)
        self.assertEqual(core.hamming(0b1011, 0b0001), 2)
        self.assertEqual(core.parse_hash("00ff00ff00ff00ff"), 0x00ff00ff00ff00ff)
        for bad in ("", "xyz", "00ff", "g" * 16, None, "0" * 17):
            self.assertIsNone(core.parse_hash(bad))

    def test_is_jpeg(self):
        self.assertTrue(core.is_jpeg(jpeg()))
        self.assertFalse(core.is_jpeg(b"\x89PNG\r\n"))
        self.assertFalse(core.is_jpeg(b"\xff\xd8abc"))
        self.assertFalse(core.is_jpeg(b""))


class DedupeTest(unittest.TestCase, helpers.TmpCase):
    def setUp(self):
        self.vlm = FakeVlm()
        self.app = make_app(self.make_tmp(), vlm=self.vlm, hash_threshold=4)

    def test_similar_hash_skipped_before_vlm(self):
        base = 0x0F0F0F0F0F0F0F0F
        r1 = self.app.ingest_bytes(jpeg(), "거실", "%016x" % base)
        self.assertEqual(r1["status"], "stored")
        self.assertEqual(self.vlm.calls, 1)
        r2 = self.app.ingest_bytes(jpeg(), "거실", "%016x" % (base ^ 0b1011))   # 거리 3
        self.assertEqual((r2["status"], r2["distance"]), ("similar", 3))
        self.assertEqual(self.vlm.calls, 1)                 # VLM 호출 없음
        self.assertEqual(self.app.store.stats()["frames"], 1)

    def test_threshold_boundary(self):
        base = 0
        self.app.ingest_bytes(jpeg(), "거실", "%016x" % base)
        self.assertEqual(self.app.ingest_bytes(jpeg(), "거실", "%016x" % 0b1111)["status"], "similar")      # 4 == 임계
        r = self.app.ingest_bytes(jpeg(), "거실", "%016x" % 0b11111)                                         # 5
        self.assertEqual(r["status"], "stored")

    def test_compares_to_last_stored_same_camera_only(self):
        self.app.ingest_bytes(jpeg(), "거실", "%016x" % 0)
        self.assertEqual(self.app.ingest_bytes(jpeg(), "책상", "%016x" % 0)["status"], "stored")   # 다른 카메라
        self.assertEqual(self.app.ingest_bytes(jpeg(), "책상", "%016x" % 1)["status"], "similar")
        # 느린 변화는 마지막 '저장된' 프레임과 비교하므로 결국 저장된다
        self.assertEqual(self.app.ingest_bytes(jpeg(), "거실", "%016x" % 0b111)["status"], "similar")
        self.assertEqual(self.app.ingest_bytes(jpeg(), "거실", "%016x" % 0b1111111)["status"], "stored")

    def test_no_hash_still_dedupes_exact_bytes(self):
        data = jpeg()
        self.assertEqual(self.app.ingest_bytes(data, "거실")["status"], "stored")
        r = self.app.ingest_bytes(data, "거실")
        self.assertEqual((r["status"], r["reason"]), ("duplicate", "sha256"))

    def test_bad_hash_ignored(self):
        self.app.ingest_bytes(jpeg(), "거실", "%016x" % 0)
        self.assertEqual(self.app.ingest_bytes(jpeg(), "거실", "not-a-hash")["status"], "stored")

    def test_rejects_non_jpeg_and_oversize(self):
        with self.assertRaises(core.FrameError):
            self.app.ingest_bytes(b"hello world", "x")
        app = make_app(self.make_tmp(), max_frame_bytes=50)
        with self.assertRaises(core.FrameError):
            app.ingest_bytes(b"\xff\xd8" + b"a" * 100 + b"\xff\xd9", "x")

    def test_camera_label_sanitized_and_not_in_path(self):
        r = self.app.ingest_bytes(jpeg(), "../../etc\n거실" + "x" * 100)
        row = self.app.store.frame(r["frame_id"])
        self.assertTrue(row["path"].startswith(self.app.photo_dir))
        self.assertNotIn("\n", row["camera"])
        self.assertLessEqual(len(row["camera"]), 40)


class DescribeCacheTest(unittest.TestCase, helpers.TmpCase):
    def test_vlm_down_then_describe_pending_and_cache(self):
        vlm = FakeVlm([{"objects": [obj("리모컨", "소파 위")], "scene": ""}])
        app = make_app(self.make_tmp(), vlm=vlm)
        vlm.fail = True
        data = jpeg()
        r = app.ingest_bytes(data, "거실")
        self.assertEqual((r["status"], r["described"]), ("stored", False))
        self.assertFalse(askmod.answer(app, "리모컨 어디")["found"])
        vlm.fail = False
        self.assertEqual(app.describe_pending(), {"described": 1, "failed": 0})
        self.assertTrue(askmod.answer(app, "리모컨 어디")["found"])
        calls = vlm.calls
        # 캐시: 같은 sha256 을 다시 설명해도 VLM 을 부르지 않음
        fid = r["frame_id"]
        self.assertTrue(app.describe_frame(fid)["cached"])
        self.assertEqual(vlm.calls, calls)
        self.assertEqual(app.store.stats()["sightings"], 1)       # 재설명해도 중복 색인 없음


class PrivacyTest(unittest.TestCase, helpers.TmpCase):
    def test_range_logic(self):
        r = ["23:00-07:00"]
        self.assertTrue(cfgmod.in_privacy(r, 23 * 60))
        self.assertTrue(cfgmod.in_privacy(r, 3 * 60))
        self.assertTrue(cfgmod.in_privacy(r, 6 * 60 + 59))
        self.assertFalse(cfgmod.in_privacy(r, 7 * 60))
        self.assertFalse(cfgmod.in_privacy(r, 12 * 60))
        self.assertFalse(cfgmod.in_privacy(r, 22 * 60 + 59))
        self.assertTrue(cfgmod.in_privacy(["09:00-18:00"], 9 * 60))
        self.assertFalse(cfgmod.in_privacy(["09:00-18:00"], 18 * 60))
        self.assertFalse(cfgmod.in_privacy([], 0))
        with self.assertRaises(ValueError):
            cfgmod.parse_ranges(["abc"])

    def test_nothing_stored_nor_sent_during_privacy_hours(self):
        vlm = FakeVlm()
        clock = Clock(at(2026, 11, 2, 23, 30))
        app = make_app(self.make_tmp(), vlm=vlm, clock=clock, privacy_hours=["23:00-07:00"])
        r = app.ingest_bytes(jpeg(), "거실", "%016x" % 5)
        self.assertEqual(r["status"], "privacy")
        self.assertEqual(vlm.calls, 0)
        self.assertEqual(app.store.stats()["frames"], 0)
        photos = [f for _, _, fs in os.walk(app.photo_dir) for f in fs]
        self.assertEqual(photos, [])
        clock.t = at(2026, 11, 3, 7, 1)
        self.assertEqual(app.ingest_bytes(jpeg(), "거실")["status"], "stored")

    def test_invalid_privacy_config_fails_early(self):
        with self.assertRaises(ValueError):
            make_app(self.make_tmp(), privacy_hours=["밤"])

    def test_folder_ingest_respects_privacy_by_photo_time(self):
        tmp = self.make_tmp()
        src = os.path.join(tmp, "sync")
        os.makedirs(src)
        for name in ("IMG_20261102_233000.jpg", "IMG_20261102_120000.jpg"):
            with open(os.path.join(src, name), "wb") as f:
                f.write(jpeg())
        with open(os.path.join(src, "notes.txt"), "w") as f:
            f.write("x")
        with open(os.path.join(src, "fake.jpg"), "wb") as f:
            f.write(b"not a jpeg")
        app = make_app(tmp, privacy_hours=["23:00-07:00"])
        res = app.ingest_folder(src, "거실")
        self.assertEqual((res["stored"], res["privacy"], res["skipped"]), (1, 1, 1))
        self.assertEqual(app.store.stats()["frames"], 1)

    def test_filename_timestamp(self):
        self.assertEqual(core.ts_from_name("PXL_20261102_214030123.jpg", 0), at(2026, 11, 2, 21, 40) + 30)
        self.assertEqual(core.ts_from_name("photo.jpg", 42), 42)


class RetentionTest(unittest.TestCase, helpers.TmpCase):
    def test_purge_with_fake_clock(self):
        for fts in (True, False):
            clock = Clock(at(2026, 9, 1, 12, 0))
            vlm = FakeVlm()
            app = make_app(self.make_tmp(), vlm=vlm, clock=clock, photo_days=7, desc_days=90, use_fts=fts)
            vlm.results = [{"objects": [obj("리모컨", "소파", 0.9)], "scene": ""},
                           {"objects": [obj("리모컨", "탁자", 0.9)], "scene": ""}]
            r1 = app.ingest_bytes(jpeg(), "거실")
            clock.t = at(2026, 10, 25, 12, 0)                 # 54일 뒤
            r2 = app.ingest_bytes(jpeg(), "거실")
            p1, p2 = r1["path"], r2["path"]
            clock.t = at(2026, 10, 26, 12, 0)
            self.assertEqual(app.purge(), {"photos_deleted": 1, "descriptions_deleted": 0})
            self.assertFalse(os.path.exists(p1))
            self.assertTrue(os.path.exists(p2))
            # 사진은 없지만 설명은 남아서 질문에 답할 수 있다
            a = askmod.answer(app, "리모컨 어디 뒀지")
            self.assertEqual(a["sightings"][0]["where"], "탁자")
            self.assertEqual(len(a["sightings"]), 2)
            self.assertIn("보관 기간이 지나 삭제", a["answer"])
            self.assertIsNone(a["sightings"][1]["photo"])
            clock.t = at(2026, 12, 5, 12, 0)                  # r1 은 95일, r2 는 41일
            res = app.purge()
            self.assertEqual(res["descriptions_deleted"], 1)
            a = askmod.answer(app, "리모컨 어디 뒀지")
            self.assertEqual([s["where"] for s in a["sightings"]], ["탁자"])
            self.assertEqual(app.store.stats()["frames"], 1)
            self.assertEqual(app.store.stats()["sightings"], 1)
            clock.t = at(2027, 3, 1)
            app.purge()
            self.assertEqual(app.store.stats()["frames"], 0)
            self.assertFalse(askmod.answer(app, "리모컨 어디 뒀지")["found"])
            leftover = [f for _, _, fs in os.walk(app.photo_dir) for f in fs]
            self.assertEqual(leftover, [])
            self.assertEqual(os.listdir(app.photo_dir), [])   # 빈 날짜 폴더도 정리


class IndexTest(unittest.TestCase, helpers.TmpCase):
    def test_fts_detection_and_fallback_match_same_results(self):
        self.assertIsInstance(fts5_available(), bool)
        results = []
        for fts in (True, False):
            app = make_app(self.make_tmp(), use_fts=fts)
            app.vlm.results = [{"objects": [obj("택배 상자", "현관 앞"), obj("충전기", "침대 옆 협탁"), obj("키", "현관 선반")], "scene": ""}]
            app.ingest_bytes(jpeg(), "현관")
            self.assertEqual(app.store.use_fts, fts and fts5_available())
            results.append([sorted(s["name"] for s in askmod.answer(app, q)["sightings"])
                            for q in ("상자 어디", "열쇠 어디", "충전 어디", "협탁 어디", "선반 어디")])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], ["택배 상자"])
        self.assertEqual(results[0][1], ["키"])


if __name__ == "__main__":
    unittest.main()
