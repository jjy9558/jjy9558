"""Config loading with defaults. Relative paths resolve against the config file's folder."""
import copy
import json
import os

DEFAULTS = {
    "sources": [],
    "interests": {
        "topics": {},          # {"토픽 이름": {"키워드": 가중치, ...}}
        "mute": [],
        "comment_boost": 0.5,  # score += boost * log10(1 + comments)
        "multi_source_boost": 0.5,
        "min_score": 1.0,
        "top_n": 15,
    },
    "dedupe": {"title_similarity": 0.82, "remember_days": 14},
    "fetch": {
        "user_agent": "community-briefing/0.1 (personal RSS reader)",
        "delay_seconds": 2.0,
        "timeout": 20,
        "cache_dir": "cache",
        "respect_robots": True,  # applies to type "html" sources
    },
    "llm": {
        "enabled": True,
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "local",
        "api_key": "",
        "timeout": 60,
        "temperature": 0.3,
        "use_proxy": False,  # local server: bypass system HTTP proxy
    },
    "output": {"dir": "out", "db_path": "data/seen.sqlite3", "since": "24h"},
    "delivery": {"ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": "", "token": ""}},
    "tts": {
        "enabled": False,
        "backend": "none",        # none | sapi | piper | openai_compat
        "style": "monologue",     # monologue | dialogue (진행자 A/B)
        "use_llm": True,          # 대본을 LLM으로 다듬기 (실패하면 템플릿)
        "feed": {"enabled": False, "base_url": "", "title": "커뮤니티 브리핑"},
    },
    "timeline": {"enabled": False, "dir": "data/timeline", "html": "timeline.html", "days": 30},
}


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def from_dict(data, base_dir="."):
    cfg = _merge(copy.deepcopy(DEFAULTS), data or {})
    cfg["_base_dir"] = os.path.abspath(base_dir)
    return cfg


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    return from_dict(data, os.path.dirname(os.path.abspath(path)))


def resolve(cfg, path):
    return path if os.path.isabs(path) else os.path.join(cfg["_base_dir"], path)
