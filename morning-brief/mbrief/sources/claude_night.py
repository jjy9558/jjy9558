"""야간 Claude 보고 어댑터: Claude Code 트랜스크립트(~/.claude/projects/**/*.jsonl)에서 밤사이 작업을 프로젝트별로 3줄 요약.

jjkit claude_logs.load_messages 로 읽는다(사이드체인, system-reminder 제외). voice-journal 의
claude_ingest 와 같은 아이디어(요청/완료/막힘 3줄)를 가져오되 결과는 jjkit redact 로 마스킹한다.
창: 전날 start_hour 시 ~ 오늘 end_hour 시(기본 23시~9시).
"""
from __future__ import annotations

import datetime as dt
import os
import re
from collections import OrderedDict

from .._jjkit import claude_logs
from ..model import Context, Item, SourceMissing
from ..speech import shorten

NAME = "claude_night"
SECTION = "밤사이 Claude"
DEFAULTS = {"enabled": True, "projects_dir": "", "start_hour": 23, "end_hour": 9, "max_projects": 5,
            "ignore_cwd": []}

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_BLOCK = re.compile(r"막혔|막힘|진행\s*불가|못\s*했|확인이\s*필요|승인|권한|permission|blocked|cannot|can't|"
                    r"실패|failed|failing|에러|오류|판단이\s*필요|결정이\s*필요|알려\s*주|말씀해", re.I)
_TEST = re.compile(r"pytest|unittest|npm\s+(?:run\s+)?test|go\s+test|cargo\s+test|jest|vitest")
_SENT = re.compile(r"(?<=[.!?。…])\s+|[\r\n]+")


def window(ctx: Context, start_hour: int, end_hour: int) -> tuple[dt.datetime, dt.datetime]:
    tz, today = ctx.tz, ctx.today
    end = dt.datetime(today.year, today.month, today.day, tzinfo=tz) + dt.timedelta(hours=end_hour)
    start_day = today - dt.timedelta(days=1) if start_hour > end_hour else today
    start = dt.datetime(start_day.year, start_day.month, start_day.day, tzinfo=tz) + dt.timedelta(hours=start_hour)
    return start, end


def summarize(msgs: list, red) -> dict:
    prompts = [t for m in msgs if m.role == "user" for t in m.texts]
    files, commits, tests = OrderedDict(), 0, 0
    for m in msgs:
        for name, inp in m.tools:
            if name in EDIT_TOOLS:
                fp = inp.get("file_path") or inp.get("notebook_path") or ""
                if fp:
                    files[os.path.basename(str(fp))] = True
            elif name == "Bash":
                cmd = str(inp.get("command", ""))
                commits += "git commit" in cmd
                tests += bool(_TEST.search(cmd))
    a_texts = [t for m in msgs if m.role == "assistant" for t in m.texts]
    last_a = a_texts[-1] if a_texts else ""
    sents = [s.strip() for s in _SENT.split(red(last_a)) if s.strip()]

    asked = ('요청: "%s"' % shorten(red(prompts[0]), 60) + (" 외 %d건" % (len(prompts) - 1) if len(prompts) > 1 else "")) \
        if prompts else "요청: (사람 입력 없이 이어서 진행된 세션)"
    parts = []
    if files:
        names = list(files)
        parts.append("파일 %d개 수정(%s%s)" % (len(names), ", ".join(names[:3]), " 외" if len(names) > 3 else ""))
    if commits:
        parts.append("커밋 %d회" % commits)
    if tests:
        parts.append("테스트 %d회 실행" % tests)
    done = "완료: " + (", ".join(parts) if parts else "눈에 띄는 변경 없음")
    if sents:
        done += ' - 마지막 보고: "%s"' % shorten(sents[0], 70)

    blocked_why = ""
    if msgs and msgs[-1].role == "user":
        blocked_why = "마지막 요청에 응답이 없음(중단됐을 수 있음)"
    elif last_a and (last_a.rstrip().endswith(("?", "？")) or _BLOCK.search(last_a)):
        pick = (next((s for s in reversed(sents) if _BLOCK.search(s)), None)
                or next((s for s in reversed(sents) if s.endswith(("?", "？"))), None) or (sents[-1] if sents else ""))
        blocked_why = shorten(pick, 70)
    return {"asked": asked, "done": done, "blocked": blocked_why, "files": len(files),
            "commits": commits, "messages": len(msgs)}


def collect(cfg: dict, ctx: Context) -> list[Item]:
    root = ctx.path(cfg["projects_dir"]) if cfg.get("projects_dir") else claude_logs.default_projects_dir()
    if not root.is_dir():
        raise SourceMissing("Claude 로그 폴더가 없어요: %s" % root.name)
    start, end = window(ctx, int(cfg["start_hour"]), int(cfg["end_hour"]))
    msgs = claude_logs.load_messages(root, since=start, until=end)
    ignore = [s for s in cfg.get("ignore_cwd") or [] if s]
    groups: "OrderedDict[str, list]" = OrderedDict()
    for m in msgs:
        key = m.cwd or m.path
        if any(s in key for s in ignore):
            continue
        groups.setdefault(key, []).append(m)
    if not groups:
        return []
    red = ctx.red
    rows = []
    for key, ms in groups.items():
        label = re.split(r"[\\/]", key.rstrip("/\\"))[-1] or key
        s = summarize(ms, red)
        s["project"] = shorten(red(label), 30)
        rows.append(s)
    rows.sort(key=lambda r: (-r["messages"], r["project"]))
    rows = rows[:max(1, int(cfg["max_projects"]))]
    blocked = [r for r in rows if r["blocked"]]
    items: list[Item] = []
    n, b = len(rows), len(blocked)
    speak = "밤새 Claude가 프로젝트 %d개를 작업했습니다" % n + (". 그중 %d개는 확인이 필요합니다" % b if b else "")
    items.append(Item(NAME, "claude-summary", "밤새 Claude 작업 %d개%s" % (n, ", 확인 필요 %d개" % b if b else ""),
                      "\n".join("- %s" % r["project"] for r in rows), 64 if b else 50, speak,
                      "밤새 Claude 작업 %d개" % n + (", 확인 필요 %d개" % b if b else ""), None, (), card=False))
    for r in rows:
        lines = [r["asked"], r["done"], "막힘: " + (r["blocked"] or "없음")]
        if r["blocked"]:
            items.append(Item(NAME, "claude-blocked", "%s: 확인 필요" % r["project"], "\n".join(lines), 60,
                              "%s에서 막힘: %s" % (r["project"], shorten(r["blocked"], 50)),
                              "%s 확인 필요" % r["project"], None, ("Claude 세션 열어서 답하기",)))
        else:
            items.append(Item(NAME, "claude-night", "%s: 밤샘 작업 완료" % r["project"], "\n".join(lines), 45,
                              "", "", None, ()))
    return items
