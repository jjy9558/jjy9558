"""상태 표 (jjkit textwidth 로 한글 폭을 맞춘다)."""
from __future__ import annotations

from ._jjkit import textwidth
from .procs import UnitStatus


def fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 60:
        return "%d초" % s
    if s < 3600:
        return "%d분" % (s // 60)
    if s < 86400:
        return "%d시간 %d분" % (s // 3600, (s % 3600) // 60)
    return "%d일 %d시간" % (s // 86400, (s % 86400) // 3600)


def render_status(rows: list[UnitStatus]) -> list[str]:
    body = []
    for r in rows:
        state = r.state
        if not r.enabled and not r.running and not r.port_open:
            state = "꺼짐 (비활성)"
        body.append([
            r.name, state, str(r.pid) if r.pid else "-", str(r.port) if r.port else "-",
            r.health.label, fmt_uptime(r.uptime), r.title,
        ])
    lines = textwidth.table(body, ["이름", "상태", "PID", "포트", "건강 확인", "가동", "설명"],
                            right_cols=(2, 3), sep="  ")
    running = sum(1 for r in rows if r.running)
    wanted = sum(1 for r in rows if r.enabled)
    lines.append("")
    lines.append("실행 중 %d개 / 켜기로 한 서비스 %d개" % (running, wanted))
    problems = []
    for r in rows:
        if r.state == "다른 프로그램이 포트 사용":
            problems.append("%s: 포트 %d 를 다른 프로그램이 쓰고 있어요" % (r.name, r.port))
        elif r.running and r.health.state == "auth":
            problems.append("%s: 켜져 있지만 토큰이 안 맞아요 (homelab doctor)" % r.name)
        elif r.running and r.port and not r.port_open:
            problems.append("%s: 프로세스는 있는데 포트가 안 열렸어요 (homelab logs %s)" % (r.name, r.name))
    for p in problems:
        lines.append("주의: " + p)
    return lines
