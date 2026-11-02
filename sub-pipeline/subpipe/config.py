"""Optional JSON config (--config). Everything has a default; the file overrides keys.

{
  "disclosure": "이 자막은 AI 초안을 만든 뒤 사람이 검수했습니다.",
  "power": {"tiers": [{"upto_kwh": 200, "rate": 100}, {"upto_kwh": null, "rate": 200}]}
}
"""
from __future__ import annotations

import json
from typing import Optional

DEFAULT_DISCLOSURE = "본 자막은 AI 초안 작성 후 사람이 검수했습니다."


class ConfigError(RuntimeError):
    pass


def load_config(path: Optional[str]) -> dict:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as e:
        raise ConfigError("설정 파일을 읽을 수 없습니다: %s" % path) from e
    except ValueError as e:
        raise ConfigError("설정 파일이 올바른 JSON이 아닙니다: %s" % path) from e
    if not isinstance(data, dict):
        raise ConfigError("설정 파일 최상위는 객체여야 합니다: " + path)
    return data


def disclosure(cfg: dict) -> str:
    text = str(cfg.get("disclosure") or DEFAULT_DISCLOSURE)
    return " ".join(text.split())  # always a single line
