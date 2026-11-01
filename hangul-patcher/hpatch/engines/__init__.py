from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import ApplyOptions, Engine
from .renpy import RenPyEngine
from .rpgmaker import RPGMakerEngine

ENGINES = {"rpgmaker": RPGMakerEngine, "renpy": RenPyEngine}


def detect_engine(game_dir, forced: Optional[str] = None) -> Optional[Engine]:
    game_dir = Path(game_dir)
    if forced and forced != "auto":
        return ENGINES[forced].detect(game_dir)
    for cls in ENGINES.values():
        eng = cls.detect(game_dir)
        if eng is not None:
            return eng
    return None


__all__ = ["ApplyOptions", "Engine", "ENGINES", "detect_engine", "RPGMakerEngine", "RenPyEngine"]
