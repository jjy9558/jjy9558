"""MCP 체중계: 도구 정의 JSON이 컨텍스트에서 차지하는 토큰 추정. 서버를 띄우거나 네트워크를 쓰지 않는다.

방법(정확도 순):
  A. 도구 정의(name/description/inputSchema)가 파일에 있으면 compact JSON 문자 수/4  -> 'schema'
  B. 서버 설정에 도구 수만 있으면(toolCount 또는 tools가 이름 목록) 도구당 가정 토큰 x 수  -> 'count'
  C. 서버 설정만 있고 아무 정보가 없으면 서버당 가정 도구 수 x 도구당 가정 토큰 -> 'assumed'
  (선택) --logs 가 있으면 로그에서 실제 쓰인 mcp__서버__도구 이름을 세어 B 단계의 도구 수 하한으로 쓴다.
가정 값은 --tokens-per-tool, --tools-per-server 로 바꿀 수 있고 리포트에 그대로 표시된다.
"""
import json
import re
from pathlib import Path

from ._jjkit import claude_logs
from .report import Report, est_tokens, fmt_tok

DEFAULT_TOKENS_PER_TOOL = 350      # 가정: 이름+설명+스키마 한 도구의 평균(추정치, 서버마다 크게 다름)
DEFAULT_TOOLS_PER_SERVER = 10      # 가정


def _compact(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def _is_tooldef(o) -> bool:
    return isinstance(o, dict) and isinstance(o.get("name"), str) and any(
        k in o for k in ("description", "inputSchema", "input_schema", "parameters"))


def _servers_from(data) -> dict:
    """다양한 모양에서 {서버: {'defs': [...], 'count': n|None, 'cfg': dict}} 를 뽑는다."""
    out: dict = {}

    def add(name, entry):
        e = out.setdefault(name, {"defs": [], "count": None, "cfg": {}})
        if isinstance(entry, dict):
            e["cfg"] = {k: v for k, v in entry.items() if k != "tools"}
            tools = entry.get("tools")
            if isinstance(entry.get("toolCount"), int):
                e["count"] = entry["toolCount"]
            if isinstance(tools, list):
                defs = [t for t in tools if _is_tooldef(t)]
                e["defs"] += defs
                if not defs:
                    e["count"] = len(tools)
        elif isinstance(entry, list):
            e["defs"] += [t for t in entry if _is_tooldef(t)]

    if isinstance(data, list):
        if all(_is_tooldef(t) for t in data) and data:
            # 이름이 mcp__서버__도구 면 서버별로 묶는다
            for t in data:
                m = re.match(r"mcp__(.+?)__", t["name"])
                out.setdefault(m.group(1) if m else "(서버 미상)", {"defs": [], "count": None, "cfg": {}})["defs"].append(t)
        return out
    if not isinstance(data, dict):
        return out
    if isinstance(data.get("mcpServers"), dict):
        for n, v in data["mcpServers"].items():
            add(n, v)
    if isinstance(data.get("projects"), dict):       # ~/.claude.json
        for pv in data["projects"].values():
            if isinstance(pv, dict) and isinstance(pv.get("mcpServers"), dict):
                for n, v in pv["mcpServers"].items():
                    add(n, v)
    if isinstance(data.get("tools"), list):
        for n, e in _servers_from(data["tools"]).items():
            out.setdefault(n, e)
    if not out and data and all(isinstance(v, list) for v in data.values()):   # {서버: [도구...]}
        for n, v in data.items():
            add(n, v)
    return out


def observed_tools(logs_root) -> dict:
    """로그에서 실제 호출된 서버별 고유 MCP 도구 이름."""
    seen: dict = {}
    for _p, obj in claude_logs.iter_records(logs_root):
        msg = obj.get("message")
        c = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    m = re.match(r"mcp__(.+?)__(.+)", str(b.get("name")))
                    if m:
                        seen.setdefault(m.group(1), set()).add(m.group(2))
    return seen


def weigh(data, *, tokens_per_tool=DEFAULT_TOKENS_PER_TOOL, tools_per_server=DEFAULT_TOOLS_PER_SERVER,
          observed: dict | None = None) -> list:
    rows = []
    for name, e in _servers_from(data).items():
        if e["defs"]:
            tok = sum(est_tokens(len(_compact(t))) for t in e["defs"])
            n, method = len(e["defs"]), "schema"
        else:
            obs = len((observed or {}).get(name, ()))
            if e["count"] is not None or obs:
                n, method = max(e["count"] or 0, obs), "count"
            else:
                n, method = tools_per_server, "assumed"
            tok = n * tokens_per_tool
        rows.append({"server": name, "tools": n, "tokens": tok, "method": method})
    for name, tools in (observed or {}).items():       # 설정에는 없지만 로그에 쓰인 서버(커넥터 등)
        if name not in {r["server"] for r in rows}:
            rows.append({"server": name, "tools": len(tools), "tokens": len(tools) * tokens_per_tool, "method": "count"})
    rows.sort(key=lambda r: (-r["tokens"], r["server"]))
    return rows


METHOD_KO = {"schema": "스키마 문자수/4 (비교적 근거 있음)", "count": "도구 수 x 가정 토큰", "assumed": "서버당 가정 도구 수 x 가정 토큰 (순수 가정)"}


def build_report(rows: list, source: str, *, tokens_per_tool, tools_per_server, window: int = 200_000) -> Report:
    rep = Report("MCP 체중계: 도구 정의가 컨텍스트에서 먹는 토큰")
    rep.intro.append("서버를 실행하지 않고 파일만 읽은 추정치입니다. 실제 도구 정의는 서버가 연결될 때 받아오므로 디스크에 스키마가 없으면 가정치를 씁니다.")
    rep.intro.append("Claude Code는 도구가 많으면 ToolSearch로 정의 로딩을 미뤄 실제 상시 비용이 더 작을 수 있습니다(설정과 버전에 따라 다름).")
    total = sum(r["tokens"] for r in rows)
    rep.add("요약", [f"입력: {source}", f"서버 {len(rows)}개, 추정 합계 {fmt_tok(total)} 토큰 (컨텍스트 {window // 1000}k 기준 약 {total * 100 / window:.1f}%)",
                   f"가정값: 도구당 {tokens_per_tool} 토큰, 정보 없는 서버는 {tools_per_server}개 도구 (바꾸려면 --tokens-per-tool / --tools-per-server)"])
    rep.add("서버별 순위", [], ["순위", "서버", "도구 수", "추정 토큰", "비율", "추정 방법"],
            [[i + 1, r["server"], r["tools"], fmt_tok(r["tokens"]), f"{r['tokens'] * 100 / total:.0f}%" if total else "-", METHOD_KO[r["method"]]]
             for i, r in enumerate(rows)] or [["-", "서버를 찾지 못함", "", "", "", ""]])
    rep.add("추정 방법 설명", ["A. 도구 정의 JSON이 있으면 compact JSON 문자 수/4.", "B. 도구 수만 알면 도구 수 x 도구당 가정 토큰(로그에서 관찰된 도구 이름 수 포함).",
                          "C. 서버 설정만 있으면 가정 도구 수 x 가정 토큰. 실제 값과 크게 다를 수 있음.",
                          "절감: 안 쓰는 서버 끄기, 프로젝트별 .mcp.json으로 필요한 서버만 켜기."])
    return rep


def default_sources() -> list:
    home = Path.home()
    return [home / ".claude.json", home / ".claude" / "settings.json", home / ".mcp.json", Path.cwd() / ".mcp.json"]


def load_sources(paths) -> tuple:
    """여러 파일을 합친다. 읽기 전용."""
    merged: dict = {"mcpServers": {}}
    used = []
    for p in paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        used.append(str(p))
        for n, e in _servers_from(data).items():
            cur = merged["mcpServers"].setdefault(n, {"tools": []})
            cur["tools"] += e["defs"]
            if e["count"] is not None:
                cur["toolCount"] = e["count"]
    return merged, used
