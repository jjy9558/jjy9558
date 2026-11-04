"""secrets.json: 홈랩이 만든 모든 토큰의 단일 출처.

- POSIX 에서는 권한 600 으로 쓴다. 윈도우에서는 사용자 프로필(``%USERPROFILE%\\.homelab``) 안에 두고
  가능하면 ``icacls`` 로 현재 사용자만 읽게 줄인다(실패해도 계속).
- 토큰은 이미 있으면 다시 만들지 않는다(``--rotate-tokens`` 일 때만 새로).
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ._jjkit.httpkit import generate_token

VERSION = 1
MIN_TOKEN_LEN = 16  # jarvis-hub 가 요구하는 최소 길이

# 이름 -> 설명 (doctor/token show 에서 씀)
TOKEN_NAMES = {
    "sidekick": "s21-sidekick server.token",
    "phone-home": "phone-home 클라이언트 토큰 (폰 로그인용)",
    "object-memory": "object-memory 토큰",
    "voice-journal": "voice-journal 토큰",
    "sound-watch": "sound-watch 토큰",
    "jarvis-main": "jarvis 기기 main (이 PC 워커)",
    "jarvis-s21": "jarvis 기기 s21 (phone-home 이 붙을 때 씀)",
    "jarvis-minipc": "jarvis 기기 minipc (미니PC 워커)",
}
EXTRA_NAMES = {
    "sidekick-emergency-code": "알람 비상 해제 코드 (6자리)",
    "ntfy-topic": "ntfy 토픽 (추측하기 어려운 이름)",
}


class SecretsError(ValueError):
    pass


def empty() -> dict[str, Any]:
    return {"version": VERSION, "tokens": {}, "extras": {}}


def load(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise SecretsError("secrets.json 을 읽을 수 없어요 (%s): %s" % (path, e))
    if not isinstance(raw, dict):
        raise SecretsError("secrets.json 은 JSON 객체여야 해요: %s" % path)
    out = empty()
    for key in ("tokens", "extras"):
        val = raw.get(key, {})
        if not isinstance(val, dict):
            raise SecretsError("secrets.json 의 %s 는 객체여야 해요" % key)
        out[key] = {str(k): str(v) for k, v in val.items()}
    return out


def new_extra(name: str) -> str:
    if name == "sidekick-emergency-code":
        return "%06d" % _secrets.randbelow(1_000_000)
    if name == "ntfy-topic":
        return "homelab-" + _secrets.token_hex(8)
    raise KeyError(name)


def ensure(data: dict[str, Any], token_names: list[str], extra_names: list[str] | None = None,
           *, rotate: bool = False) -> list[str]:
    """없는 토큰을 만든다. 만든(바뀐) 이름 목록을 돌려준다."""
    changed = []
    for n in token_names:
        cur = data["tokens"].get(n, "")
        if rotate or len(cur) < MIN_TOKEN_LEN:
            data["tokens"][n] = generate_token(24)
            changed.append(n)
    for n in extra_names or []:
        if rotate or not data["extras"].get(n):
            data["extras"][n] = new_extra(n)
            changed.append(n)
    return changed


def all_values(data: dict[str, Any]) -> list[str]:
    """화면에 찍기 전에 가릴 값들 (긴 것부터)."""
    vals = [v for v in list(data["tokens"].values()) + list(data["extras"].values()) if v]
    return sorted(set(vals), key=len, reverse=True)


def mask(text: str, data: dict[str, Any]) -> str:
    for v in all_values(data):
        if len(v) >= 6:
            text = text.replace(v, "***")
    return text


def write_private(path: Path, text: str) -> None:
    """임시 파일에 권한 600 으로 쓴 뒤 교체한다 (읽을 수 있는 순간이 없게)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    restrict_windows(path)


def restrict_windows(path: Path) -> bool:
    """윈도우에서 현재 사용자만 접근하게 줄인다(최선 노력). 다른 OS 에서는 아무것도 안 함."""
    if sys.platform != "win32":
        return False
    user = os.environ.get("USERNAME")
    if not user:
        return False
    try:
        r = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", "%s:F" % user],
                           capture_output=True, timeout=10, shell=False)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def save(path: Path, data: dict[str, Any]) -> None:
    write_private(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def permissions_ok(path: Path) -> bool | None:
    """POSIX 에서 그룹/기타 권한이 없으면 True. 윈도우나 파일 없음이면 None."""
    if sys.platform == "win32" or not Path(path).exists():
        return None
    return (os.stat(path).st_mode & 0o077) == 0
