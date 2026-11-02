# -*- coding: utf-8 -*-
"""Claude Code 트랜스크립트(~/.claude/projects/**/*.jsonl) -> 야간 작업 아침 3줄 보고.

til-drafter/transcripts.py 의 파싱 아이디어(노이즈/사이드체인 제외, 깨진 줄 건너뛰기)를 축약해 가져왔다.
모든 출력 텍스트는 redact.Redactor 를 거친다.
"""
import json
import os
import re
from collections import OrderedDict, namedtuple
from datetime import datetime, timedelta, timezone

from .llm import llm_summarize_lines
from .parse import split_sentences
from .redact import Redactor

Event = namedtuple("Event", "ts role cwd session texts tools errors")

_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?\s*(Z|z|[+-]\d{2}:?\d{2})?$")
_NOISE_PREFIXES = (
    "Caveat:", "<command-name>", "<command-message>", "<command-args>", "<local-command-stdout>",
    "<local-command-stderr>", "<local-command-caveat>", "<user-prompt-submit-hook>",
    "<ide_opened_file>", "<ide_selection>", "[Request interrupted",
    "This session is being continued from a previous conversation",
)
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I)
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def default_projects_dir():
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(base, "projects") if base else os.path.join(os.path.expanduser("~"), ".claude", "projects")


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    m = _TS_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
    frac = (m.group(7) or "0")[:6].ljust(6, "0")
    zone = m.group(8)
    if zone in (None, "Z", "z"):
        tz = timezone.utc
    else:
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    try:
        return datetime(y, mo, d, h, mi, s, int(frac), tzinfo=tz)
    except ValueError:
        return None


def clean_text(text):
    if not isinstance(text, str):
        return None
    t = text
    if "<system-reminder" in t.lower():
        t = _REMINDER.sub("", t)
        if "<system-reminder" in t.lower():
            return None
    t = t.strip()
    if not t or any(t.startswith(p) for p in _NOISE_PREFIXES):
        return None
    return t


def _result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _extract(obj):
    typ = obj.get("type")
    if typ not in ("user", "assistant"):
        return None
    if obj.get("isSidechain") or obj.get("isMeta") or obj.get("isCompactSummary"):
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict) or msg.get("model") == "<synthetic>":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = content
    else:
        return None
    texts, tools, errors = [], [], []
    for b in blocks:
        if isinstance(b, str):
            b = {"type": "text", "text": b}
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            t = clean_text(b.get("text"))
            if t:
                texts.append(t)
        elif bt == "tool_use" and typ == "assistant":
            if isinstance(b.get("name"), str):
                tools.append((b["name"], b.get("input") if isinstance(b.get("input"), dict) else {}))
        elif bt == "tool_result" and typ == "user" and b.get("is_error") is True:
            errors.append(_result_text(b)[:300])
    if not (texts or tools or errors):
        return None
    return typ, texts, tools, errors


def iter_jsonl_files(root):
    if not os.path.isdir(str(root)):
        return
    for dp, dns, fns in os.walk(str(root)):
        dns[:] = sorted(d for d in dns if d != "subagents")
        for fn in sorted(fns):
            if fn.endswith(".jsonl"):
                yield os.path.join(dp, fn)


def load_events(projects_dir, start, end, stats=None):
    """[start, end) (aware datetime) 안의 이벤트를 시간순으로."""
    if stats is None:
        stats = {}
    for k in ("files", "lines", "malformed", "events"):
        stats.setdefault(k, 0)
    out = []
    for path in iter_jsonl_files(projects_dir):
        try:
            if os.path.getmtime(path) < start.timestamp():
                continue
        except OSError:
            continue
        stats["files"] += 1
        proj_default = os.path.basename(os.path.dirname(path)).strip("-")
        sid_default = os.path.splitext(os.path.basename(path))[0]
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                stats["lines"] += 1
                try:
                    obj = json.loads(line)
                except ValueError:
                    stats["malformed"] += 1
                    continue
                if not isinstance(obj, dict):
                    stats["malformed"] += 1
                    continue
                ts = parse_timestamp(obj.get("timestamp"))
                if ts is None or not (start <= ts < end):
                    continue
                got = _extract(obj)
                if got is None:
                    continue
                role, texts, tools, errors = got
                cwd = obj.get("cwd") if isinstance(obj.get("cwd"), str) and obj.get("cwd") else proj_default
                sid = obj.get("sessionId") if isinstance(obj.get("sessionId"), str) else sid_default
                out.append(Event(ts, role, cwd, sid, texts, tools, errors))
    out.sort(key=lambda e: e.ts)
    stats["events"] = len(out)
    return out


# ---- 요약 ----

_BLOCK_RE = re.compile(
    r"막혔|막힘|진행\s*불가|못\s*했|못\s*함|확인이\s*필요|승인|권한|permission|blocked|cannot|can't|"
    r"실패|failed|failing|에러|오류|판단이\s*필요|결정이\s*필요|알려\s*주|말씀해", re.I)
_TEST_RE = re.compile(r"pytest|unittest|npm\s+(?:run\s+)?test|go\s+test|cargo\s+test|jest|vitest")


def project_label(cwd):
    c = (cwd or "").rstrip("/\\")
    return re.split(r"[\\/]", c)[-1] or c or "(알 수 없음)"


def _short(s, n):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def summarize_project(events, red):
    """events: 한 프로젝트의 Event 리스트(시간순). red: Redactor. -> dict(asked, done, blocked, stats)."""
    prompts = [t for e in events if e.role == "user" for t in e.texts]
    files, commits, tests, edits = OrderedDict(), 0, 0, 0
    for e in events:
        for name, inp in e.tools:
            if name in EDIT_TOOLS:
                fp = inp.get("file_path") or inp.get("notebook_path") or ""
                if fp:
                    files[os.path.basename(fp)] = True
                    edits += 1
            elif name == "Bash":
                cmd = str(inp.get("command", ""))
                if "git commit" in cmd:
                    commits += 1
                if _TEST_RE.search(cmd):
                    tests += 1
    a_texts = [(e, t) for e in events if e.role == "assistant" for t in e.texts]
    last_a = a_texts[-1][1] if a_texts else ""
    last_ev = events[-1] if events else None

    if prompts:
        asked = "요청: \"%s\"" % _short(red.redact(prompts[0]), 70)
        if len(prompts) > 1:
            asked += " 외 %d건" % (len(prompts) - 1)
    else:
        asked = "요청: (사람이 입력한 프롬프트 없음 - 이어서 진행된 세션)"

    parts = []
    if files:
        names = list(files)
        parts.append("파일 %d개 수정(%s%s)" % (len(names), ", ".join(names[:3]),
                                              " 외" if len(names) > 3 else ""))
    if commits:
        parts.append("커밋 %d회" % commits)
    if tests:
        parts.append("테스트 %d회 실행" % tests)
    first = ""
    if last_a:
        sents = split_sentences(red.redact(last_a))
        first = _short(sents[0], 80) if sents else ""
    done = "완료: " + (", ".join(parts) if parts else "눈에 띄는 변경 없음")
    if first:
        done += ' - 마지막 보고: "%s"' % first

    blocked = "막힘: 없음"
    tail_errors = [e for e in events[-6:] if e.errors]
    if last_ev is not None and last_ev.role == "user" and last_ev.texts and not last_ev.errors:
        blocked = "막힘: 마지막 요청에 응답이 없음 (중단/대기 중일 수 있음)"
    elif last_a and (last_a.rstrip().endswith(("?", "？")) or _BLOCK_RE.search(last_a)):
        sents = split_sentences(red.redact(last_a))
        pick = (next((s for s in reversed(sents) if _BLOCK_RE.search(s)), None)
                or next((s for s in reversed(sents) if s.endswith(("?", "？"))), None) or sents[-1])
        blocked = "막힘: \"%s\"" % _short(pick, 90)
    elif tail_errors:
        blocked = "막힘: 마지막 도구 오류 - %s" % _short(red.redact(tail_errors[-1].errors[-1]), 80)
    return {"asked": asked, "done": done, "blocked": blocked,
            "messages": len(events), "files": list(files), "commits": commits, "tests": tests,
            "context": _llm_context(prompts, files, last_a, red)}


def _llm_context(prompts, files, last_a, red):
    return red.redact("사용자 요청들:\n- " + "\n- ".join(_short(p, 200) for p in prompts[:5]) +
                      "\n수정한 파일: " + ", ".join(list(files)[:10]) +
                      "\n마지막 어시스턴트 메시지: " + _short(last_a, 600))


def build_report(projects_dir, start, end, llm=None, redactor=None, stats=None):
    """프로젝트별 3줄 보고를 [{project, session_count, lines[3], messages}] 로."""
    red = redactor or Redactor()
    events = load_events(projects_dir, start, end, stats)
    groups = OrderedDict()
    for e in events:
        groups.setdefault(e.cwd, []).append(e)
    report = []
    for cwd, evs in groups.items():
        s = summarize_project(evs, red)
        lines = [s["asked"], s["done"], s["blocked"]]
        if llm is not None:
            got = llm_summarize_lines(llm, s["context"])   # 이미 마스킹된 컨텍스트
            if got:
                lines = [red.redact(l) for l in got]
        report.append({"project": project_label(red.redact(cwd)), "sessions": len({e.session for e in evs}),
                       "messages": s["messages"], "lines": lines})
    report.sort(key=lambda r: r["project"])
    return report


def render_report(report, start, end):
    head = "야간 작업 보고 (%s ~ %s)" % (start.astimezone().strftime("%m-%d %H:%M"),
                                      end.astimezone().strftime("%m-%d %H:%M"))
    if not report:
        return head + "\n  이 시간대에 Claude Code 활동이 없었어요."
    out = [head]
    for r in report:
        out.append("")
        out.append("■ %s (세션 %d, 메시지 %d)" % (r["project"], r["sessions"], r["messages"]))
        for i, l in enumerate(r["lines"], 1):
            out.append("  %d) %s" % (i, l))
    return "\n".join(out)
