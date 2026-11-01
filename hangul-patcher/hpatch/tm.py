"""번역 메모리 (sqlite). 원문 해시 -> 번역문."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

from .units import source_hash


class TranslationMemory:
    def __init__(self, path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS tm ("
            " hash TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,"
            " origin TEXT NOT NULL DEFAULT 'llm', model TEXT, created REAL)"
        )
        self.db.commit()

    def close(self) -> None:
        try:
            self.db.commit()
            self.db.close()
        except sqlite3.ProgrammingError:
            pass  # 이미 닫힘

    def __del__(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass

    def commit(self) -> None:
        self.db.commit()

    def get(self, source: str) -> Optional[str]:
        row = self.db.execute("SELECT target FROM tm WHERE hash=?", (source_hash(source),)).fetchone()
        return row[0] if row else None

    def has(self, source: str) -> bool:
        return self.get(source) is not None

    def put(self, source: str, target: str, origin: str = "llm", model: str = "", overwrite: bool = False) -> bool:
        """저장. 수동(manual) 항목은 manual 로만 덮어쓴다. 저장했으면 True."""
        h = source_hash(source)
        row = self.db.execute("SELECT origin FROM tm WHERE hash=?", (h,)).fetchone()
        if row is not None:
            if row[0] == "manual" and origin != "manual":
                return False
            if not overwrite and origin != "manual":
                return False
        self.db.execute(
            "INSERT OR REPLACE INTO tm(hash, source, target, origin, model, created) VALUES (?,?,?,?,?,?)",
            (h, source, target, origin, model, time.time()),
        )
        return True

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM tm").fetchone()[0]

    def items(self) -> Iterator[Tuple[str, str, str]]:
        for source, target, origin in self.db.execute("SELECT source, target, origin FROM tm ORDER BY created"):
            yield source, target, origin

    def as_dict(self) -> Dict[str, str]:
        return {s: t for s, t, _ in self.items()}
