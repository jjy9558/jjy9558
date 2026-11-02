"""기록 저장. 데이터는 홈 폴더 안에서만 읽고 쓴다(친구 이름이 들어가므로 로컬 전용)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class StoreError(Exception):
    pass


def data_dir(override: str | None = None, env: dict | None = None) -> Path:
    e = os.environ if env is None else env
    raw = override or e.get("LIFECOURT_DIR") or ""
    home = Path.home().resolve()
    path = Path(raw).expanduser() if raw else home / ".life-court"
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if path != home and home not in path.parents:
        raise StoreError("기록 폴더는 홈 폴더 안이어야 합니다(친구 기록 보호): %s" % path)
    return path


class Log:
    """{"entries": [...]} 하나짜리 json."""

    def __init__(self, directory: Path, name: str):
        self.path = Path(directory) / (name + ".json")

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            raise StoreError("기록 파일을 읽을 수 없습니다: %s" % self.path) from e
        ents = data.get("entries") if isinstance(data, dict) else None
        return [x for x in ents if isinstance(x, dict)] if isinstance(ents, list) else []

    def save(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"entries": entries}, f, ensure_ascii=False, indent=1)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def add(self, entry: dict) -> bool:
        """같은 기록이 이미 있으면 넣지 않는다(재실행으로 재범이 부풀지 않게)."""
        ents = self.load()
        if entry in ents:
            return False
        ents.append(entry)
        self.save(ents)
        return True
