"""데이터 폴더와 JSON 파일 읽기/쓰기 (원자적 저장).

기본 위치: 환경변수 ``SHORTSFB_HOME`` 또는 ``~/.shorts-feedback``. 명령마다 ``--data``로 바꿀 수 있다.
"""
import json
import os
import tempfile
from pathlib import Path


def data_dir(explicit: str | os.PathLike | None = None) -> Path:
    base = explicit or os.environ.get("SHORTSFB_HOME") or (Path.home() / ".shorts-feedback")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except ValueError as e:
        raise ValueError("JSON 파일이 깨졌습니다: %s (%s)" % (path, e)) from e


def write_json(path: Path, obj) -> None:
    """같은 폴더에 임시 파일을 쓴 뒤 바꿔치기해서 중간에 끊겨도 원본이 남게 한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
