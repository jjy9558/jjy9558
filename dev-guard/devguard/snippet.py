"""`settings-snippet`: .claude/settings.json 에 붙여 넣을 hooks 블록을 만든다 (자동 수정 없음)."""
from __future__ import annotations

import difflib
import json
import os
import sys
from typing import Any, Dict, List, Optional

from devguard.githooks import PROJECT_ROOT

STRIKE_MATCHER = "Bash|Edit|Write|MultiEdit"


def _cmd(python: str, launcher: str, *args: str) -> str:
    def q(s: str) -> str:
        s = s.replace("\\", "/")
        return '"%s"' % s if (" " in s or "(" in s) else s
    return " ".join([q(python), q(launcher)] + list(args))


SIDEKICK_MATCHER = "Bash|PowerShell"   # s21-sidekick README 의 예시와 같은 값
SIDEKICK_TIMEOUT = 60                  # 폰 결재를 기다리는 시간이라 길다
NAGGER_TIMEOUT = 15


class SettingsError(Exception):
    """settings.json 을 읽거나 병합할 수 없을 때 (파일은 건드리지 않았다)."""


def find_nagger_hook() -> Optional[str]:
    """형제 폴더 life-nagger 에 Claude Code 훅 스크립트가 있으면 경로를 돌려준다 (현재는 없음)."""
    base = os.path.join(os.path.dirname(PROJECT_ROOT), "life-nagger")
    for rel in ("hooks/claude_stop.py", "hooks/claude_hook.py", "hooks/claude_pretooluse.py"):
        cand = os.path.join(base, *rel.split("/"))
        if os.path.isfile(cand):
            return cand
    return None


def build(python: Optional[str] = None, launcher: Optional[str] = None,
          only: Optional[List[str]] = None, stop: bool = True,
          sidekick: Optional[str] = None, nagger: Optional[str] = None) -> Dict[str, Any]:
    """훅 순서(PreToolUse): 위험 명령 결재(sidekick) -> strike -> 나머지.
    nagger 훅은 Stop 이벤트의 지갑 한 줄 뒤에 붙는다."""
    python = python or sys.executable or "python"
    launcher = launcher or os.path.join(PROJECT_ROOT, "devguard_run.py")
    only = only or ["strike", "wallet"]
    hooks: Dict[str, Any] = {}
    if sidekick:
        hooks["PreToolUse"] = [{"matcher": SIDEKICK_MATCHER, "hooks": [
            {"type": "command", "command": _cmd(python, sidekick), "timeout": SIDEKICK_TIMEOUT}]}]
    if "strike" in only:
        strike_cmd = _cmd(python, launcher, "strike")
        entry = {"type": "command", "command": strike_cmd, "timeout": 10}
        hooks.setdefault("PreToolUse", []).append({"matcher": STRIKE_MATCHER, "hooks": [dict(entry)]})
        hooks["PostToolUse"] = [{"matcher": STRIKE_MATCHER, "hooks": [dict(entry)]}]
        # 실패한 Bash 가 PostToolUse 대신 이 이벤트로 오는 버전용. 없는 이벤트라 오류가 나면 이 항목만 지우세요.
        hooks["PostToolUseFailure"] = [{"matcher": "Bash", "hooks": [dict(entry)]}]
    if "wallet" in only and stop:
        hooks["Stop"] = [{"hooks": [{"type": "command",
                                     "command": _cmd(python, launcher, "wallet", "stop-hook"),
                                     "timeout": 15}]}]
    if nagger:
        hooks.setdefault("Stop", []).append({"hooks": [
            {"type": "command", "command": _cmd(python, nagger), "timeout": NAGGER_TIMEOUT}]})
    return {"hooks": hooks}


# ----------------------------------------------------------------------------
# --merge: 기존 settings.json 에 중복 없이 병합
# ----------------------------------------------------------------------------


def _commands(groups: List[Any]) -> set:
    out = set()
    for g in groups:
        if isinstance(g, dict) and isinstance(g.get("hooks"), list):
            for h in g["hooks"]:
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    out.add(h["command"])
    return out


def merge_settings(existing: Any, snippet: Dict[str, Any]) -> Dict[str, Any]:
    """existing 은 건드리지 않고 병합된 새 dict 를 돌려준다.

    - 알 수 없는 키와 기존 훅은 그대로 둔다.
    - 같은 이벤트 안에 같은 command 가 이미 있으면 추가하지 않는다 (몇 번을 돌려도 결과 동일).
    - 새로 들어갈 PreToolUse 그룹은 기존 항목보다 앞에 둔다 (결재 -> strike -> 나머지)."""
    if not isinstance(existing, dict):
        raise SettingsError("settings.json 최상위가 JSON 객체가 아닙니다")
    result = json.loads(json.dumps(existing))
    hooks = result.get("hooks")
    if hooks is None:
        hooks = result["hooks"] = {}
    if not isinstance(hooks, dict):
        raise SettingsError('"hooks" 가 객체가 아닙니다')
    for event, groups in snippet.get("hooks", {}).items():
        cur = hooks.get(event)
        if cur is None:
            cur = []
        if not isinstance(cur, list):
            raise SettingsError('hooks.%s 가 배열이 아닙니다' % event)
        have = _commands(cur)
        fresh: List[Any] = []
        for g in groups:
            missing = [h for h in g["hooks"] if h.get("command") not in have]
            if missing:
                have.update(h.get("command") for h in missing)
                fresh.append(dict(g, hooks=missing))
        if not fresh and event not in hooks:
            continue
        hooks[event] = (fresh + cur) if event == "PreToolUse" else (cur + fresh)
    return result


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def read_settings(path: str) -> Any:
    """없거나 빈 파일이면 {}. 깨진 JSON 이면 SettingsError."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise SettingsError("읽을 수 없습니다: %s" % exc)
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError as exc:
        raise SettingsError("올바른 JSON 이 아닙니다 (파일은 수정하지 않았습니다): %s" % exc)


def _backup_path(path: str) -> str:
    cand, n = path + ".bak", 1
    while os.path.exists(cand):
        cand = "%s.bak.%d" % (path, n)
        n += 1
    return cand


def merge_file(path: str, snippet: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """path 하나만 읽고 쓴다. 변경이 있을 때만 .bak 을 먼저 만든 뒤 임시 파일 -> 교체로 쓴다.
    반환: {"changed": bool, "diff": str, "backup": Optional[str]}"""
    existing = read_settings(path)
    merged = merge_settings(existing, snippet)
    exists = os.path.exists(path)
    old_text = ""
    if exists:
        with open(path, "r", encoding="utf-8-sig") as f:
            old_text = f.read()
    new_text = _dump(merged)
    changed = merged != existing or not exists
    diff = "".join(difflib.unified_diff(old_text.splitlines(True), new_text.splitlines(True),
                                        fromfile=path + " (현재)", tofile=path + " (병합 후)"))
    info: Dict[str, Any] = {"changed": changed, "diff": diff if changed else "", "backup": None}
    if dry_run or not changed:
        return info
    target = os.path.realpath(path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    if exists:
        bak = _backup_path(target)
        with open(target, "rb") as src, open(bak, "wb") as dst:
            dst.write(src.read())
        info["backup"] = bak
    tmp = target + ".devguard-tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return info


NOTES = """\
# [devguard] 위 JSON 의 "hooks" 블록을 ~/.claude/settings.json (전체 적용) 또는
# <프로젝트>/.claude/settings.json (프로젝트 한정) 에 직접 붙여 넣으세요. 자동으로 수정하지 않습니다.
# 이미 "hooks" 가 있다면 같은 이벤트 배열에 항목만 추가하세요.
# PostToolUseFailure 가 인식되지 않는 버전이면 그 항목만 삭제해도 됩니다.
# 붙여 넣은 뒤 Claude Code 에서 /hooks 로 등록 확인, 검증 방법은 README 의 '에이전트 파업 검증' 참고.
"""


def render(python: Optional[str] = None, launcher: Optional[str] = None,
           only: Optional[List[str]] = None, stop: bool = True,
           sidekick: Optional[str] = None, nagger: Optional[str] = None) -> str:
    return json.dumps(build(python, launcher, only, stop, sidekick, nagger), ensure_ascii=False, indent=2)
