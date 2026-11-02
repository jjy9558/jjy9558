"""git 훅 설치/제거 (기존 훅은 보존하고 체이닝).

생성되는 훅은 `#!/bin/sh` 스크립트다 (Windows 의 Git for Windows 도 sh 로 실행한다).
기존 훅이 있으면 `<훅이름>.devguard-orig` 로 옮겨 두고, 우리 훅이 검사 후 그것도 실행한다.
"""
from __future__ import annotations

import os
import stat
import sys
from typing import List, Optional, Sequence

from devguard.common import GitError, run_git

MARKER = "devguard-managed-hook"
ORIG_SUFFIX = ".devguard-orig"
HOOK_TO_SUBCMD = {"pre-commit": "precommit", "pre-push": "prepush"}
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _posix(p: str) -> str:
    return p.replace("\\", "/")


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def render_hook(hook: str, python: str, launcher: str) -> str:
    sub = HOOK_TO_SUBCMD[hook]
    head = [
        "#!/bin/sh",
        "# %s v1 (%s)" % (MARKER, hook),
        "# 생성: python -m devguard install-git-hooks   /  제거: python -m devguard uninstall-git-hooks",
        'DG_DIR=$(dirname "$0")',
        "DG_PY=%s" % _q(_posix(python)),
        "DG_RUN=%s" % _q(_posix(launcher)),
        'DG_ORIG="$DG_DIR/%s%s"' % (hook, ORIG_SUFFIX),
    ]
    if hook == "pre-push":
        head.append("DG_IN=$(cat)")
        run = 'printf \'%s\\n\' "$DG_IN" | "$DG_PY" "$DG_RUN" ' + sub + ' "$@" || exit $?'
        chain = '  printf \'%s\\n\' "$DG_IN" | "$DG_ORIG" "$@" || exit $?'
    else:
        run = '"$DG_PY" "$DG_RUN" ' + sub + ' "$@" || exit $?'
        chain = '  "$DG_ORIG" "$@" || exit $?'
    body = [
        'if [ -f "$DG_RUN" ] && command -v "$DG_PY" >/dev/null 2>&1; then',
        "  " + run,
        "else",
        '  echo "[devguard] python 또는 devguard_run.py 를 찾지 못해 검사를 건너뜁니다." >&2',
        "fi",
        'if [ -f "$DG_ORIG" ] && [ -x "$DG_ORIG" ]; then',
        chain,
        "fi",
        "exit 0",
    ]
    return "\n".join(head + body) + "\n"


def hooks_dir(repo: str) -> str:
    out = run_git(["rev-parse", "--git-path", "hooks"], cwd=repo).decode("utf-8", "replace").strip()
    return out if os.path.isabs(out) else os.path.normpath(os.path.join(repo, out))


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _write_exec(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:  # LF 고정 (Windows 에서 CRLF 방지)
        f.write(text)
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def install(repo: Optional[str] = None, hooks: Sequence[str] = ("pre-commit", "pre-push"),
            python: Optional[str] = None, launcher: Optional[str] = None) -> List[str]:
    repo = os.path.abspath(repo or os.getcwd())
    python = python or sys.executable or "python"
    launcher = launcher or os.path.join(PROJECT_ROOT, "devguard_run.py")
    try:
        hdir = hooks_dir(repo)
    except GitError as exc:
        raise GitError("git 저장소가 아닙니다: %s (%s)" % (repo, exc))
    os.makedirs(hdir, exist_ok=True)
    msgs: List[str] = []
    for hook in hooks:
        if hook not in HOOK_TO_SUBCMD:
            msgs.append("알 수 없는 훅: %s (건너뜀)" % hook)
            continue
        path = os.path.join(hdir, hook)
        orig = path + ORIG_SUFFIX
        state = "새로 설치"
        if os.path.exists(path):
            existing = _read(path)
            if MARKER in existing:
                state = "이미 설치됨 -> 갱신"
            elif existing.strip():
                if os.path.exists(orig):
                    msgs.append("%s: 기존 훅과 %s 가 모두 있어 중단했습니다. 직접 정리 후 다시 실행하세요." % (hook, os.path.basename(orig)))
                    continue
                os.replace(path, orig)
                state = "기존 훅을 %s 로 보존하고 체이닝" % os.path.basename(orig)
        _write_exec(path, render_hook(hook, python, launcher))
        msgs.append("%s: %s (%s)" % (hook, state, path))
    return msgs


def uninstall(repo: Optional[str] = None, hooks: Sequence[str] = ("pre-commit", "pre-push")) -> List[str]:
    repo = os.path.abspath(repo or os.getcwd())
    hdir = hooks_dir(repo)
    msgs: List[str] = []
    for hook in hooks:
        path = os.path.join(hdir, hook)
        orig = path + ORIG_SUFFIX
        if os.path.exists(path) and MARKER in _read(path):
            os.remove(path)
            if os.path.exists(orig):
                os.replace(orig, path)
                msgs.append("%s: devguard 제거, 기존 훅 복원" % hook)
            else:
                msgs.append("%s: devguard 제거" % hook)
        else:
            msgs.append("%s: devguard 훅이 아니어서 그대로 둠" % hook)
    return msgs
