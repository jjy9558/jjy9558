"""설정 로드/저장. 기본 위치: ~/.gamediary/config.json (환경변수 GAMEDIARY_CONFIG 로 변경)."""
import json
import os
import sys

HOME_DIR = os.path.join("~", ".gamediary")

DEFAULTS = {
    "base_url": "http://127.0.0.1:8080/v1",
    "model": "local",
    "api_key": "none",
    "timeout": 180,
    "max_mb": 8.0,                 # 이보다 큰 파일은 건너뜀(PIL 없이 축소 불가)
    "caption_source": "original",  # "original" | "thumb" (Steam 썸네일로 캡션)
    "thumbs_dir": "",              # 추가 썸네일 폴더(선택)
    "session_gap_hours": 2.0,
    "sources": [],                 # [{"path": "...", "kind": "steam|gamebar|folder"}]
    "game_names": {},              # {"570": "Dota 2"} 수동 지정
    "names_cache": os.path.join(HOME_DIR, "games.json"),
    "use_steam_buddy": True,
    "db_path": os.path.join(HOME_DIR, "gamediary.sqlite3"),
    "out_dir": os.path.join(HOME_DIR, "out"),
}

KINDS = ("steam", "gamebar", "folder")


def config_path(explicit=None):
    p = explicit or os.environ.get("GAMEDIARY_CONFIG") or os.path.join(HOME_DIR, "config.json")
    return os.path.expanduser(p)


def load_config(path=None):
    p = config_path(path)
    cfg = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("설정 파일은 JSON 객체여야 합니다: " + p)
        cfg.update(user)
        legacy_out = "out_dir" not in user
    else:
        legacy_out = True
    if legacy_out and os.path.isdir("out") and os.listdir("out"):
        cfg["out_dir"] = "out"
        print("안내: 현재 폴더의 옛 out/ 을 계속 씁니다. 사용자 폴더(%s)로 옮기려면 out/ 내용을 옮기고 "
              "설정에서 out_dir 을 지정/삭제하세요." % os.path.expanduser(DEFAULTS["out_dir"]), file=sys.stderr)
    cfg["_path"] = p
    return cfg


def save_config(cfg, path=None):
    p = config_path(path or cfg.get("_path"))
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    data = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def expand(p):
    return os.path.expanduser(p) if p else p


def coerce(key, value):
    """--set key=value 문자열을 기본값의 타입에 맞춰 변환."""
    if key not in DEFAULTS:
        raise KeyError("알 수 없는 설정 키: " + key)
    d = DEFAULTS[key]
    if isinstance(d, bool):
        return value.lower() in ("1", "true", "yes", "y", "on")
    if isinstance(d, float):
        return float(value)
    if isinstance(d, int):
        return int(value)
    if isinstance(d, (list, dict)):
        return json.loads(value)
    return value
