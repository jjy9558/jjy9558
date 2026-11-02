"""권한 소음 측정기: ~/.claude/projects/**/*.jsonl 의 tool_use 를 세어 allow 목록 후보를 '제안'만 한다.

settings 는 절대 수정하지 않는다. 적용하려면 제안을 보고 직접 넣거나 settings-snippet 의 --merge 흐름을 쓴다.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from devguard.wallet import iter_jsonl

# 두 단어까지 보는 명령 (git status / npm run / docker compose ...)
SUBCOMMAND_TOOLS = {"git", "npm", "npx", "pnpm", "yarn", "docker", "kubectl", "cargo", "go", "pip", "pip3",
                    "uv", "gh", "make", "dotnet", "terraform", "brew", "apt", "apt-get", "systemctl"}
# 제안에서 빼는 위험 명령 (세기는 하되 '검토 필요'로 따로 표시)
RISKY = {"rm", "rmdir", "sudo", "su", "dd", "mkfs", "chmod", "chown", "kill", "killall", "pkill", "curl", "wget",
         "ssh", "scp", "eval", "exec", "mv", "shutdown", "reboot", "format", "del", "git push", "git reset",
         "git clean", "git checkout", "git rebase", "npm publish", "docker rm", "docker run", "pip install",
         "npm install", "kubectl delete", "terraform apply", "terraform destroy"}
# 읽기 전용이라 허용해도 부담이 적은 도구
SAFE_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead", "TodoWrite", "WebSearch"}
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def command_prefixes(command: str) -> List[str]:
    """'cd x && git status | head' -> ['cd', 'git status', 'head']. 파싱 못 하면 첫 단어."""
    out: List[str] = []
    for seg in _SPLIT.split(command or ""):
        seg = seg.strip()
        if not seg:
            continue
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        while toks and _ASSIGN.match(toks[0]):
            toks.pop(0)
        if not toks:
            continue
        head = os.path.basename(toks[0]) if "/" in toks[0] else toks[0]
        if head in SUBCOMMAND_TOOLS and len(toks) > 1 and not toks[1].startswith("-"):
            out.append("%s %s" % (head, toks[1]))
        else:
            out.append(head)
    return out


def _tool_uses(path: str) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] != "{":
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                msg = obj.get("message") if isinstance(obj, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use" and isinstance(item.get("name"), str):
                        inp = item.get("input")
                        yield str(item.get("id") or ""), item["name"], inp if isinstance(inp, dict) else {}
    except OSError:
        return


def count_tool_calls(root: str, min_mtime: Optional[float] = None) -> Tuple[Counter, Counter, int]:
    """(도구별 Counter, Bash 접두어별 Counter, 총 호출 수). tool_use id 로 중복 제거."""
    tools: Counter = Counter()
    bash: Counter = Counter()
    seen = set()
    total = 0
    for path in iter_jsonl(root, min_mtime):
        for tid, name, inp in _tool_uses(path):
            if tid:
                if tid in seen:
                    continue
                seen.add(tid)
            total += 1
            tools[name] += 1
            if name == "Bash":
                cmd = inp.get("command")
                if isinstance(cmd, str):
                    for pre in command_prefixes(cmd):
                        bash[pre] += 1
    return tools, bash, total


def is_risky(prefix: str) -> bool:
    return prefix in RISKY or prefix.split(" ")[0] in RISKY


def suggest(tools: Counter, bash: Counter, min_count: int = 3, top: int = 20) -> Dict[str, List[Tuple[str, int]]]:
    """{"allow": [(규칙, 횟수)], "review": [(규칙, 횟수)]}. review 는 위험해 보여 자동 제안에서 뺀 것."""
    allow: List[Tuple[str, int]] = []
    review: List[Tuple[str, int]] = []
    for name, n in tools.items():
        if n < min_count or name == "Bash":
            continue
        (allow if (name in SAFE_TOOLS or name.startswith("mcp__")) else review).append((name, n))
    for pre, n in bash.items():
        if n < min_count:
            continue
        rule = "Bash(%s:*)" % pre
        (review if is_risky(pre) else allow).append((rule, n))
    key = lambda kv: (-kv[1], kv[0])  # noqa: E731
    return {"allow": sorted(allow, key=key)[:top], "review": sorted(review, key=key)[:top]}


def format_report(tools: Counter, bash: Counter, total: int, sug: Dict[str, List[Tuple[str, int]]],
                  top: int = 10) -> str:
    L = ["=== 권한 소음 측정기 (제안만 출력, settings 는 수정하지 않음) ===", "도구 호출 %d회" % total]
    if not total:
        L.append("(기록 없음)")
        return "\n".join(L)
    L += ["", "[도구별]"] + ["  %6d  %s" % (n, k) for k, n in tools.most_common(top)]
    L += ["", "[Bash 명령 접두어]"] + ["  %6d  %s" % (n, k) for k, n in bash.most_common(top)]
    L += ["", "[allow 후보] permissions.allow 에 넣으면 확인 창이 줄어듭니다"]
    L += ["  %6d  %s" % (n, r) for r, n in sug["allow"]] or ["  (없음)"]
    if sug["review"]:
        L += ["", "[검토 필요] 자주 쓰지만 위험할 수 있어 자동 제안에서 뺐습니다 (신중히 직접 결정)"]
        L += ["  %6d  %s" % (n, r) for r, n in sug["review"]]
    L += ["", '적용 예: "permissions": {"allow": [ ...위 규칙... ]}  (수동 편집 또는 직접 확인 후 반영)']
    return "\n".join(L)


def report_json(tools: Counter, bash: Counter, total: int, sug: Dict[str, List[Tuple[str, int]]]) -> str:
    return json.dumps({"total_calls": total, "tools": dict(tools.most_common()),
                       "bash_prefixes": dict(bash.most_common()),
                       "suggest_allow": [{"rule": r, "count": n} for r, n in sug["allow"]],
                       "needs_review": [{"rule": r, "count": n} for r, n in sug["review"]]},
                      ensure_ascii=False, indent=2)
