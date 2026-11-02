"""에이전트 파업 (Claude Code hooks).

같은 명령이 연속으로 N번 실패하거나, 같은 파일을 A->B->A 식으로 N번 되돌리면
'파업'을 선언하고, 다음 동일 시도를 PreToolUse 에서 거부한다.

하나의 명령(`python -m devguard strike`)이 PreToolUse / PostToolUse / PostToolUseFailure 를
모두 처리한다 (stdin JSON 의 hook_event_name 으로 구분).

출력 규약 (문서화된 형식 기준, README 의 '검증 방법' 참고):
  PreToolUse        -> {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                         "permissionDecision": "deny", "permissionDecisionReason": "..."}}
  PostToolUse       -> {"decision": "block", "reason": "..."}      (action=stop 이면 {"continue": false, "stopReason": ...})
  폴백(output=exit2) -> 종료코드 2 + stderr 메시지
어떤 예외가 나도 훅은 '허용'(종료코드 0, 출력 없음)으로 끝난다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from devguard.config import load_config

EDIT_TOOLS = ("Edit", "Write", "MultiEdit")
MSG_COMMAND = "같은 실패 {n}번째. 파업합니다. 접근 방식을 바꾸세요."
MSG_FILE = "같은 파일을 {n}번 되돌렸습니다. 파업합니다. 접근 방식을 바꾸세요."
HINT = (" (같은 명령을 그대로 재시도하지 말고 원인을 조사하거나 파일을 수정한 뒤 시도하세요."
        " 수동 해제: python -m devguard strike-reset)")

Result = Tuple[int, Optional[Dict[str, Any]], str]  # (exit_code, stdout_json, stderr_text)

# ----------------------------------------------------------------------------
# 정규화 / 실패 판정
# ----------------------------------------------------------------------------

_TRAIL = re.compile(r"\s*\|\s*(?:tail|head|tee)\b[^|;&]*$")


def normalize_command(cmd: str) -> str:
    s = re.sub(r"\s+", " ", cmd or "").strip()
    s = s.replace(" 2>&1", "")
    prev = None
    while prev != s:
        prev = s
        s = _TRAIL.sub("", s)
    return s.rstrip(" ;")[:2000]


def command_key(norm: str) -> str:
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()[:16]


def bash_failed(resp: Any) -> bool:
    """tool_response 로부터 실패 여부 추정. 모르면 False(성공으로 간주).

    Claude Code 버전마다 tool_response 모양이 다를 수 있어 여러 모양을 받아들인다.
    (실패 Bash 는 PostToolUseFailure 이벤트로 오는 버전도 있다 - handle() 참고)
    """
    if resp is None:
        return False
    if isinstance(resp, str):
        m = re.match(r"\s*Exit code (-?\d+)", resp)
        return bool(m and int(m.group(1)) != 0)
    if isinstance(resp, dict):
        if resp.get("interrupted"):
            return False
        for k in ("exit_code", "exitCode", "returncode", "return_code", "code"):
            v = resp.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                return v != 0
        if resp.get("is_error") or resp.get("isError"):
            return True
        if resp.get("success") is False:
            return True
        err = resp.get("error")
        if isinstance(err, str) and err.strip():
            return True
    return False


# ----------------------------------------------------------------------------
# 상태 (세션별 임시 JSON)
# ----------------------------------------------------------------------------


def state_dir(env: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if env is None else env
    return env.get("DEVGUARD_STATE_DIR") or os.path.join(tempfile.gettempdir(), "devguard-strike")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:80] or "unknown"


def state_path(sdir: str, session_id: str, agent_id: str = "") -> str:
    name = _safe(session_id) + (("__" + _safe(agent_id)) if agent_id else "")
    return os.path.join(sdir, name + ".json")


def load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("cmds", {})
            data.setdefault("files", {})
            return data
    except Exception:
        pass
    return {"v": 1, "cmds": {}, "files": {}}


def save_state(path: str, state: Dict[str, Any]) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    # 오래된 상태 파일 정리 (가끔)
    try:
        names = os.listdir(d)
        if len(names) > 200:
            cutoff = time.time() - 3 * 86400
            for n in names:
                p = os.path.join(d, n)
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
    except OSError:
        pass
    # 상태가 무한정 커지지 않게
    for key in ("cmds", "files"):
        items = state.get(key, {})
        if len(items) > 60:
            keep = sorted(items.items(), key=lambda kv: kv[1].get("t", 0), reverse=True)[:40]
            state[key] = dict(keep)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ----------------------------------------------------------------------------
# 핵심 로직
# ----------------------------------------------------------------------------


def _h(s: Any) -> str:
    return hashlib.sha1(str(s).encode("utf-8", "replace")).hexdigest()[:12]


def _norm_path(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _expired(entry: Dict[str, Any], now: float, ttl: float) -> bool:
    return now - entry.get("struck_at", 0) > ttl


def _record_command(state: Dict[str, Any], cmd: str, failed: bool, cfg: dict, now: float) -> Optional[str]:
    """실행 결과를 기록하고, 이번에 파업이 선언되면 메시지를 돌려준다."""
    norm = normalize_command(cmd)
    if not norm:
        return None
    for pat in cfg.get("ignore_regex", []):
        try:
            if re.search(pat, norm):
                return None
        except re.error:
            continue
    key = command_key(norm)
    ent = state["cmds"].setdefault(key, {"cmd": norm[:200], "fails": 0, "struck": False, "denies": 0})
    ent["t"] = now
    if not failed:
        ent.update(fails=0, struck=False, denies=0)
        return None
    ent["fails"] = ent.get("fails", 0) + 1
    thr = int(cfg.get("threshold", 3))
    if ent["fails"] >= thr and not ent.get("struck"):
        ent.update(struck=True, struck_at=now, denies=0)
        return MSG_COMMAND.format(n=thr)
    return None


def _clear_command_strikes(state: Dict[str, Any]) -> None:
    for ent in state["cmds"].values():
        if ent.get("struck"):
            ent.update(struck=False, fails=0, denies=0)


def _record_edit(state: Dict[str, Any], tool: str, tin: Dict[str, Any], cfg: dict, now: float) -> Optional[str]:
    fp = tin.get("file_path") or tin.get("path")
    if not isinstance(fp, str) or not fp:
        return None
    key = _norm_path(fp)
    ent = state["files"].setdefault(key, {"edits": [], "writes": [], "flips": 0, "struck": False, "denies": 0})
    ent["t"] = now
    flips = 0
    if tool == "Edit":
        pairs = [(tin.get("old_string"), tin.get("new_string"))]
    elif tool == "MultiEdit":
        pairs = [(e.get("old_string"), e.get("new_string")) for e in tin.get("edits", []) if isinstance(e, dict)]
    else:
        pairs = []
    for old, new in pairs:
        if old is None or new is None or old == new:
            continue
        o, n = _h(old), _h(new)
        if any(p[0] == n and p[1] == o for p in ent["edits"]):
            flips += 1
        ent["edits"].append([o, n])
    ent["edits"] = ent["edits"][-30:]
    if tool == "Write" and "content" in tin:
        c = _h(tin.get("content"))
        if ent["writes"] and c != ent["writes"][-1] and c in ent["writes"]:
            flips += 1
        if not ent["writes"] or ent["writes"][-1] != c:
            ent["writes"].append(c)
        ent["writes"] = ent["writes"][-30:]
    ent["flips"] = ent.get("flips", 0) + flips
    lim = int(cfg.get("edit_flips", 3))
    if ent["flips"] >= lim and not ent.get("struck"):
        ent.update(struck=True, struck_at=now, denies=0)
        return MSG_FILE.format(n=lim)
    return None


def _deny_if_struck(ent: Optional[Dict[str, Any]], msg: str, cfg: dict, now: float) -> Optional[str]:
    if not ent or not ent.get("struck"):
        return None
    if _expired(ent, now, float(cfg.get("ttl_seconds", 1800))) or ent.get("denies", 0) >= int(cfg.get("max_denies", 3)):
        ent.update(struck=False, fails=0, flips=0, denies=0)  # 자동 해제 (교착 방지)
        return None
    ent["denies"] = ent.get("denies", 0) + 1
    return msg


def _post_output(msg: str, cfg: dict) -> Result:
    if cfg.get("output") == "exit2":
        return 2, None, msg
    if cfg.get("action") == "stop":
        return 0, {"continue": False, "stopReason": msg}, ""
    return 0, {"decision": "block", "reason": msg}, ""


def _pre_output(msg: str, cfg: dict) -> Result:
    if cfg.get("output") == "exit2":
        return 2, None, msg
    return 0, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": msg}}, ""


def handle(payload: Dict[str, Any], cfg: dict, sdir: str, now: Optional[float] = None) -> Result:
    """훅 payload 하나를 처리한다. 상태 파일을 읽고/쓰며 (code, stdout_json, stderr) 를 돌려준다."""
    now = time.time() if now is None else now
    if not cfg.get("enabled", True):
        return 0, None, ""
    tool = payload.get("tool_name") or ""
    tin = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    event = payload.get("hook_event_name") or ""
    if not event:
        event = "PostToolUse" if ("tool_response" in payload or "error" in payload) else "PreToolUse"
    if tool != "Bash" and tool not in EDIT_TOOLS:
        return 0, None, ""
    sid = str(payload.get("session_id") or "unknown")
    aid = str(payload.get("agent_id") or "")
    path = state_path(sdir, sid, aid)
    state = load_state(path)
    result: Result = (0, None, "")
    dirty = False

    if event == "PreToolUse":
        if tool == "Bash":
            key = command_key(normalize_command(str(tin.get("command", ""))))
            ent = state["cmds"].get(key)
            msg = _deny_if_struck(ent, MSG_COMMAND.format(n=cfg.get("threshold", 3)) + HINT, cfg, now)
        else:
            fp = tin.get("file_path") or tin.get("path")
            ent = state["files"].get(_norm_path(fp)) if isinstance(fp, str) and fp else None
            msg = _deny_if_struck(ent, MSG_FILE.format(n=cfg.get("edit_flips", 3)) + HINT, cfg, now)
        if ent is not None:
            dirty = True  # denies 카운터/자동 해제 반영
        if msg:
            result = _pre_output(msg, cfg)
    elif event in ("PostToolUse", "PostToolUseFailure"):
        msg = None
        if tool == "Bash":
            if event == "PostToolUseFailure":
                failed = not payload.get("is_interrupt")
                if payload.get("is_interrupt"):
                    return 0, None, ""
            else:
                failed = bash_failed(payload.get("tool_response"))
            msg = _record_command(state, str(tin.get("command", "")), failed, cfg, now)
            dirty = True
        elif event == "PostToolUse":
            msg = _record_edit(state, tool, tin, cfg, now)
            _clear_command_strikes(state)  # 파일을 고쳤다 = 접근을 바꿨다
            dirty = True
        if msg:
            result = _post_output(msg, cfg)
    if dirty:
        save_state(path, state)
    return result


def run_hook(stdin_text: str, env: Optional[Mapping[str, str]] = None, cwd: Optional[str] = None,
             cfg: Optional[dict] = None, sdir: Optional[str] = None, now: Optional[float] = None,
             exit2: bool = False) -> Tuple[int, str, str]:
    """(exit_code, stdout_text, stderr_text). 어떤 예외도 (0, '', '') 로 삼킨다."""
    env = os.environ if env is None else env
    try:
        if env.get("DEVGUARD_STRIKE_OFF") == "1":
            return 0, "", ""
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
        if not isinstance(payload, dict):
            return 0, "", ""
        dump = env.get("DEVGUARD_STRIKE_DUMP")
        if dump:  # 실제 payload 모양 확인용 (README 검증 방법)
            try:
                with open(dump, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except OSError:
                pass
        if cfg is None:
            cfg = load_config(cwd or payload.get("cwd") or os.getcwd())["strike"]
        cfg = dict(cfg)
        if exit2 or env.get("DEVGUARD_STRIKE_OUTPUT") == "exit2":
            cfg["output"] = "exit2"
        if env.get("DEVGUARD_STRIKE_THRESHOLD", "").isdigit():
            cfg["threshold"] = int(env["DEVGUARD_STRIKE_THRESHOLD"])
        code, out, err = handle(payload, cfg, sdir or state_dir(env), now)
        return code, (json.dumps(out, ensure_ascii=True) if out is not None else ""), err
    except Exception:  # noqa: BLE001 - Claude 를 절대 망가뜨리지 않는다
        return 0, "", ""


def reset(sdir: str, session_id: Optional[str] = None) -> int:
    """상태 파일 삭제 (session_id 없으면 전부). 지운 개수 반환."""
    n = 0
    try:
        for name in os.listdir(sdir):
            if session_id and not name.startswith(_safe(session_id)):
                continue
            if name.endswith(".json"):
                os.remove(os.path.join(sdir, name))
                n += 1
    except OSError:
        pass
    return n


def status(sdir: str) -> List[str]:
    lines: List[str] = []
    try:
        names = sorted(os.listdir(sdir))
    except OSError:
        return ["(상태 없음)"]
    for name in names:
        if not name.endswith(".json"):
            continue
        st = load_state(os.path.join(sdir, name))
        cmds = [(e.get("cmd", "?"), e.get("fails", 0), e.get("struck")) for e in st["cmds"].values() if e.get("fails")]
        files = [(k, e.get("flips", 0), e.get("struck")) for k, e in st["files"].items() if e.get("flips")]
        if cmds or files:
            lines.append("[%s]" % name[:-5])
            for c, n, s in cmds:
                lines.append("  명령 실패 %d회%s : %s" % (n, " (파업중)" if s else "", c[:80]))
            for f, n, s in files:
                lines.append("  파일 되돌림 %d회%s : %s" % (n, " (파업중)" if s else "", f))
    return lines or ["(기록된 실패/되돌림 없음)"]
