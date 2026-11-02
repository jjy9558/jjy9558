"""설정 로딩: 기본값 <- ~/.devguard/config.json <- (저장소) .devguard.json

JSON 만 사용한다 (Python 3.9 에는 tomllib 이 없으므로).
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, Optional

DEFAULTS: Dict[str, Any] = {
    "friday": {
        # 막을 요일: "mon".."sun" 또는 0..6 (월=0) 또는 "금"
        "days": ["fri"],
        "after": "15:00",       # 이 시각부터 (로컬 시간)
        "before": "24:00",      # 이 시각 전까지
        "all_day_days": [],     # 하루 종일 막을 요일 (예: ["sat", "sun"])
        "branches": ["main", "master", "release/*"],
        "tags": [],             # 보호할 태그 패턴 (예: ["v*"]). 기본은 태그 푸시 허용.
    },
    "secrets": {
        "disable_rules": [],
        "min_entropy": 3.5,
        "max_findings_shown": 20,
    },
    "strike": {
        "enabled": True,
        "threshold": 3,         # 같은 명령 연속 실패 N번째에 파업
        "edit_flips": 3,        # 같은 파일 A->B->A 되돌림 N번째에 파업
        "max_denies": 3,        # 파업 중 거부 횟수 상한 (이후 자동 해제, 교착 방지)
        "ttl_seconds": 1800,    # 파업 자동 해제 시간
        "action": "deny",       # "deny" | "stop"(continue:false 로 클로드 자체를 멈춤)
        "output": "json",       # "json" | "exit2"(종료코드 2 + stderr 폴백)
        "ignore_regex": [],     # 파업 집계에서 제외할 명령 정규식
    },
    "wallet": {
        "daily_limit_usd": 0,   # 0 = 한도 미설정
        "session_limit_usd": 0,
        "warn_ratio": 0.8,
        "ntfy_url": "",         # 예: https://ntfy.sh/내-비밀-토픽 (선택)
        "prices_confirmed": False,  # 단가표를 공식 가격 페이지로 갱신한 뒤 true 로
        "prices": {},           # 모델 계열별 덮어쓰기 (wallet.PLACEHOLDER_PRICES 참고)
    },
}


def devguard_home(env: Optional[Dict[str, str]] = None) -> str:
    env = os.environ if env is None else env
    return env.get("DEVGUARD_HOME") or os.path.join(os.path.expanduser("~"), ".devguard")


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_json_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def find_repo_config(start: Optional[str]) -> Optional[str]:
    """start 에서 위로 올라가며 .devguard.json 을 찾는다 (.git 을 만나면 거기까지)."""
    if not start:
        return None
    cur = os.path.abspath(start)
    for _ in range(30):
        cand = os.path.join(cur, ".devguard.json")
        if os.path.isfile(cand):
            return cand
        if os.path.exists(os.path.join(cur, ".git")):
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return None


def load_config(repo_dir: Optional[str] = None, home: Optional[str] = None) -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    home = home or devguard_home()
    cfg = deep_merge(cfg, load_json_file(os.path.join(home, "config.json")))
    repo_cfg = find_repo_config(repo_dir)
    if repo_cfg:
        cfg = deep_merge(cfg, load_json_file(repo_cfg))
    return cfg
