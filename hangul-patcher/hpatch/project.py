"""작업 폴더(번역 메모리/용어집/추출 결과)와 공통 파이프라인 헬퍼."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .backup import BackupStore
from .engines import Engine, detect_engine
from .glossary import Glossary
from .tm import TranslationMemory
from .units import ExtractResult

WORK_DIRNAME = ".hpatch"


class Workspace:
    def __init__(self, game_dir, work_dir=None, tm_path=None, glossary_path=None) -> None:
        self.game_dir = Path(game_dir)
        self.dir = Path(work_dir) if work_dir else self.game_dir / WORK_DIRNAME
        self.tm_path = Path(tm_path) if tm_path else self.dir / "tm.sqlite"
        self._glossary_path = Path(glossary_path) if glossary_path else None
        self.units_path = self.dir / "units.json"

    @property
    def glossary_path(self) -> Optional[Path]:
        if self._glossary_path:
            return self._glossary_path
        for name in ("glossary.json", "glossary.tsv", "glossary.txt"):
            p = self.dir / name
            if p.is_file():
                return p
        return None

    def load_glossary(self) -> Glossary:
        p = self.glossary_path
        if p is None:
            return Glossary()
        if not p.is_file():
            raise FileNotFoundError(f"용어집 파일을 찾을 수 없습니다: {p}")
        return Glossary.load(p)

    def open_tm(self) -> TranslationMemory:
        return TranslationMemory(self.tm_path)

    def tm_exists(self) -> bool:
        return self.tm_path.is_file()


def load_engine(game_dir, forced: Optional[str] = None) -> Engine:
    eng = detect_engine(game_dir, forced)
    if eng is None:
        raise ValueError("지원하는 엔진을 찾지 못했습니다 (RPG Maker MV/MZ, Ren'Py). "
                         "게임 폴더(실행 파일이 있는 폴더)를 지정했는지 확인하세요.")
    return eng


def extract_all(engine: Engine, store: Optional[BackupStore] = None) -> ExtractResult:
    return engine.extract(store or BackupStore(engine.game_dir))


def save_units(ws: Workspace, res: ExtractResult) -> None:
    ws.dir.mkdir(parents=True, exist_ok=True)
    payload = {"units": [u.to_dict() for u in res.units], "notes": res.notes}
    ws.units_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def unique_kinds(res: ExtractResult) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for u in res.units:
        counts[u.kind] = counts.get(u.kind, 0) + 1
    return counts
