"""공용 유틸: 콘솔 인코딩, stdin 읽기, git 호출."""
from __future__ import annotations

import subprocess
import sys
from typing import List, Optional, Sequence


def setup_stdio() -> None:
    """Windows 콘솔(cp949 등)에서 한글/기호 출력 때문에 죽지 않게 한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def eprint(*parts: object) -> None:
    try:
        print(*parts, file=sys.stderr)
    except Exception:
        try:
            sys.stderr.write(" ".join(str(p) for p in parts).encode("ascii", "replace").decode() + "\n")
        except Exception:
            pass


def read_stdin_text() -> str:
    """UTF-8 로 stdin 전체를 읽는다. 대화형 터미널이면 기다리지 않고 빈 문자열."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        buf = getattr(sys.stdin, "buffer", None)
        if buf is not None:
            return buf.read().decode("utf-8", "replace")
        return sys.stdin.read()
    except Exception:
        return ""


class GitError(RuntimeError):
    pass


def run_git(args: Sequence[str], cwd: Optional[str] = None, env: Optional[dict] = None,
            check: bool = True) -> bytes:
    """git 을 인자 리스트로 호출한다 (shell=False). stdout 바이트를 돌려준다."""
    proc = subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip() or "git 실패")
    return proc.stdout


def split_csv(value: str) -> List[str]:
    return [p.strip() for p in value.split(",") if p.strip()]
