import unittest

from sellstudio import jpegmeta
from tests.helpers import make_jpeg


class ExifTests(unittest.TestCase):
    def test_gps_parsed(self):
        info = jpegmeta.parse_exif(make_jpeg(gps=True))
        self.assertTrue(info.has_gps)
        self.assertAlmostEqual(info.lat, 37.5, places=4)
        self.assertAlmostEqual(info.lon, 127.041667, places=4)
        self.assertEqual(info.model, "Model X")
        self.assertEqual(info.orientation, 6)
        self.assertEqual(info.datetime, "2026:01:02 03:04:05")

    def test_no_gps(self):
        info = jpegmeta.parse_exif(make_jpeg(gps=False))
        self.assertTrue(info.has_exif)
        self.assertFalse(info.has_gps)

    def test_no_exif(self):
        info = jpegmeta.parse_exif(make_jpeg(exif=False))
        self.assertFalse(info.has_exif)
        self.assertFalse(info.has_gps)

    def test_xmp_gps_detected(self):
        self.assertTrue(jpegmeta.parse_exif(make_jpeg(exif=False, xmp_gps=True)).has_gps)

    def test_png_not_supported(self):
        info = jpegmeta.parse_exif(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
        self.assertFalse(info.supported)
        self.assertTrue(info.notes)

    def test_truncated_does_not_crash(self):
        raw = make_jpeg()
        for cut in (2, 5, 20, 60, 100):
            info = jpegmeta.parse_exif(raw[:cut])
            self.assertIsInstance(info.has_gps, bool)

    def test_garbage_tiff_does_not_crash(self):
        raw = bytearray(make_jpeg())
        i = raw.index(b"II*\x00")
        raw[i + 4:i + 8] = b"\xff\xff\xff\x7f"  # IFD0 오프셋을 범위 밖으로
        info = jpegmeta.parse_exif(bytes(raw))
        self.assertFalse(info.has_gps)

    def test_image_size(self):
        self.assertEqual(jpegmeta.image_size(make_jpeg(width=640, height=480)), (640, 480))


class StripTests(unittest.TestCase):
    def test_strip_removes_gps_and_keeps_orientation(self):
        raw = make_jpeg(gps=True, orientation=6)
        clean = jpegmeta.strip_app1(raw)
        info = jpegmeta.parse_exif(clean)
        self.assertFalse(info.has_gps)
        self.assertEqual(info.model, "")
        self.assertEqual(info.datetime, "")
        self.assertEqual(info.orientation, 6)
        self.assertNotIn(b"TestCam", clean)
        self.assertEqual(jpegmeta.image_size(clean), (64, 48))
        self.assertTrue(clean.endswith(b"\x11\x22\x33\xff\xd9"))  # 이미지 데이터는 그대로

    def test_strip_drop_orientation(self):
        clean = jpegmeta.strip_app1(make_jpeg(), keep_orientation=False)
        info = jpegmeta.parse_exif(clean)
        self.assertFalse(info.has_exif)
        self.assertNotIn(b"Exif", clean)

    def test_strip_xmp(self):
        clean = jpegmeta.strip_app1(make_jpeg(exif=False, xmp_gps=True))
        self.assertFalse(jpegmeta.parse_exif(clean).has_gps)
        self.assertNotIn(b"GPSLatitude", clean)

    def test_input_bytes_not_modified(self):
        raw = make_jpeg()
        before = bytes(raw)
        jpegmeta.strip_app1(raw)
        self.assertEqual(raw, before)

    def test_orientation_one_not_kept(self):
        clean = jpegmeta.strip_app1(make_jpeg(orientation=1))
        self.assertFalse(jpegmeta.parse_exif(clean).has_exif)

    def test_non_jpeg_rejected(self):
        with self.assertRaises(jpegmeta.JpegError):
            jpegmeta.strip_app1(b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
