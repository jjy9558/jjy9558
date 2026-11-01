"""데이터 폴더 위치와 사용자 설정."""

import copy
import json
import os
import tempfile

DEFAULT_CONFIG = {
    # RAM 정격 속도(XMP/EXPO, MT/s). 비워 두면 부품번호/SPD로 추정.
    "expected_ram_mts": None,
    # llama.cpp 등에서 쓰는 GGUF 모델 폴더들 (업그레이드 조언·페이지파일 점검에 사용)
    "model_dirs": [],
    "gpu_driver_max_age_days": 180,
    "startup_apps_warn": 15,
    "disk_warn_percent": 90,
    "disk_critical_percent": 95,
    "update_recent_days": 45,
    "update_stale_days": 60,
    "gamemode": {
        # saver / balanced / high / ultimate 또는 GUID
        "power_plan": "high",
        # 사용자가 직접 적은 것만 종료합니다. 시스템 필수 항목은 적어도 거부됩니다.
        "stop_processes": [],
        "stop_services": [],
        # 게임 모드 off 때 종료했던 프로그램을 다시 실행할지
        "relaunch_processes": True,
        # watch 모드에서 감시할 게임 실행 파일 이름
        "game_exes": [],
        "poll_seconds": 10,
    },
}


def data_dir():
    """%LOCALAPPDATA%\\pc-doctor (테스트/다른 OS는 PC_DOCTOR_HOME 우선)."""
    override = os.environ.get("PC_DOCTOR_HOME")
    if override:
        base = override
    elif os.environ.get("LOCALAPPDATA"):
        base = os.path.join(os.environ["LOCALAPPDATA"], "pc-doctor")
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share", "pc-doctor")
    os.makedirs(base, exist_ok=True)
    return base


def sub_dir(name):
    p = os.path.join(data_dir(), name)
    os.makedirs(p, exist_ok=True)
    return p


def config_path():
    return os.path.join(data_dir(), "config.json")


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    path = path or config_path()
    user = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
    return _merge(DEFAULT_CONFIG, user)


def write_json_atomic(path, obj):
    """임시 파일에 쓰고 fsync 후 교체 → 쓰는 도중 꺼져도 반쪽 파일이 남지 않음."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
