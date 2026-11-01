"""Shared helpers for the tests (synthetic data only)."""

import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from kakao_wrapped.parser import Message, classify_text  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
NAMES = ("철수", "영희", "민수")


def fixture_path(name):
    return os.path.join(FIXTURES, name)


def read_fixture(name):
    with open(fixture_path(name), "rb") as fh:
        return fh.read()


def M(when, user, text):
    """Build a Message: M("2026-03-01 10:00:05", "철수", "안녕")."""
    fmt = "%Y-%m-%d %H:%M:%S" if when.count(":") == 2 else "%Y-%m-%d %H:%M"
    kind, count = classify_text(text)
    return Message(datetime.strptime(when, fmt), user, text, kind, count)
