# -*- coding: utf-8 -*-
import datetime as dt
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from paperbutler.butler import Butler
from paperbutler.config import Config

TODAY = dt.date(2026, 11, 2)
OLD_MTIME = 1_000_000
NOW = dt.datetime(2026, 11, 2, 16, 0, 0)


class FakeLLM:
    """네트워크 없이 LLM 응답을 흉내내는 가짜 클라이언트."""

    def __init__(self, fields=None, image_text=None, error=None):
        self.fields = fields or {}
        self.image_text = image_text
        self.error = error
        self.calls = []
        self.image_calls = []

    def extract_fields(self, masked_text):
        self.calls.append(masked_text)
        if self.error:
            raise self.error
        return self.fields

    def transcribe_image(self, path):
        self.image_calls.append(path)
        if self.error:
            raise self.error
        return self.image_text or ""


class TmpCase(unittest.TestCase):
    """임시 home 을 가진 테스트 베이스."""

    today = TODAY

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pbtest-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = Path(self.tmp) / "home"

    def butler(self, today=None, llm=None, **cfg_kw):
        cfg = Config(home=self.home, **cfg_kw)
        b = Butler(cfg, llm=llm, today=today or self.today, now=NOW,
                   which=lambda name: None)
        self.addCleanup(b.close)
        return b

    def write(self, name, content, mtime=None, binary=False):
        p = Path(self.tmp) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path, encoding="utf-8"):
    with open(path, encoding=encoding) as f:
        return f.read()
