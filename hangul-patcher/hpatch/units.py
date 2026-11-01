"""엔진 공통 자료구조."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


def source_hash(text: str) -> str:
    """번역 메모리 키: 원문 문자열의 해시."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


@dataclass
class Unit:
    """번역 대상 문자열 하나(또는 연속된 대사 줄 묶음)."""

    file: str  # 게임 폴더 기준 상대경로 (posix)
    kind: str  # dialogue / choice / name / description / message / term / title / scroll_text
    source: str
    path: List[Any] = field(default_factory=list)  # 엔진별 위치 정보 (JSON 직렬화 가능)
    block: Optional[List[int]] = None  # RPG Maker 대사 묶음: [시작 인덱스, 줄 수, 코드]
    speaker: str = ""
    prev: str = ""
    line: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def hash(self) -> str:
        return source_hash(self.source)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["hash"] = self.hash
        return d


@dataclass
class ExtractResult:
    units: List[Unit] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    files: int = 0
    ok: bool = True  # False: 번역할 수 있는 소스가 없음 (예: rpyc/rpa 뿐)


@dataclass
class ApplyReport:
    written: List[str] = field(default_factory=list)
    replaced: int = 0
    untranslated: int = 0
    notes: List[str] = field(default_factory=list)
