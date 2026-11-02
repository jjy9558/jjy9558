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
    # 의회가 읽을 다른 프로젝트 데이터 경로. 비우면 각 프로젝트의 문서화된 기본 위치.
    "sources": {
        "cool_off": "",       # cool-off data.json
        "steam_buddy": "",    # steam-buddy data.json
        "life_nagger": "",    # life-nagger 상태 폴더 (status.json, work.json)
        "voice_journal": "",  # voice-journal journal.db
        "paper_butler": "",   # paper-butler paperbutler.db
    },
    "council": {
        "monthly_budget": 500000,   # 이번 달 지출이 이 금액을 넘으면 paper-butler 의원이 반대
        "game_hours_limit": 40,     # 이번 달 게임 시간이 이 값 이상이면 life-nagger 의원이 반대
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
        "lunch": {"menu": [], "history": []},
        "config": copy.deepcopy(DEFAULT_CONFIG),
    }


def _merge_defaults(state):
    base = default_state()
    for key, value in base.items():
        state.setdefault(key, value)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    user_cfg = state.get("config") or {}
    nested = ("llm", "sources", "council")
    merged = {}
    for key in nested:
        d = dict(cfg[key])
        d.update(user_cfg.get(key) or {})
        merged[key] = d
    cfg.update({k: v for k, v in user_cfg.items() if k not in nested})
    cfg.update(merged)
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
