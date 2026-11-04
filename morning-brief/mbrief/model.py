"""공통 자료형: Item(어댑터가 내는 한 항목), Context(실행 환경), 어댑터 상태 예외."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ._jjkit.redact import Redactor

KST_MINUTES = 540


class SourceSkip(Exception):
    """어댑터를 일부러 건너뜀(예: --dry-run에서 네트워크 어댑터). 상태 '건너뜀'."""


class SourceMissing(Exception):
    """원본 파일/설정이 없음. 오류는 아니지만 아직 연결되지 않은 상태. 상태 '없음'."""


@dataclass
class Item:
    """아침 브리핑 한 항목.

    priority 는 0~100(클수록 급함). speak 가 비어 있으면 낭독하지 않고 카드/md 에만 남는다.
    speak_short 는 예산이 모자랄 때 대신 쓰는 짧은 문장이다. due 는 ISO 날짜 또는 분 단위 ISO 시각.
    """
    source: str
    kind: str
    title: str
    detail: str = ""
    priority: float = 0.0
    speak: str = ""
    speak_short: str = ""
    due: str | None = None
    actions: tuple = ()      # 문자열(표시 전용) 또는 {"id","label","text"}
    section: str = ""
    card: bool = True


@dataclass
class Context:
    now: dt.datetime                       # 시간대가 붙은 현재 시각
    base_dir: Path = field(default_factory=Path.cwd)   # 설정 파일 폴더(상대 경로 기준)
    dry_run: bool = False                  # True 면 네트워크 호출 0
    opener: Any = None                     # 테스트용 가짜 opener
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    redactor: Redactor = field(default_factory=lambda: Redactor())
    notes: dict = field(default_factory=dict)     # 어댑터 이름 -> 상태표 비고(예: 읽은 방식)

    @property
    def tz(self) -> dt.tzinfo:
        return self.now.tzinfo or dt.timezone.utc

    @property
    def today(self) -> dt.date:
        return self.now.date()

    def path(self, value: str | os.PathLike) -> Path:
        p = Path(str(value)).expanduser()
        return p if p.is_absolute() else self.base_dir / p

    def home_path(self, env_name: str, default: str) -> Path:
        """환경변수가 있으면 그것, 없으면 default(홈 기준)."""
        raw = self.env.get(env_name)
        return Path(raw).expanduser() if raw else Path(default).expanduser()

    def red(self, text: Any) -> str:
        return self.redactor.redact(str(text or ""))


def iso_minutes(value: dt.datetime) -> str:
    return value.isoformat(timespec="minutes")
