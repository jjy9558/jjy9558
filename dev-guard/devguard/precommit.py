"""git pre-commit 훅 진입점 (.env 커밋 도둑 경보)."""
from __future__ import annotations

import os
from typing import Mapping, Optional

from devguard import scanner
from devguard.common import eprint
from devguard.config import load_config


def run(repo: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> int:
    """0 = 통과, 1 = 차단. 내부 오류는 경고만 하고 통과(커밋을 막지 않음)."""
    env = os.environ if env is None else env
    if env.get("DEVGUARD_SKIP") == "1":
        eprint("[devguard] DEVGUARD_SKIP=1 -> 비밀 검사를 건너뜁니다.")
        return 0
    repo = repo or os.getcwd()
    try:
        cfg = load_config(repo)
        findings = scanner.scan_staged(repo, cfg)
    except Exception as exc:  # noqa: BLE001 - 훅이 커밋을 망가뜨리면 안 된다
        eprint("[devguard] 검사 중 오류(%s) -> 검사를 건너뜁니다." % exc)
        return 0
    if not findings:
        return 0
    eprint(scanner.format_report(findings, int(cfg["secrets"].get("max_findings_shown", 20))))
    return 1
