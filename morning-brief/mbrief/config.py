"""설정: JSON 파일 하나(mbrief.json). 없으면 기본값. 상대 경로는 설정 파일이 있는 폴더 기준."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

CONFIG_NAME = "mbrief.json"
HARD_MAX_CHARS = 300          # 계약: speak_text 상한 (WP18 알람 게이트의 say.max_chars 와 맞춤)

# 어댑터 이름 -> 기본 가중치. 주말(조용한 날)에는 quiet_days.weights 를 곱한다.
DEFAULTS: dict[str, Any] = {
    "tz_offset_minutes": 540,
    "default_time": "07:30",               # --date 로 날짜만 줄 때 쓰는 가상의 '지금'
    "out_dir": "out",
    "reject_text": "일어나면 해 드립니다",
    "speak": {"seconds": 60, "chars_per_sec": 5.0, "max_chars": HARD_MAX_CHARS, "intro": True},
    "weights": {},
    "quiet_days": {
        "weekdays": [5, 6],                # 0=월 ... 6=일
        "weights": {"game_calendar": 1.5, "community_briefing": 1.4, "claude_night": 0.7,
                    "voice_journal": 0.8},
    },
    "sources": {},                         # 어댑터별 설정은 각 어댑터의 DEFAULTS 와 합쳐 쓴다
    "notify": {
        "sidekick": {"url": "http://127.0.0.1:8765", "token_env": "SIDEKICK_TOKEN", "token": "",
                     "module": "morning-brief"},
        "ntfy": {"server": "https://ntfy.sh", "topic": "", "token_env": "NTFY_TOKEN", "token": "",
                 "priority": "default"},
    },
}


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class ConfigError(Exception):
    pass


def find_config(explicit: str | None, env: dict | None = None, cwd: Path | None = None) -> Path | None:
    """--config > $MBRIEF_CONFIG > ./mbrief.json. 없으면 None(기본값으로 진행)."""
    env = os.environ if env is None else env
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigError("설정 파일이 없어요: %s" % explicit)
        return p
    if env.get("MBRIEF_CONFIG"):
        p = Path(env["MBRIEF_CONFIG"]).expanduser()
        if not p.is_file():
            raise ConfigError("MBRIEF_CONFIG 파일이 없어요: %s" % p)
        return p
    p = (cwd or Path.cwd()) / CONFIG_NAME
    return p if p.is_file() else None


def load_config(path: Path | None) -> tuple[dict, Path]:
    """(합쳐진 설정, 기준 폴더)."""
    cfg = copy.deepcopy(DEFAULTS)
    base = Path.cwd()
    if path is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ConfigError("설정 파일을 읽을 수 없어요 (%s): %s" % (path.name, e))
        if not isinstance(raw, dict):
            raise ConfigError("설정 파일은 JSON 객체여야 해요")
        cfg = deep_merge(cfg, raw)
        base = path.resolve().parent
    speak = cfg["speak"]
    speak["max_chars"] = max(20, min(int(speak.get("max_chars", HARD_MAX_CHARS)), HARD_MAX_CHARS))
    return cfg, base


def secret(section: dict, env: dict) -> str:
    """토큰: 설정의 token 이 있으면 그것, 없으면 token_env 환경변수."""
    if section.get("token"):
        return str(section["token"])
    name = section.get("token_env")
    return str(env.get(name, "")) if name else ""
