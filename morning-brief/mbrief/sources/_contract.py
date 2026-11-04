"""형제 프로젝트의 JSON 계약(schema v1) 명령을 부르는 공통 도우미.

어댑터는 config ``mode`` 로 읽는 방식을 고른다.
  auto(기본)  JSON 명령을 먼저 쓰고, 실패하면 원본 파일을 직접 읽는 기존 방식으로 되돌아간다
  json        JSON 명령만 쓴다 (실패하면 오류)
  direct      원본 파일만 직접 읽는다 (명령을 부르지 않음)

명령은 인자 목록 + shell=False + timeout 으로만 실행한다. 어떤 방식이 쓰였는지는 ctx.notes 를 거쳐
브리핑 ``sources.<이름>.note`` 에 남는다(계약 파일의 키는 그대로).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

from ..model import Context

log = logging.getLogger("mbrief")

MODES = ("auto", "json", "direct")
JSON_DEFAULTS = {"mode": "auto", "command": [], "cwd": "", "timeout": 20}
SUPPORTED_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[3]       # morning-brief 의 부모 폴더 (형제 프로젝트가 있는 곳)


class BadConfig(Exception):
    """설정 값이 잘못됨(모드 이름, command 모양). 되돌아가지 않고 오류로 드러낸다."""


class ContractUnavailable(Exception):
    """JSON 계약 경로를 못 쓴다(명령 실패, 깨진 JSON, 모르는 버전 등). auto 에서는 직접 읽기로 넘어간다."""


def get_mode(cfg: dict) -> str:
    mode = str(cfg.get("mode") or "auto").strip().lower()
    if mode not in MODES:
        raise BadConfig("mode 는 auto|json|direct 중 하나여야 해요: %r" % cfg.get("mode"))
    return mode


def sibling_dir(ctx: Context, project: str) -> Path | None:
    for base in (ctx.base_dir.parent, REPO_ROOT):
        p = base / project
        if p.is_dir():
            return p
    return None


def _command(cfg: dict, pkg: str) -> list[str]:
    cmd = cfg.get("command") or []
    if not cmd:
        return [sys.executable, "-m", pkg]
    if not isinstance(cmd, (list, tuple)) or not all(isinstance(c, str) and c for c in cmd):
        raise BadConfig("command 는 문자열 목록이어야 해요 (예: [\"python3\", \"-m\", \"%s\"])" % pkg)
    return list(cmd)


def run_json(cfg: dict, ctx: Context, *, project: str, pkg: str, args: Sequence[str],
             env_extra: dict | None = None, date_key: str = "generated_for") -> dict:
    """형제 명령을 돌려 검증된 JSON 객체를 돌려준다. 못 쓰면 ContractUnavailable."""
    cmd = _command(cfg, pkg) + [str(a) for a in args]
    cwd = None
    if cfg.get("cwd"):
        cwd = ctx.path(cfg["cwd"])
    elif not cfg.get("command"):
        cwd = sibling_dir(ctx, project)        # 없으면 설치된 패키지에 맡긴다
    env = {str(k): str(v) for k, v in dict(ctx.env).items()}
    env.update({k: str(v) for k, v in (env_extra or {}).items() if v})
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, shell=False, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=float(cfg.get("timeout") or 20))
    except subprocess.TimeoutExpired:
        raise ContractUnavailable("명령 시간 초과")
    except (OSError, ValueError) as e:
        raise ContractUnavailable("명령을 실행할 수 없어요 (%s)" % type(e).__name__)
    if proc.returncode != 0:
        raise ContractUnavailable("명령 실패(종료 코드 %d)" % proc.returncode)
    try:
        obj = json.loads(proc.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ContractUnavailable("출력이 JSON 이 아니에요")
    if not isinstance(obj, dict):
        raise ContractUnavailable("출력 최상위가 객체가 아니에요")
    ver = obj.get("version")
    if ver != SUPPORTED_VERSION or isinstance(ver, bool):
        raise ContractUnavailable("모르는 스키마 버전: %r" % (ver,))
    if obj.get(date_key) != ctx.today.isoformat():
        raise ContractUnavailable("기준일이 달라요 (%s != %s)" % (obj.get(date_key), ctx.today.isoformat()))
    return obj


def collect_with_mode(name: str, cfg: dict, ctx: Context, via_json: Callable[[], list],
                      via_direct: Callable[[], list]) -> list:
    """mode 에 따라 JSON/직접 읽기를 고르고, 쓰인 방식을 ctx.notes[name] 에 남긴다."""
    mode = get_mode(cfg)
    if mode == "direct":
        got = via_direct()
        ctx.notes[name] = "모드: direct"
        return got
    try:
        try:
            got = via_json()
        except (KeyError, TypeError, ValueError, AttributeError) as e:    # 모양이 계약과 다름
            raise ContractUnavailable("형식 불일치 (%s)" % type(e).__name__)
    except ContractUnavailable as e:
        if mode == "json":
            raise
        log.warning("%s: JSON 계약을 쓰지 못해 직접 읽기로 되돌아가요 (%s)", name, e)
        got = via_direct()
        ctx.notes[name] = "모드: direct (json 실패: %s)" % e
        return got
    ctx.notes[name] = "모드: json"
    return got
