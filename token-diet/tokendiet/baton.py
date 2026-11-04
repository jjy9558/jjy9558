"""세션 바톤: Claude Code 트랜스크립트에서 새 세션으로 넘길 한국어 인수인계 메모를 만든다(읽기 전용)."""
import re
from pathlib import Path

from ._jjkit import claude_logs
from ._jjkit import redact as _redact

DEFAULT_CAP = 2000
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
OPEN_RE = re.compile(r"남은|남았|TODO|다음에|다음 단계|다음은|해야 할|미완|아직|FIXME|follow-?up", re.I)
FAIL_RE = re.compile(r"^(?:FAIL|ERROR):\s+(\S+)|^FAILED\s+(\S+)", re.M)
CHANGE_RE = re.compile(r"대신|말고|바꿔|변경|수정해|추가로|그리고 |also|instead", re.I)


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def find_session(root, session=None, latest=False, project=None):
    """로그 루트에서 세션 파일 하나를 고른다. 없으면 None."""
    root = Path(root)
    files = [p for p in claude_logs.iter_jsonl_files(root)]
    if project:
        slug = _slug(Path(project).resolve())
        want = str(project).rstrip("/\\")
        keep = []
        for p in files:
            if p.parent.name == slug or p.parent.name == _slug(want):
                keep.append(p)
                continue
            for _, obj in claude_logs.iter_records(p):
                c = obj.get("cwd")
                if isinstance(c, str):
                    if Path(c) == Path(want) or Path(c) == Path(project).resolve():
                        keep.append(p)
                    break
        files = keep
    if session:
        for p in files:
            if p.stem == session:
                return p
        return None
    if not files:
        return None
    return max(files, key=lambda p: (p.stat().st_mtime, str(p)))


def _tool_results(path):
    """(is_error, text) 목록: 실패 테스트 이름 탐지용."""
    out = []
    for _, obj in claude_logs.iter_records(path):
        if obj.get("type") != "user" or obj.get("isSidechain"):
            continue
        c = (obj.get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                cc = b.get("content")
                if isinstance(cc, list):
                    cc = "\n".join(x.get("text", "") for x in cc if isinstance(x, dict))
                if isinstance(cc, str):
                    out.append((bool(b.get("is_error")), cc))
    return out


def _one_line(t, n):
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def analyze(path) -> dict:
    msgs = claude_logs.load_messages(path)
    users = [m for m in msgs if m.role == "user"]
    asst = [m for m in msgs if m.role == "assistant"]
    files, cmds, opens = [], [], []
    for m in asst:
        for name, inp in m.tools:
            if name in EDIT_TOOLS:
                f = inp.get("file_path") or inp.get("notebook_path")
                if isinstance(f, str) and f not in files:
                    files.append(f)
            elif name == "Bash" and isinstance(inp.get("command"), str):
                c = _one_line(inp["command"], 100)
                if c not in cmds:
                    cmds.append(c)
    for m in msgs:
        if m.role != "assistant":
            continue
        for line in m.text.splitlines():
            if OPEN_RE.search(line):
                s = _one_line(line.strip("-*• #>"), 120)
                if s and s not in opens:
                    opens.append(s)
    failing = []
    results = _tool_results(path)
    for _, text in results[-20:]:
        for mt in FAIL_RE.finditer(text):
            n = mt.group(1) or mt.group(2)
            if n not in failing:
                failing.append(n)
    changes = [_one_line(m.text, 120) for m in users[1:] if CHANGE_RE.search(m.text)]
    return {
        "sid": msgs[0].session_id if msgs else Path(path).stem,
        "cwd": next((m.cwd for m in msgs if m.cwd), ""),
        "goal": _one_line(users[0].text, 200) if users else "",
        "changes": changes[-3:],
        "summaries": [_one_line(m.text, 160) for m in asst if m.text][-3:],
        "files": files[-12:], "cmds": cmds[-6:], "opens": opens[-6:], "failing": failing[-5:],
    }


def _bul(items, empty="(없음)"):
    return [f"- {i}" for i in items] if items else [f"- {empty}"]


def build_baton(info: dict, cap: int = DEFAULT_CAP) -> str:
    nxt = ["## 다음 세션 첫 메시지", "```",
           "이전 세션에서 이어서 작업합니다. 목표: " + (info["goal"] or "(불명)")[:120]]
    todo = info["failing"] and f"먼저 실패하는 테스트를 확인: {', '.join(info['failing'][:3])}" or \
        (info["opens"][0] if info["opens"] else "위 '남은 일'부터 이어서 진행")
    nxt += [f"먼저 할 일: {todo}", "수정된 파일을 먼저 읽고 현재 상태를 확인한 뒤 시작해 주세요.", "```"]
    head = ["# 세션 바톤", ""]
    if info["cwd"]:
        head.append(f"> 작업 폴더: `{info['cwd']}`")
        head.append("")
    body = ["## 목표", info["goal"] or "(사용자 요청 없음)"]
    if info["changes"]:
        body += ["", "변경된 요구:"] + _bul(info["changes"])
    body += ["", "## 지금까지"] + _bul(info["summaries"])
    body += ["", "수정한 파일:"] + _bul(info["files"])
    body += ["", "## 남은 일"] + _bul(info["opens"] + [f"실패 테스트: {n}" for n in info["failing"]])
    body += ["", "## 실행한 주요 명령"] + _bul(info["cmds"])
    tail = "\n".join([""] + nxt) + "\n"
    red = _redact.redact
    tail = red(tail)
    budget = max(cap - len(tail) - 20, 0)
    text = red("\n".join(head + body))
    if len(text) > budget:
        text = text[:budget].rstrip() + "\n…(길이 제한으로 생략)"
    return text + "\n" + tail
