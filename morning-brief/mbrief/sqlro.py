"""다른 프로젝트의 sqlite 를 읽기 전용(mode=ro)으로만 연다. 원본 파일은 건드리지 않는다."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .model import SourceMissing


@contextmanager
def open_ro(path: str | Path, label: str = "DB") -> Iterator[sqlite3.Connection]:
    p = Path(path)
    if not p.is_file():
        raise SourceMissing("%s 파일이 없어요: %s" % (label, p.name))
    uri = p.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=3.0)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=1")
        yield con
    finally:
        con.close()
