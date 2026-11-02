"""설정(JSON) 로드/저장, 보정(calibration) 계산."""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import secrets
import tempfile
from typing import Any, Dict, Optional

NOMINAL_OFFSET_DB = 120.0   # 표시 dB = A-가중 dBFS + 120 + 사용자 보정. 폰 마이크 대략치일 뿐, 기기마다 다르다.

DEFAULTS: Dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8770, "token": "", "max_body_kb": 512},
    "utc_offset_minutes": 540,   # 보고서 시간대 (한국 UTC+9, 서머타임 없음)
    "noise": {
        "enabled": True,
        "threshold_db": 50.0,      # 표시 dB(A상당) 기준. 보정 전에는 상대값
        "hysteresis_db": 2.0,      # 내려올 때는 threshold - 2 dB 아래여야 끝
        "min_duration_ms": 300,    # 이 시간 이상 넘어야 이벤트
        "merge_gap_ms": 2000,      # 이 간격 이내의 이벤트는 하나로 합침
    },
    "calibration": {"offset_db": 0.0, "set_at": None, "note": "", "calibrated": False},
    "night": {"start": "22:00", "end": "06:00"},
    "clips": {
        "enabled": False,          # 기본 끔: 소리 녹음은 반드시 직접 켜야 함
        "pre_s": 3.0, "post_s": 3.0, "max_s": 30.0,
        "retention_days": 14,
        "lowpass_hz": 500,         # 대화 내용을 알아듣기 어렵게 저역만 남김 (0 = 끔)
        "skip_speech": True,       # 말소리처럼 보이면 저장하지 않음 (휴리스틱, 완전하지 않음)
    },
    "recognition": {"enabled": True},
    "detectors": {
        "knock": {"enabled": True, "min_hits": 3, "cooldown_s": 10.0, "notify": True},
        "beep": {"enabled": True, "cooldown_s": 60.0, "notify": False},
    },
    "notify": {
        "console": True,
        "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": "", "token_env": "NTFY_TOKEN",
                 "priority": 3},
        "sidekick": {"enabled": False, "url": "http://127.0.0.1:8765", "token_env": "SIDEKICK_TOKEN",
                     "module": "sound-watch"},
    },
}


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Paths:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.config = os.path.join(data_dir, "config.json")
        self.templates = os.path.join(data_dir, "templates.json")
        self.db = os.path.join(data_dir, "soundwatch.db")
        self.clips = os.path.join(data_dir, "clips")
        self.reports = os.path.join(data_dir, "reports")


def load_config(path: str) -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = deep_merge(cfg, json.load(f))
    return cfg


def save_config(path: str, cfg: Dict[str, Any]):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def ensure_token(cfg: Dict[str, Any]) -> bool:
    """토큰이 없으면 무작위로 생성. 새로 만들었으면 True."""
    if not cfg["server"].get("token"):
        cfg["server"]["token"] = secrets.token_urlsafe(24)
        return True
    return False


def tz_of(cfg: Dict[str, Any]) -> dt.timezone:
    return dt.timezone(dt.timedelta(minutes=int(cfg.get("utc_offset_minutes", 540))))


def parse_hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


class Calibration:
    """A-가중 dBFS <-> 표시 dB. 표시 dB = dBFS_A + NOMINAL_OFFSET_DB + offset_db."""

    def __init__(self, offset_db: float = 0.0, calibrated: bool = False, set_at: Optional[str] = None,
                 note: str = ""):
        self.offset_db = float(offset_db)
        self.calibrated = bool(calibrated)
        self.set_at = set_at
        self.note = note

    @staticmethod
    def from_cfg(cfg: Dict[str, Any]) -> "Calibration":
        c = cfg.get("calibration", {})
        return Calibration(c.get("offset_db", 0.0), c.get("calibrated", False), c.get("set_at"), c.get("note", ""))

    def to_db(self, dbfs_a: float) -> float:
        return dbfs_a + NOMINAL_OFFSET_DB + self.offset_db

    def from_db(self, db: float) -> float:
        return db - NOMINAL_OFFSET_DB - self.offset_db

    def label(self) -> str:
        if self.calibrated:
            return "dB(A)상당 (사용자 보정 %+.1f dB 적용, 검증 안 됨)" % self.offset_db
        return "dB(A)상당 (미보정 상대값)"


def set_calibration(cfg: Dict[str, Any], offset_db: float, note: str = "", now: Optional[dt.datetime] = None):
    now = now or dt.datetime.now()
    cfg["calibration"] = {"offset_db": float(offset_db), "set_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                          "note": note, "calibrated": True}
    return cfg
