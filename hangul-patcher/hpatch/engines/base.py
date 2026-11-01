from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from ..backup import BackupStore
from ..masking import Masker
from ..units import ApplyReport, ExtractResult


@dataclass
class ApplyOptions:
    font: Optional[str] = None  # 사용자가 제공한 .ttf/.otf/.woff 경로
    locale_ko: bool = False  # RPG Maker: System.json locale 을 ko_KR 로 (Dotum 등 시스템 한글 폰트 사용)
    language: str = "korean"  # Ren'Py tl 폴더 이름
    default_language: bool = True  # Ren'Py: config.default_language 를 한국어로


class Engine:
    name = ""
    label = ""

    def __init__(self, game_dir) -> None:
        self.game_dir = Path(game_dir)

    @classmethod
    def detect(cls, game_dir: Path) -> Optional["Engine"]:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> str:
        return self.label

    def masker(self) -> Masker:  # pragma: no cover - interface
        raise NotImplementedError

    def finalize(self, source: str, target: str) -> Optional[str]:
        """번역문 후처리/검증. None 이면 거부."""
        return target

    def extract(self, store: BackupStore) -> ExtractResult:  # pragma: no cover - interface
        raise NotImplementedError

    def apply(self, store: BackupStore, translations: Dict[str, str], options: ApplyOptions) -> ApplyReport:  # pragma: no cover
        raise NotImplementedError
