"""규칙들이 주고받는 작은 데이터 타입."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import FrozenSet, Optional

INFO, WARN, URGENT = 0, 1, 2
LEVELS = {"info": INFO, "warn": WARN, "urgent": URGENT}

MODULE_LABELS = {
    "mombot": "엄마봇",
    "sitting": "엉덩이 경보",
    "work": "출근 도장",
    "dishes": "설거지",
    "counter": "카운터",
    "system": "life-nagger",
}


@dataclass(frozen=True)
class Message:
    text: str
    module: str
    level: int = INFO


@dataclass(frozen=True)
class Action:
    kind: str          # 현재는 "suspend" 하나뿐
    reason: str = ""


@dataclass(frozen=True)
class Snapshot:
    now: datetime
    idle_s: float
    foreground: Optional[str]            # 정규화된 프로세스 이름 (소문자, .exe 제거)
    processes: FrozenSet[str] = field(default_factory=frozenset)


def norm_proc(name):
    """'Code.exe' / 'C:\\x\\Code.EXE' -> 'code'."""
    if not name:
        return ""
    n = str(name).replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if n.endswith(".exe"):
        n = n[:-4]
    return n
