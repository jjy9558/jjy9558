"""JSON 파일 저장소. 저장은 임시 파일에 쓴 뒤 교체해서 중간에 끊겨도 기존 데이터가 깨지지 않게 합니다."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Optional

from .core import empty_state

DATA_FILE = "data.json"


class StoreError(Exception):
    """데이터 파일을 읽거나 쓸 수 없을 때."""


def home_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    raw = env.get("COOLOFF_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".cool-off"


class Store:
    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return empty_state()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError(f"데이터 파일을 읽을 수 없어요 ({self.path}): {exc}")
        if not isinstance(data, dict):
            raise StoreError(f"데이터 파일 형식이 올바르지 않아요: {self.path}")
        defaults = empty_state()
        for key, value in defaults.items():
            data.setdefault(key, value)
        return data

    def save(self, state: dict) -> None:
        directory = self.path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".data-", suffix=".tmp")
        except OSError as exc:
            raise StoreError(f"저장 폴더를 만들 수 없어요 ({directory}): {exc}")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            if isinstance(exc, OSError):
                raise StoreError(f"데이터를 저장할 수 없어요 ({self.path}): {exc}")
            raise
