"""명령줄 진입점: python -m tokendiet <명령>."""
import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from . import __version__
from . import baton, checkup, claudemd, logscan, mcpweigh, repeats, route
from ._jjkit import claude_logs
from .report import Report, to_html, to_markdown


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="token-diet", description="토큰 다이어트: 세션 로그 절감 포인트, MCP 체중계, 반복 프롬프트, CLAUDE.md 린터")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--md", help="마크다운 리포트를 이 파일로 저장(없으면 화면 출력)")
        sp.add_argument("--html", help="자기완결 HTML 리포트 저장")

    s = sub.add_parser("scan", help="jsonl 로그에서 반복 읽기/큰 출력/재시도 루프 찾기")
    s.add_argument("path", nargs="?", help="폴더 또는 .jsonl (기본 ~/.claude/projects)")
    s.add_argument("--min-tokens", type=int, default=2000, help="큰 출력 기준(추정 토큰)")
    s.add_argument("--top", type=int, default=10)
    common(s)
    m = sub.add_parser("mcp-weigh", help="MCP 도구 정의의 컨텍스트 토큰 추정")
    m.add_argument("files", nargs="*", help="settings.json / .mcp.json / tools.json (기본: ~/.claude.json 등 자동 탐색)")
    m.add_argument("--logs", help="실제 쓰인 mcp 도구 이름을 세려는 로그 경로")
    m.add_argument("--tokens-per-tool", type=int, default=mcpweigh.DEFAULT_TOKENS_PER_TOOL)
    m.add_argument("--tools-per-server", type=int, default=mcpweigh.DEFAULT_TOOLS_PER_SERVER)
    common(m)
    r = sub.add_parser("repeats", help="세션 간 반복된 사용자 지시 -> 스킬/훅 후보")
    r.add_argument("path", nargs="?")
    r.add_argument("--min-count", type=int, default=3)
    r.add_argument("--min-sessions", type=int, default=2)
    r.add_argument("--threshold", type=float, default=0.6)
    common(r)
    c = sub.add_parser("claude-md-lint", help="CLAUDE.md 린트")
    c.add_argument("file")
    c.add_argument("--root", help="낡은 경로 검사 기준 폴더(기본: 파일이 있는 폴더)")
    common(c)
    o = sub.add_parser("route", help="작업 유형별 모델 배정 제안표")
    o.add_argument("--config", help="단가/작업 JSON")
    o.add_argument("--runs", type=int, default=100)
    common(o)
    a = sub.add_parser("report", help="전부 합친 종합 리포트")
    a.add_argument("--logs")
    a.add_argument("--claude-md", action="append", default=[])
    a.add_argument("--mcp-file", action="append", default=[])
    common(a)
    b = sub.add_parser("baton", help="세션 바톤: 새 세션으로 넘길 한국어 인수인계 메모")
    b.add_argument("--session", help="세션 ID(= jsonl 파일 이름)")
    b.add_argument("--latest", action="store_true", help="가장 최근 세션(기본)")
    b.add_argument("--project", help="이 작업 폴더의 세션만")
    b.add_argument("--logs", help="로그 루트(기본 ~/.claude/projects)")
    b.add_argument("--cap", type=int, default=baton.DEFAULT_CAP, help="최대 글자 수(기본 2000)")
    b.add_argument("--out", help="마크다운 저장 경로(없으면 화면 출력)")
    k = sub.add_parser("checkup", help="건강검진표: 저장소 상태 점검 카드")
    k.add_argument("repo_dir", nargs="?", default=".")
    k.add_argument("--run", action="store_true", help="감지한 테스트 명령을 실제 실행")
    k.add_argument("--timeout", type=int, default=300, help="테스트 제한 시간(초)")
    k.add_argument("--large-kb", type=int, default=1024)
    common(k)
    return p


def _emit(rep: Report, args, out: TextIO) -> int:
    md = to_markdown(rep)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    if args.html:
        Path(args.html).write_text(to_html(rep), encoding="utf-8")
    if not args.md and not args.html:
        out.write(md)
    else:
        print("저장:", ", ".join(x for x in (args.md, args.html) if x), file=out)
    return 0


def _root(path):
    return Path(path) if path else claude_logs.default_projects_dir()


def _scan_rep(args) -> Report:
    root = _root(args.path)
    return logscan.build_report(logscan.load_sessions(root), min_tokens=args.min_tokens, top=args.top, source=str(root))


def _mcp_rep(args) -> Report:
    paths = [Path(f) for f in args.files] or mcpweigh.default_sources()
    data, used = mcpweigh.load_sources(paths)
    obs = mcpweigh.observed_tools(args.logs) if args.logs else None
    rows = mcpweigh.weigh(data, tokens_per_tool=args.tokens_per_tool, tools_per_server=args.tools_per_server, observed=obs)
    return mcpweigh.build_report(rows, ", ".join(used) or "(읽을 수 있는 파일 없음)",
                                 tokens_per_tool=args.tokens_per_tool, tools_per_server=args.tools_per_server)


def _rep_rep(args) -> Report:
    return repeats.build_report(repeats.collect_prompts(_root(args.path)), min_count=args.min_count,
                                min_sessions=args.min_sessions, threshold=args.threshold)


def cmd_scan(args, out):
    return _emit(_scan_rep(args), args, out)


def cmd_mcp(args, out):
    return _emit(_mcp_rep(args), args, out)


def cmd_repeats(args, out):
    return _emit(_rep_rep(args), args, out)


def cmd_lint(args, out):
    if not Path(args.file).is_file():
        print(f"파일이 없습니다: {args.file}", file=sys.stderr)
        return 2
    return _emit(claudemd.build_report(args.file, args.root), args, out)


def cmd_route(args, out):
    cfg = route.load_config(args.config) if args.config else None
    return _emit(route.build_report(cfg, args.runs), args, out)


def cmd_report(args, out):
    root = _root(args.logs)
    full = Report("토큰 다이어트 종합 리포트")
    ns = argparse.Namespace(path=str(root), min_tokens=2000, top=10)
    parts = [_scan_rep(ns)]
    ns2 = argparse.Namespace(files=args.mcp_file, logs=str(root), tokens_per_tool=mcpweigh.DEFAULT_TOKENS_PER_TOOL,
                             tools_per_server=mcpweigh.DEFAULT_TOOLS_PER_SERVER)
    parts.append(_mcp_rep(ns2))
    parts.append(repeats.build_report(repeats.collect_prompts(root)))
    mds = [Path(x) for x in args.claude_md] or [p for p in (Path.cwd() / "CLAUDE.md", Path.home() / ".claude" / "CLAUDE.md") if p.is_file()]
    for md in mds:
        parts.append(claudemd.build_report(md))
    for r in parts:
        full.intro += [n for n in r.intro if n not in full.intro]
        for s in r.sections:
            s.title = f"[{r.title.split(':')[0]}] {s.title}"
            full.sections.append(s)
    return _emit(full, args, out)


def cmd_baton(args, out):
    root = _root(args.logs)
    path = baton.find_session(root, session=args.session, latest=args.latest, project=args.project)
    if path is None:
        print("조건에 맞는 세션을 찾지 못했습니다.", file=sys.stderr)
        return 2
    text = baton.build_baton(baton.analyze(path), args.cap)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("저장:", args.out, file=out)
    else:
        out.write(text)
    return 0


def cmd_checkup(args, out):
    if not Path(args.repo_dir).is_dir():
        print(f"폴더가 없습니다: {args.repo_dir}", file=sys.stderr)
        return 2
    return _emit(checkup.build_report(args.repo_dir, run=args.run, timeout=args.timeout, large_kb=args.large_kb), args, out)


COMMANDS = {"baton": cmd_baton, "checkup": cmd_checkup, "scan": cmd_scan, "mcp-weigh": cmd_mcp, "repeats": cmd_repeats, "claude-md-lint": cmd_lint,
            "route": cmd_route, "report": cmd_report}


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return COMMANDS[args.cmd](args, out or sys.stdout)
