"""금요일 배포 차단기 (git pre-push).

pre-push 훅은 stdin 으로 `<local ref> <local sha> <remote ref> <remote sha>` 줄을 받는다.
git push 자체에는 임의 옵션을 달 수 없으므로, 우회는
  - 환경변수  DEVGUARD_FRIDAY_OK=1 git push ...
  - 래퍼      python -m devguard push --i-know ...
두 가지로 제공한다.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import os
import subprocess
import sys
from typing import List, Mapping, Optional, Sequence, Tuple

from devguard.common import eprint, run_git
from devguard.config import load_config

ZERO_SHA = "0" * 40
_DAY_NAMES = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6,
}
_KO_DAY = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

MESSAGES = [
    "{day} {time}에 {target} 푸시라니... 주말을 PagerDuty 에게 저당 잡히실 생각이세요?",
    "배포는 금요일에 하는 게 아닙니다. 전설에 따르면 {day} 오후 {target} 에 푸시한 자의 주말은 장애가 가져갔다고 합니다.",
    "{target} 푸시 차단! 월요일의 당신이 고마워할 거예요. 지금의 당신은 퇴근하세요.",
    "{day} {time}. 이 시각에 {target} 으로 배포하는 건 '내 주말 + 팀원 주말'을 건 올인입니다. 칩을 거두세요.",
]


def parse_days(values: Sequence[object]) -> List[int]:
    out: List[int] = []
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and 0 <= v <= 6:
            out.append(v)
        elif isinstance(v, str):
            key = v.strip().lower()
            key = key[:3] if key[:3] in _DAY_NAMES else key
            if key in _DAY_NAMES:
                out.append(_DAY_NAMES[key])
            elif key.isdigit() and 0 <= int(key) <= 6:
                out.append(int(key))
    return out


def parse_minutes(value: object, default: int) -> int:
    """'15:00' / 15 / 15.5 / '24:00' -> 하루 중 분."""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(round(float(value) * 60))
        if isinstance(value, str) and ":" in value:
            h, m = value.strip().split(":", 1)
            return int(h) * 60 + int(m)
        if isinstance(value, str):
            return int(round(float(value) * 60))
    except Exception:
        pass
    return default


def is_blocked_time(now: _dt.datetime, fcfg: dict) -> bool:
    wd = now.weekday()
    if wd in parse_days(fcfg.get("all_day_days", [])):
        return True
    if wd not in parse_days(fcfg.get("days", ["fri"])):
        return False
    minutes = now.hour * 60 + now.minute
    start = parse_minutes(fcfg.get("after", "15:00"), 15 * 60)
    end = parse_minutes(fcfg.get("before", "24:00"), 24 * 60)
    return start <= minutes < end


def match_any(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def parse_pre_push_stdin(text: str) -> List[Tuple[str, str, str, str]]:
    refs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4:
            refs.append((parts[0], parts[1], parts[2], parts[3]))
    return refs


def protected_targets(refs: Sequence[Tuple[str, str, str, str]], fcfg: dict) -> List[str]:
    """푸시 대상 중 보호 대상인 브랜치/태그 이름 목록."""
    hit: List[str] = []
    branches = fcfg.get("branches", [])
    tags = fcfg.get("tags", [])
    for _l, _ls, remote_ref, _rs in refs:
        if remote_ref.startswith("refs/heads/"):
            name = remote_ref[len("refs/heads/"):]
            if match_any(name, branches):
                hit.append(name)
        elif remote_ref.startswith("refs/tags/"):
            name = remote_ref[len("refs/tags/"):]
            if tags and match_any(name, tags):
                hit.append("tag:" + name)
    return hit


def pick_message(now: _dt.datetime, targets: Sequence[str]) -> str:
    tmpl = MESSAGES[now.toordinal() % len(MESSAGES)]
    return tmpl.format(day=_KO_DAY[now.weekday()], time="%02d:%02d" % (now.hour, now.minute),
                       target=", ".join(targets))


def format_block_message(now: _dt.datetime, targets: Sequence[str]) -> str:
    return "\n".join([
        "",
        "=" * 64,
        " [devguard] 금요일 배포 차단기 발동",
        "=" * 64,
        " " + pick_message(now, targets),
        "",
        " 그래도 꼭 밀어야 한다면 (책임은 본인에게):",
        "   macOS/Linux/Git Bash :  DEVGUARD_FRIDAY_OK=1 git push ...",
        "   PowerShell           :  $env:DEVGUARD_FRIDAY_OK=1; git push ...",
        "   래퍼                 :  python -m devguard push --i-know ...",
        "",
    ])


def _parse_now(text: Optional[str]) -> Optional[_dt.datetime]:
    """DEVGUARD_NOW=2025-01-03T16:00 (시험/진단용 가짜 시계)."""
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def current_branch(repo: Optional[str]) -> Optional[str]:
    try:
        out = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).decode().strip()
        return out if out and out != "HEAD" else None
    except Exception:
        return None


def check(now: _dt.datetime, refs: Sequence[Tuple[str, str, str, str]], fcfg: dict) -> List[str]:
    """차단해야 할 대상 이름 목록 (빈 리스트면 통과). 순수 함수."""
    if not is_blocked_time(now, fcfg):
        return []
    return protected_targets(refs, fcfg)


def run(stdin_text: str, i_know: bool = False, now: Optional[_dt.datetime] = None,
        env: Optional[Mapping[str, str]] = None, repo: Optional[str] = None,
        cfg: Optional[dict] = None) -> int:
    env = os.environ if env is None else env
    try:
        if i_know or env.get("DEVGUARD_FRIDAY_OK") == "1":
            return 0
        now = now or _parse_now(env.get("DEVGUARD_NOW")) or _dt.datetime.now()
        cfg = cfg or load_config(repo or os.getcwd())
        fcfg = cfg["friday"]
        refs = parse_pre_push_stdin(stdin_text)
        if not refs:  # 수동 실행 등: 현재 브랜치로 대체
            br = current_branch(repo)
            if br:
                refs = [("refs/heads/" + br, "x", "refs/heads/" + br, "x")]
        blocked = check(now, refs, fcfg)
        if not blocked:
            return 0
        eprint(format_block_message(now, blocked))
        return 1
    except Exception as exc:  # noqa: BLE001
        eprint("[devguard] 금요일 검사 중 오류(%s) -> 통과시킵니다." % exc)
        return 0


def push_wrapper(args: Sequence[str]) -> int:
    """python -m devguard push [--i-know] <git push 인자...>"""
    rest = [a for a in args if a != "--i-know"]
    env = dict(os.environ)
    if len(rest) != len(args):
        env["DEVGUARD_FRIDAY_OK"] = "1"
    return subprocess.call(["git", "push"] + rest, env=env)
