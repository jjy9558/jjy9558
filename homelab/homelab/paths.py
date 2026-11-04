"""홈랩 폴더 배치와 설정(homelab.json).

``~/.homelab`` 아래에 홈랩이 직접 만드는 것만 둔다::

    homelab.json   홈랩 자신의 선택(비밀 아님)
    secrets.json   토큰 모음(POSIX 권한 600)
    config/        s21-sidekick, jarvis-hub 등 --config 로 받는 설정 파일
    data/          서비스별 데이터 폴더 (sound-watch, jarvis DB, 브리핑 출력 ...)
    tokens/        phone-home 이 token_file 로 읽는 뒤쪽 서비스 토큰
    run/           PID 파일
    logs/          서비스·작업 로그 (크기 기준 회전)

각 프로젝트가 홈에서 직접 찾는 설정(``~/.phone-home/config.json`` 등)은 ``home`` 기준 문서화된 위치에 쓴다.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SETTINGS_VERSION = 1

# 서비스 이름 -> 기본 켜기 여부는 registry 에서 정한다. 여기엔 사용자 선택의 기본값만 둔다.
DEFAULT_SETTINGS: dict[str, Any] = {
    "version": SETTINGS_VERSION,
    "python": "",                      # 비우면 init 을 실행한 파이썬(sys.executable)
    "repo_root": "",                   # 비우면 이 폴더의 부모(저장소 루트)
    "llm_url": "http://127.0.0.1:8080/v1",
    "enabled": [],                     # 비우면 registry 기본 목록
    "ports": {},                       # {"sidekick": 8765, ...} 바꾸고 싶을 때만
    "hub_host": "127.0.0.1",           # 허브가 열리는 주소 (미니PC 쓰면 Tailscale IP)
    "hub_url": "",                     # 워커가 붙을 허브 주소 (비우면 hub_host 로 계산)
    "minipc": False,                   # 미니PC 워커 설정 파일도 만든다
    "sidekick_url_for_minipc": "http://127.0.0.1:8765",
    "wol_mac": "",
    "wol_broadcast": "255.255.255.255",
    "ntfy": False,                     # ntfy 푸시(토픽은 secrets.json 의 무작위 값)
    "ntfy_server": "https://ntfy.sh",
    "wallet_limit_usd": 20,
    "redact_words": [],
    "gm_world": "",
    "appids": [],
    "morning_gate": False,             # 알람 승인 뒤 아침 브리핑 낭독 (sidekick alarm.morning_gate)
    "tailnet_host": "",                # tailscale 안내문에 넣을 호스트 이름 (선택)
    "log_max_mb": 2.0,
    "log_backups": 3,
    "start_timeout": 10.0,
    "schedule_times": {},              # {"morning-brief": "06:50"} 덮어쓰기
}

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


class SettingsError(ValueError):
    pass


def default_root(env: dict[str, str] | None = None, home: Path | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("HOMELAB_DIR"):
        return Path(env["HOMELAB_DIR"]).expanduser()
    return (home or Path.home()) / ".homelab"


def default_repo_root() -> Path:
    # homelab/homelab/paths.py -> 저장소 루트
    return Path(__file__).resolve().parents[2]


class Layout:
    def __init__(self, root: Path | str, home: Path | str | None = None):
        self.root = Path(root)
        self.home = Path(home) if home is not None else Path.home()

    @property
    def settings_path(self) -> Path:
        return self.root / "homelab.json"

    @property
    def secrets_path(self) -> Path:
        return self.root / "secrets.json"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def tokens_dir(self) -> Path:
        return self.root / "tokens"

    @property
    def run_dir(self) -> Path:
        return self.root / "run"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def config(self, name: str) -> Path:
        return self.config_dir / (name + ".json")

    def data(self, name: str) -> Path:
        return self.data_dir / name

    def token_file(self, name: str) -> Path:
        return self.tokens_dir / (name + ".token")

    def pid_file(self, name: str) -> Path:
        return self.run_dir / (name + ".pid")

    def log_file(self, name: str) -> Path:
        return self.logs_dir / (name + ".log")

    # --- 프로젝트들이 홈에서 직접 찾는 문서화된 위치 -----------------------------------
    @property
    def phone_home_config(self) -> Path:
        return self.home / ".phone-home" / "config.json"

    @property
    def objmem_config(self) -> Path:
        return self.home / ".objmem" / "config.json"

    @property
    def vjournal_token(self) -> Path:
        return self.home / ".vjournal" / "token"

    @property
    def til_config(self) -> Path:
        return self.home / ".til-drafter.json"

    @property
    def devguard_home(self) -> Path:
        return self.home / ".devguard"

    @property
    def devguard_config(self) -> Path:
        return self.devguard_home / "config.json"


def load_settings(layout: Layout) -> dict[str, Any]:
    """homelab.json 을 기본값 위에 얹는다. 없으면 기본값."""
    data = dict(DEFAULT_SETTINGS)
    p = layout.settings_path
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise SettingsError("homelab.json 을 읽을 수 없어요 (%s): %s" % (p, e))
        if not isinstance(raw, dict):
            raise SettingsError("homelab.json 은 JSON 객체여야 해요: %s" % p)
        data.update(raw)
    return data


def validate_settings(s: dict[str, Any], known_services: list[str]) -> dict[str, Any]:
    """값을 검사하고 정리한 사본을 돌려준다. 문제는 SettingsError(한국어)."""
    s = dict(s)
    url = str(s.get("llm_url") or "")
    if url and not re.match(r"^https?://[^\s/]+", url):
        raise SettingsError("llm_url 은 http://호스트:포트/v1 형식이어야 해요: %r" % url)
    s["llm_url"] = url.rstrip("/")
    enabled = [str(x) for x in (s.get("enabled") or [])]
    bad = [x for x in enabled if x not in known_services]
    if bad:
        raise SettingsError("알 수 없는 서비스: %s (가능: %s)" % (", ".join(bad), ", ".join(known_services)))
    s["enabled"] = enabled
    ports = s.get("ports") or {}
    if not isinstance(ports, dict):
        raise SettingsError("ports 는 객체여야 해요")
    clean = {}
    for k, v in ports.items():
        if k not in known_services:
            raise SettingsError("ports 에 알 수 없는 서비스: %s" % k)
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise SettingsError("ports.%s 는 숫자여야 해요" % k)
        if not 1 <= n <= 65535:
            raise SettingsError("ports.%s 범위 오류: %s" % (k, v))
        clean[k] = n
    s["ports"] = clean
    mac = str(s.get("wol_mac") or "").strip()
    if mac and not MAC_RE.match(mac):
        raise SettingsError("wol_mac 형식 오류: %r (예: AA:BB:CC:DD:EE:FF)" % mac)
    s["wol_mac"] = mac.replace("-", ":").upper() if mac else ""
    try:
        s["wallet_limit_usd"] = float(s.get("wallet_limit_usd") or 0)
    except (TypeError, ValueError):
        raise SettingsError("wallet_limit_usd 는 숫자여야 해요")
    if s["wallet_limit_usd"] < 0:
        raise SettingsError("wallet_limit_usd 는 0 이상이어야 해요")
    s["redact_words"] = [str(w).strip() for w in (s.get("redact_words") or []) if str(w).strip()]
    ids = []
    for a in s.get("appids") or []:
        try:
            ids.append(int(a))
        except (TypeError, ValueError):
            raise SettingsError("appids 는 숫자 목록이어야 해요: %r" % (a,))
    s["appids"] = ids
    times = s.get("schedule_times") or {}
    if not isinstance(times, dict):
        raise SettingsError("schedule_times 는 객체여야 해요")
    for k, v in times.items():
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(v)):
            raise SettingsError("schedule_times.%s 는 HH:MM 형식이어야 해요: %r" % (k, v))
    try:
        s["log_max_mb"] = float(s.get("log_max_mb") or 2.0)
        s["log_backups"] = int(s.get("log_backups") or 3)
        s["start_timeout"] = float(s.get("start_timeout") or 10.0)
    except (TypeError, ValueError):
        raise SettingsError("log_max_mb/log_backups/start_timeout 는 숫자여야 해요")
    return s


def settings_json(s: dict[str, Any]) -> str:
    return json.dumps(s, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
