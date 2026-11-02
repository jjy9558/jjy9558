import copy
import json
import os

from .timeutil import parse_hhmm


class ConfigError(ValueError):
    pass


DEFAULTS = {
    "poll_seconds": 30,
    "state_dir": "~/.life-nagger",
    "present_idle_seconds": 180,       # 마지막 입력 후 이 시간 안이면 '자리에 있음'
    "games": ["VALORANT-Win64-Shipping.exe", "League of Legends.exe", "LeagueClient.exe",
              "eldenring.exe", "Overwatch.exe", "Diablo IV.exe", "StarCraft II.exe",
              "MapleStory.exe", "Lost Ark.exe"],
    "work_apps": ["Code.exe", "Cursor.exe", "WindowsTerminal.exe", "wt.exe", "powershell.exe",
                  "pwsh.exe", "cmd.exe", "claude.exe", "pycharm64.exe", "idea64.exe",
                  "devenv.exe", "notepad++.exe"],
    "mombot": {
        "enabled": True,
        "start": "02:00", "end": "06:00",
        "interval_minutes": 10,
        "allow_sleep": False,            # 기본 OFF. 반드시 true(불리언)로 직접 켜야 함
        "save_grace_minutes": 5,         # 마지막 경고 후 저장할 시간
        "countdown_seconds": 60,
        "sleep_method": "suspend",       # suspend | hibernate
        "lines": ["지금 몇 시니? ({time})", "엄마 화났다. {game} 당장 끄자.",
                  "마지막 경고. 진짜로 마지막이다."],
    },
    "sitting": {
        "enabled": True,
        "sit_limit_minutes": 120, "break_minutes": 5, "repeat_minutes": 10,
        "message": "엉덩이 굳는다. 일어나서 물 한 잔.",
        "praise": "오, 일어났네. 물 마셨지? 잘했다.",
    },
    "work": {"enabled": True, "max_gap_seconds": 120, "clockout_after_minutes": 30},
    "dishes": {"enabled": True, "remind_hour": 20},
    "counters": {
        "sigh": {"label": "한숨", "unit": "회"},
        "pushup": {"label": "푸시업", "unit": "개"},
        "dishes": {"label": "설거지", "unit": "회"},
    },
    "outputs": {
        "console": {"enabled": True, "min_level": "info"},
        "toast": {"enabled": True, "min_level": "warn"},
        "ntfy": {"enabled": False, "url": "https://ntfy.sh", "topic": "", "min_level": "warn"},
        "sidekick": {"enabled": False, "base_url": "http://127.0.0.1:8765", "token": "",
                     "min_level": "warn"},
    },
    "events": {"enabled": False, "host": "127.0.0.1", "port": 8799, "token": ""},
    "llm": {"base_url": "", "model": "", "api_key": "", "timeout": 20},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("counters",):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def validate(cfg):
    m = cfg["mombot"]
    if not isinstance(m.get("allow_sleep"), bool):
        raise ConfigError("mombot.allow_sleep은 true/false(불리언)여야 합니다. 문자열 \"true\"는 인정 안 함.")
    if m.get("sleep_method") not in ("suspend", "hibernate"):
        raise ConfigError("mombot.sleep_method는 suspend 또는 hibernate")
    for k in ("start", "end"):
        try:
            parse_hhmm(m[k])
        except ValueError as e:
            raise ConfigError("mombot.%s: %s" % (k, e))
    for path in (("poll_seconds",), ("mombot", "interval_minutes"), ("mombot", "countdown_seconds"),
                 ("sitting", "sit_limit_minutes"), ("sitting", "break_minutes"),
                 ("sitting", "repeat_minutes")):
        v = cfg
        for p in path:
            v = v[p]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ConfigError("%s는 0보다 큰 숫자여야 합니다" % ".".join(path))
    if len(m["lines"]) < 3:
        raise ConfigError("mombot.lines는 3줄이 필요합니다")
    if cfg["events"]["enabled"] and not cfg["events"]["token"]:
        raise ConfigError("events.enabled를 켜려면 events.token이 필요합니다")
    return cfg


def load_config(path=None, overrides=None):
    raw = {}
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            raise ConfigError("설정 파일이 없습니다: %s" % path)
        except ValueError as e:
            raise ConfigError("설정 JSON 오류: %s" % e)
    cfg = _merge(DEFAULTS, raw)
    if overrides:
        cfg = _merge(cfg, overrides)
    return validate(cfg)


def state_path(cfg, name):
    d = os.path.expanduser(cfg["state_dir"])
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)
