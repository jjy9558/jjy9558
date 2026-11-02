"""`settings-snippet`: .claude/settings.json 에 붙여 넣을 hooks 블록을 만든다 (자동 수정 없음)."""
from __future__ import annotations

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


def build(python: Optional[str] = None, launcher: Optional[str] = None,
          only: Optional[List[str]] = None, stop: bool = True) -> Dict[str, Any]:
    python = python or sys.executable or "python"
    launcher = launcher or os.path.join(PROJECT_ROOT, "devguard_run.py")
    only = only or ["strike", "wallet"]
    hooks: Dict[str, Any] = {}
    if "strike" in only:
        strike_cmd = _cmd(python, launcher, "strike")
        entry = {"type": "command", "command": strike_cmd, "timeout": 10}
        hooks["PreToolUse"] = [{"matcher": STRIKE_MATCHER, "hooks": [dict(entry)]}]
        hooks["PostToolUse"] = [{"matcher": STRIKE_MATCHER, "hooks": [dict(entry)]}]
        # 실패한 Bash 가 PostToolUse 대신 이 이벤트로 오는 버전용. 없는 이벤트라 오류가 나면 이 항목만 지우세요.
        hooks["PostToolUseFailure"] = [{"matcher": "Bash", "hooks": [dict(entry)]}]
    if "wallet" in only and stop:
        hooks["Stop"] = [{"hooks": [{"type": "command",
                                     "command": _cmd(python, launcher, "wallet", "stop-hook"),
                                     "timeout": 15}]}]
    return {"hooks": hooks}


NOTES = """\
# [devguard] 위 JSON 의 "hooks" 블록을 ~/.claude/settings.json (전체 적용) 또는
# <프로젝트>/.claude/settings.json (프로젝트 한정) 에 직접 붙여 넣으세요. 자동으로 수정하지 않습니다.
# 이미 "hooks" 가 있다면 같은 이벤트 배열에 항목만 추가하세요.
# PostToolUseFailure 가 인식되지 않는 버전이면 그 항목만 삭제해도 됩니다.
# 붙여 넣은 뒤 Claude Code 에서 /hooks 로 등록 확인, 검증 방법은 README 의 '에이전트 파업 검증' 참고.
"""


def render(python: Optional[str] = None, launcher: Optional[str] = None,
           only: Optional[List[str]] = None, stop: bool = True) -> str:
    return json.dumps(build(python, launcher, only, stop), ensure_ascii=False, indent=2)
