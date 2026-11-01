import json
import os

DEFAULTS = {
    "appids": [],
    "names": {},                       # {"appid": "표시 이름"}
    "steam_buddy": {"enabled": False, "path": "~/.steam-buddy/data.json"},
    "news_count": 10,
    "request_delay_sec": 1.5,
    "cache_ttl_hours": 6,
    "default_tz": "UTC",               # 시간대 표기 없는 시각에 가정할 TZ
    "llm": {"enabled": True, "base_url": "http://127.0.0.1:8080/v1", "model": "local",
            "timeout": 60, "api_key": None},
    "ntfy": {"server": "https://ntfy.sh", "topic": ""},
    "dday": [30, 7, 1],
    "ics_keep_days": 60,               # 이보다 오래된 일정은 ics에서 제외
    "calendar_name": "내 게임 캘린더",
    "cache_dir": "cache",
    "out_dir": "out",
    "state_file": "state.json",
}


def _merge(base, over):
    r = dict(base)
    for k, v in over.items():
        r[k] = _merge(base[k], v) if isinstance(base.get(k), dict) and isinstance(v, dict) else v
    return r


def load_config(path):
    cfg = dict(DEFAULTS)
    base_dir = os.getcwd()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = _merge(DEFAULTS, json.load(f))
        base_dir = os.path.dirname(os.path.abspath(path))
    for k in ("cache_dir", "out_dir", "state_file"):
        if not os.path.isabs(cfg[k]):
            cfg[k] = os.path.join(base_dir, cfg[k])
    cfg["_path"] = path
    return cfg


def add_appid(path, appid):
    """설정 파일에 appid 추가. 이미 있으면 False."""
    raw = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    ids = [int(x) for x in raw.get("appids", [])]
    if int(appid) in ids:
        return False
    ids.append(int(appid))
    raw["appids"] = ids
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True


def load_games(cfg):
    """-> {appid: name}  (config appids + 선택적 steam-buddy 가져오기)"""
    from .steam import import_steam_buddy
    games = {}
    sb = cfg.get("steam_buddy") or {}
    if sb.get("enabled"):
        try:
            games.update(import_steam_buddy(sb.get("path", "~/.steam-buddy/data.json")))
        except (OSError, ValueError):
            pass
    for a in cfg.get("appids", []):
        games.setdefault(int(a), "App %d" % int(a))
    names = cfg.get("names") or {}
    for a in list(games):
        if str(a) in names:
            games[a] = names[str(a)]
    return games
