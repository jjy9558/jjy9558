"""JSON 저장소 (표준 라이브러리만 사용)."""
import copy
import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "nudge_minutes": 30,
    "max_skips": 5,
    "max_age_days": 30,
    "llm": {
        "endpoint": "http://localhost:1234/v1",
        "model": "local-model",
        "api_key": "",
        "timeout": 8,
    },
}


def home_dir(env=None):
    """DECISION_PET_HOME > %APPDATA%/decision-pet > ~/.decision-pet"""
    env = os.environ if env is None else env
    custom = env.get("DECISION_PET_HOME")
    if custom:
        return Path(custom)
    appdata = env.get("APPDATA")
    if appdata:
        return Path(appdata) / "decision-pet"
    return Path.home() / ".decision-pet"


def default_state():
    return {
        "version": 1,
        "next_id": 1,
        "tasks": [],
        "decisions": [],
        "next_decision_id": 1,
        "current": None,
        "experiment_log": {},
        "config": copy.deepcopy(DEFAULT_CONFIG),
    }


def _merge_defaults(state):
    base = default_state()
    for key, value in base.items():
        state.setdefault(key, value)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    user_cfg = state.get("config") or {}
    llm = dict(cfg["llm"])
    llm.update(user_cfg.get("llm") or {})
    cfg.update({k: v for k, v in user_cfg.items() if k != "llm"})
    cfg["llm"] = llm
    state["config"] = cfg
    return state


class Store:
    """상태 전체를 하나의 JSON 파일(state.json)로 저장한다."""

    def __init__(self, directory=None):
        self.dir = Path(directory) if directory else home_dir()
        self.path = self.dir / "state.json"
        self.experiments_path = self.dir / "experiments.txt"

    def load(self):
        if not self.path.exists():
            return default_state()
        with open(self.path, "r", encoding="utf-8") as f:
            return _merge_defaults(json.load(f))

    def save(self, state):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(str(tmp), str(self.path))
