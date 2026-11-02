"""로컬 저장소: 냉장고 목록과 사용자 분리배출 규칙(JSON). 외부로 나가지 않는다."""
import json
import os
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def home_dir(override: str | None = None) -> Path:
    p = override or os.environ.get("HOUSEDET_HOME")
    return Path(p) if p else Path.home() / ".house-detective"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def builtin_shelf() -> list[dict]:
    return _load(DATA_DIR / "shelf.json", {}).get("items", [])


def builtin_recycle() -> list[dict]:
    return _load(DATA_DIR / "recycle.json", {}).get("rules", [])


def load_fridge(home: Path) -> list[dict]:
    d = _load(home / "fridge.json", {})
    return d.get("items", []) if isinstance(d, dict) else []


def save_fridge(home: Path, items: list[dict]) -> None:
    _save(home / "fridge.json", {"items": items})


def load_user_rules(home: Path) -> list[dict]:
    d = _load(home / "rules.json", {})
    return d.get("rules", []) if isinstance(d, dict) else []


def save_user_rules(home: Path, rules: list[dict]) -> None:
    _save(home / "rules.json", {"rules": rules})


def parse_date(s: str, today: date) -> date:
    if s in ("오늘", "today"):
        return today
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"날짜는 YYYY-MM-DD 형식이어야 합니다: {s}")
