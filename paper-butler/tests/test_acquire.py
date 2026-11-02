# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
import unittest

from helpers import FakeLLM, TmpCase
from paperbutler.acquire import (
    AcquireError, acquire_text, decode_bytes, image_text, pdf_text, tesseract_text,
)
from paperbutler.config import Config


class Runner:
    def __init__(self, stdout=b"", stderr=b"", rc=0, exc=None):
        self.stdout, self.stderr, self.rc, self.exc = stdout, stderr, rc, exc
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(args, self.rc, self.stdout, self.stderr)


def which_all(name):
    return "/usr/bin/" + name


def which_none(name):
    return None


class TestDecode(unittest.TestCase):
    def test_encodings(self):
        s = "한국전력 청구금액 12,340원"
        self.assertEqual(decode_bytes(s.encode("utf-8")), s)
        self.assertEqual(decode_bytes(b"\xef\xbb\xbf" + s.encode("utf-8")), s)
        self.assertEqual(decode_bytes(s.encode("cp949")), s)
        self.assertEqual(decode_bytes(s.encode("utf-16")), s)


class TestAcquire(TmpCase):
    def cfg(self, **kw):
        return Config(home=self.home, **kw)

    def test_text_file(self):
        p = self.write("a.txt", "안녕 12,340원")
        a = acquire_text(str(p), self.cfg())
        self.assertEqual((a.text, a.method), ("안녕 12,340원", "text"))

    def test_cp949_text_file(self):
        p = self.write("a.txt", "한국전력 청구금액 12,340원".encode("cp949"), binary=True)
        self.assertIn("한국전력", acquire_text(str(p), self.cfg()).text)

    def test_empty_and_missing_and_unsupported(self):
        for name, content in (("e.txt", "  \n"),):
            p = self.write(name, content)
            with self.assertRaises(AcquireError):
                acquire_text(str(p), self.cfg())
        with self.assertRaises(AcquireError) as cm:
            acquire_text(os.path.join(self.tmp, "nope.txt"), self.cfg())
        self.assertIn("파일이 없습니다", str(cm.exception))
        p = self.write("a.docx", b"PK", binary=True)
        with self.assertRaises(AcquireError) as cm:
            acquire_text(str(p), self.cfg())
        self.assertIn("지원하지 않는", str(cm.exception))

    def test_heic_message(self):
        p = self.write("a.heic", b"x", binary=True)
        with self.assertRaises(AcquireError) as cm:
            acquire_text(str(p), self.cfg())
        self.assertIn("JPEG", str(cm.exception))

    def test_pdf_missing_tool_message(self):
        p = self.write("a.pdf", b"%PDF-1.4", binary=True)
        with self.assertRaises(AcquireError) as cm:
            pdf_text(str(p), self.cfg(), which_none)
        msg = str(cm.exception)
        self.assertIn("pdftotext", msg)
        self.assertIn("poppler", msg)

    def test_pdf_uses_arg_list_not_shell(self):
        p = self.write("a b;rm -rf.pdf", b"%PDF-1.4", binary=True)
        r = Runner(stdout="고지서 청구금액 12,340원".encode("utf-8"))
        a = acquire_text(str(p), self.cfg(), which=which_all, runner=r)
        self.assertEqual(a.method, "pdftotext")
        args, kw = r.calls[0]
        self.assertIsInstance(args, list)
        self.assertEqual(args[0], "/usr/bin/pdftotext")
        self.assertEqual(args[-2], os.path.abspath(str(p)))
        self.assertNotIn("shell", kw)

    def test_pdf_errors(self):
        p = self.write("a.pdf", b"%PDF", binary=True)
        with self.assertRaises(AcquireError) as cm:
            pdf_text(str(p), self.cfg(), which_all, Runner(rc=1))
        self.assertIn("손상", str(cm.exception))
        with self.assertRaises(AcquireError) as cm:
            pdf_text(str(p), self.cfg(), which_all, Runner(stdout=b"  \n"))
        self.assertIn("스캔", str(cm.exception))
        with self.assertRaises(AcquireError):
            pdf_text(str(p), self.cfg(), which_all, Runner(exc=subprocess.TimeoutExpired("x", 1)))

    def test_tesseract_args_and_errors(self):
        p = self.write("a.png", b"\x89PNG", binary=True)
        r = Runner(stdout="영수증 합계 4,500원".encode("utf-8"))
        a = image_text(str(p), self.cfg(), None, which_all, r)
        self.assertEqual(a.method, "tesseract")
        args = r.calls[0][0]
        self.assertEqual(args[1:], [os.path.abspath(str(p)), "stdout", "-l", "kor+eng"])
        with self.assertRaises(AcquireError) as cm:
            tesseract_text(str(p), self.cfg(), which_all, Runner(rc=1, stderr=b"Failed loading language 'kor'"))
        self.assertIn("tesseract-ocr-kor", str(cm.exception))
        with self.assertRaises(AcquireError) as cm:
            tesseract_text(str(p), self.cfg(), which_none, Runner())
        self.assertIn("설치", str(cm.exception))

    def test_image_without_any_backend_message(self):
        p = self.write("a.jpg", b"\xff\xd8", binary=True)
        with self.assertRaises(AcquireError) as cm:
            image_text(str(p), self.cfg(), None, which_none, Runner())
        msg = str(cm.exception)
        self.assertIn("VLM", msg)
        self.assertIn("tesseract", msg)
        self.assertIn("add -", msg)

    def test_vision_first_then_tesseract_fallback(self):
        p = self.write("a.jpg", b"\xff\xd8", binary=True)
        llm = FakeLLM(image_text="비전 결과 텍스트입니다 12,340원")
        a = image_text(str(p), self.cfg(), llm, which_all, Runner())
        self.assertEqual(a.method, "vision")
        llm_bad = FakeLLM(image_text="")           # 비전이 빈 응답
        r = Runner(stdout="tesseract 결과 텍스트 12,340원".encode("utf-8"))
        a = image_text(str(p), self.cfg(), llm_bad, which_all, r)
        self.assertEqual(a.method, "tesseract")

    def test_backend_forced(self):
        p = self.write("a.jpg", b"\xff\xd8", binary=True)
        llm = FakeLLM(image_text="비전 결과 텍스트입니다")
        r = Runner(stdout="tesseract 결과 텍스트".encode("utf-8"))
        a = image_text(str(p), self.cfg(image_backend="tesseract"), llm, which_all, r)
        self.assertEqual(a.method, "tesseract")
        self.assertEqual(llm.image_calls, [])
        with self.assertRaises(AcquireError):
            image_text(str(p), self.cfg(image_backend="vision"), None, which_all, r)

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext 미설치")
    def test_real_pdftotext_minimal_pdf(self):
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            None,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        stream = b"BT /F1 12 Tf 20 50 Td (Total 12340 won due 2026-11-25) Tj ET"
        objs[3] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        out, offsets = bytearray(b"%PDF-1.4\n"), []
        for i, o in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
        p = self.write("t.pdf", bytes(out), binary=True)
        a = acquire_text(str(p), self.cfg())
        self.assertEqual(a.method, "pdftotext")
        self.assertIn("12340", a.text)


if __name__ == "__main__":
    unittest.main()
