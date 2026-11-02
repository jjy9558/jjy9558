"""rm -rf 결재 보고: Claude Code PreToolUse 훅.

stdin으로 훅 JSON({"tool_name", "tool_input": {"command"}, ...})을 받는다.
- 위험 패턴에 안 걸리는 명령: 아무것도 출력하지 않고 바로 종료 코드 0 (평소대로 진행)
- 걸리는 명령: 사이드킥 서버에 결재를 올리고 폰의 승인/반려를 기다린다.
  * 승인: JSON {"hookSpecificOutput": {..., "permissionDecision": "allow"}} (allow_style="json")
  * 반려/시간 초과/서버 없음: stderr에 이유 + 종료 코드 2 (deny_style="exit2", 모든 버전에서 차단)
    또는 JSON permissionDecision "deny" (deny_style="json")
  * on_timeout="ask" / fail_open=true: JSON permissionDecision "ask" (Claude Code의 원래 확인 창)

매칭은 일부러 '원문 텍스트' 기준이다. `echo "rm -rf x"`도 걸린다 (오탐 = 한 번 더 물어볼 뿐).
따옴표 안을 빼 주면 `bash -c "rm -rf x"`, `eval "..."`처럼 실제로 실행되는 경우를 놓치기 때문.
이건 보조 안전장치지 샌드박스가 아니다: `$RM -rf`, 스크립트 파일 안의 삭제 등은 못 잡는다.
"""
import collections
import json
import re
import sys
import time

from .client import (ServerError, ServerUnavailable, SidekickClient, load_client_config,
                     resolve_config_path, server_token, server_url)

_SEG = r"[^;&|\n]*"  # 같은 명령 구간 (; && || | 줄바꿈에서 끊음)

DEFAULT_PATTERNS = [
    {"label": "강제 재귀 삭제", "pattern": r"(?<![\w.-])rm(?=\s)" + _SEG + r"\s(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive)(?![\w-])"},
    {"label": "git 강제 푸시", "pattern": r"\bgit\b" + _SEG + r"\spush\b" + _SEG +
     r"\s(?:--force(?:-with-lease|-if-includes)?(?![\w-])|-[A-Za-z]*f[A-Za-z]*(?![\w-])|\+[\w./-]+)"},
    {"label": "git reset --hard", "pattern": r"\bgit\b" + _SEG + r"\sreset\b" + _SEG + r"\s--hard\b"},
    {"label": "git clean", "pattern": r"\bgit\b" + _SEG + r"\sclean\b" + _SEG + r"\s(?:-[A-Za-z]*f|--force\b)"},
    {"label": "DB 테이블 삭제", "pattern": r"(?i)\b(?:drop\s+(?:table|database|schema)|truncate\s+table)\b"},
    {"label": "윈도우 강제 삭제", "pattern": r"(?i)(?<![\w-])(?:del|erase|rd|rmdir)\b" + _SEG + r"\s/s\b"},
    {"label": "PowerShell 재귀 삭제", "pattern": r"(?i)(?<![\w-])(?:remove-item|ri|del|erase|rd|rmdir)\b" + _SEG + r"\s-r[a-z]*\b"},
    {"label": "find 삭제", "pattern": r"\bfind\b" + _SEG + r"\s-delete\b"},
    {"label": "rmtree", "pattern": r"\bshutil\.rmtree\b"},
    {"label": "디스크 포맷", "pattern": r"(?i)(?<![\w-])(?:mkfs(?:\.\w+)?(?![\w-])|format(?:\.com)?\s+[a-z]:|diskpart\b)"},
    {"label": "디스크 덮어쓰기", "pattern": r"\bdd\b" + _SEG + r"\bof=/dev/"},
]

Decision = collections.namedtuple("Decision", "action reason")  # action: pass | allow | deny | ask


def compile_patterns(acfg, warn=None):
    base = acfg.get("risky_patterns")
    items = list(DEFAULT_PATTERNS if base is None else base) + list(acfg.get("extra_patterns") or [])
    out = []
    for it in items:
        if isinstance(it, str):
            it = {"pattern": it, "label": "위험 명령"}
        try:
            out.append((it.get("label") or "위험 명령", re.compile(it["pattern"])))
        except (re.error, KeyError, TypeError) as e:
            if warn:
                warn("sidekick hook: 잘못된 패턴 무시: %r (%s)" % (it, e))
    return out


def match_risky(command, patterns):
    """걸린 패턴 라벨 목록 (중복 제거, 순서 유지). 줄 이어쓰기(\\ + 줄바꿈)는 한 줄로 본다."""
    text = re.sub(r"\\\r?\n", " ", command or "")
    labels = []
    for label, rx in patterns:
        if rx.search(text) and label not in labels:
            labels.append(label)
    return labels


def extract_command(hook_input, tools):
    if not isinstance(hook_input, dict):
        return None
    if hook_input.get("hook_event_name") not in (None, "PreToolUse"):
        return None
    if hook_input.get("tool_name") not in tools:
        return None
    ti = hook_input.get("tool_input") or {}
    cmd = ti.get("command") if isinstance(ti, dict) else None
    return cmd if isinstance(cmd, str) else None


def evaluate(hook_input, acfg, client, labels, command, clock=time.monotonic, sleep=time.sleep):
    """위험 명령으로 판정된 뒤의 흐름: 결재 요청 -> 기다림 -> Decision."""
    timeout = float(acfg.get("timeout_seconds", 45))
    ti = hook_input.get("tool_input") if isinstance(hook_input, dict) else {}
    ti = ti if isinstance(ti, dict) else {}
    label = ", ".join(labels)
    unreachable = Decision("ask", "사이드킥 서버에 연결할 수 없어 Claude Code 확인 창으로 넘깁니다.") \
        if acfg.get("fail_open") else \
        Decision("deny", "사이드킥: 서버에 연결할 수 없어 위험 명령(%s)을 막았습니다. "
                         "사이드킥 서버를 켜거나 사용자에게 직접 확인받으세요." % label)
    deadline = clock() + timeout
    try:
        req = client.request_approval({
            "command": command, "label": label, "description": str(ti.get("description") or ""),
            "tool": str(hook_input.get("tool_name") or ""), "cwd": str(hook_input.get("cwd") or ""),
            "timeout_seconds": timeout})
    except ServerUnavailable:
        return unreachable
    except ServerError as e:
        if e.code == 401:
            return Decision(unreachable.action, "사이드킥: 토큰이 틀려 결재를 올리지 못했습니다. " + unreachable.reason)
        return Decision("deny", "사이드킥: 결재 요청이 거절됐습니다 (%s). 위험 명령(%s)을 막았습니다." % (e, label))
    rid = req.get("id")
    status = req.get("status", "pending")
    failures = 0
    while status == "pending":
        left = deadline - clock()
        if left <= 0:
            break
        try:
            r = client.wait_approval(rid, min(25.0, max(1.0, left)))
            status = r.get("status", "pending")
            failures = 0
        except (ServerUnavailable, ServerError):
            failures += 1
            if failures >= 3:
                return unreachable
            sleep(min(1.0, max(0.0, deadline - clock())))
    if status == "allow":
        return Decision("allow", "사이드킥: 사장님이 폰에서 승인했습니다 (%s)." % label)
    if status == "deny":
        return Decision("deny", "사이드킥: 사장님이 폰에서 반려했습니다 (%s). 이 명령은 실행하지 말고, "
                                "다른 방법을 제안하거나 사용자에게 물어보세요." % label)
    # pending(시간 초과) 또는 expired
    if acfg.get("on_timeout") == "ask":
        return Decision("ask", "사이드킥: 폰 결재가 %d초 안에 오지 않아 직접 확인을 요청합니다." % timeout)
    return Decision("deny", "사이드킥: 폰 결재가 %d초 안에 오지 않아 위험 명령(%s)을 막았습니다 "
                            "(on_timeout=deny)." % (timeout, label))


def emit(decision, acfg, out, err):
    """Decision -> Claude Code 훅 출력. 종료 코드를 돌려준다."""
    def as_json(kind):
        out.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": kind,
            "permissionDecisionReason": decision.reason}}, ensure_ascii=False) + "\n")
        return 0
    if decision.action == "pass":
        return 0
    if decision.action == "allow":
        return as_json("allow") if acfg.get("allow_style", "json") == "json" else 0
    if decision.action == "ask":
        return as_json("ask")
    if acfg.get("deny_style", "exit2") == "json":
        return as_json("deny")
    err.write(decision.reason + "\n")
    return 2


def run(raw, acfg_loader, client_factory, out, err, clock=time.monotonic):
    """훅 본체. 위험 명령으로 판정된 이후 생긴 예외는 전부 '막음'(종료 코드 2)."""
    try:
        cfg = acfg_loader()
        acfg = cfg["approval"]
    except Exception as e:  # 설정이 깨져도 기본 패턴으로 판단한다
        from .config import DEFAULTS
        cfg, acfg = None, dict(DEFAULTS["approval"])
        err.write("sidekick hook: 설정을 읽지 못해 기본값 사용 (%s)\n" % e)
    if not acfg.get("enabled", True):
        return 0
    patterns = compile_patterns(acfg, warn=lambda m: err.write(m + "\n"))
    try:
        hook_input = json.loads(raw or "{}")
        command = extract_command(hook_input, acfg.get("tools") or ["Bash"])
    except ValueError:
        # JSON이 깨졌으면 무엇이 실행될지 모른다: 원문 전체를 검사해서 위험하면 막는다
        if match_risky(raw, patterns):
            err.write("사이드킥: 훅 입력을 해석하지 못했고 위험 패턴이 보여 막았습니다.\n")
            return 2
        return 0
    if not command:
        return 0
    labels = match_risky(command, patterns)
    if not labels:
        return 0
    try:
        client = client_factory(cfg, acfg)
        decision = evaluate(hook_input, acfg, client, labels, command, clock=clock)
        return emit(decision, acfg, out, err)
    except Exception as e:
        err.write("사이드킥: 결재 처리 중 오류(%s: %s)로 위험 명령(%s)을 막았습니다.\n"
                  % (type(e).__name__, e, ", ".join(labels)))
        return 2


def main(config_path=None, stdin=None, stdout=None, stderr=None):
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    try:
        raw = stdin.buffer.read().decode("utf-8", "replace") if hasattr(stdin, "buffer") else stdin.read()
    except Exception:
        raw = ""
    path = resolve_config_path(config_path)

    def loader():
        return load_client_config(path)

    def factory(cfg, acfg):
        if cfg is None:
            raise ServerUnavailable("설정 없음")
        return SidekickClient(server_url(cfg, override=acfg.get("url") or None), server_token(cfg), timeout=10)

    return run(raw, loader, factory, stdout, stderr)
