# generated from jjkit@eeae9c07; 직접 고치지 말 것
"""Claude Code 트랜스크립트(``~/.claude/projects/**/*.jsonl``) 읽기.

기준 구현: til-drafter/til_drafter/transcripts.py, voice-journal/vjournal/claude_ingest.py.

규칙(세 곳에 흩어져 있던 것을 한 벌로):
- ``isSidechain``(서브에이전트), ``isMeta``, ``isCompactSummary``, 모델이 ``<synthetic>``인 줄은 뺀다.
  ``subagents/`` 폴더도 건너뛴다. ``include_sidechain=True``로 바꿀 수 있다.
- ``<system-reminder>...</system-reminder>`` 블록은 지우고, 닫히지 않은 태그가 남으면 그 텍스트는 버린다.
- 명령 출력/훅 주입(``<command-name>``, ``Caveat:`` 등)으로 시작하는 텍스트는 사람이 쓴 게 아니므로 버린다.
- 깨진 줄은 건너뛰고 ``stats["malformed"]``에 센다.
"""
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

__all__ = ["Message", "default_projects_dir", "parse_timestamp", "strip_reminders", "clean_text",
           "iter_jsonl_files", "iter_records", "extract_message", "iter_messages", "load_messages"]

NOISE_PREFIXES = (
    "Caveat:",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "<user-prompt-submit-hook>",
    "<ide_opened_file>",
    "<ide_selection>",
    "[Request interrupted",
    "This session is being continued from a previous conversation",
)
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I)
_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?\s*(Z|z|[+-]\d{2}:?\d{2})?$"
)


@dataclass
class Message:
    ts: datetime
    role: str                     # "user"(사람 입력) | "assistant"
    texts: list[str]
    tools: list[tuple[str, dict]] = field(default_factory=list)   # assistant의 (도구 이름, 입력)
    cwd: str = ""
    session_id: str = ""
    uuid: str = ""
    path: str = ""
    sidechain: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.texts)


def default_projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    if base:
        return Path(base) / "projects"
    return Path.home() / ".claude" / "projects"


def parse_timestamp(value: Any) -> datetime | None:
    """ISO 8601 문자열 -> aware datetime. 시간대가 없으면 UTC로 본다. 실패 시 None."""
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


def strip_reminders(text: str) -> str:
    return _REMINDER.sub("", text or "")


def clean_text(text: Any) -> str | None:
    """주입/노이즈 텍스트면 None, 아니면 정리된 텍스트."""
    if not isinstance(text, str):
        return None
    t = text
    if "<system-reminder" in t.lower():
        t = strip_reminders(t)
        if "<system-reminder" in t.lower():  # 닫히지 않은 태그
            return None
    t = t.strip()
    if not t or t.startswith(NOISE_PREFIXES):
        return None
    return t


def iter_jsonl_files(root: str | Path) -> Iterator[Path]:
    """root가 파일이면 그 파일만, 폴더면 하위 ``*.jsonl`` (``subagents/`` 제외), 이름순."""
    p = Path(root)
    if p.is_file():
        yield p
        return
    if not p.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames[:] = sorted(d for d in dirnames if d != "subagents")
        for fn in sorted(filenames):
            if fn.endswith(".jsonl"):
                yield Path(dirpath) / fn


def _stats(stats: dict | None) -> dict:
    s = stats if stats is not None else {}
    for k in ("files", "lines", "malformed", "messages"):
        s.setdefault(k, 0)
    return s


def iter_records(root: str | Path, *, stats: dict | None = None,
                 modified_after: float | None = None) -> Iterator[tuple[Path, dict]]:
    """(파일 경로, JSON 객체). ``modified_after``(epoch)보다 오래된 파일은 열지 않는다."""
    st = _stats(stats)
    for path in iter_jsonl_files(root):
        try:
            if modified_after is not None and path.stat().st_mtime < modified_after:
                continue
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        st["files"] += 1
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                st["lines"] += 1
                try:
                    obj = json.loads(line)
                except ValueError:
                    st["malformed"] += 1
                    continue
                if not isinstance(obj, dict):
                    st["malformed"] += 1
                    continue
                yield path, obj


def extract_message(obj: dict, *, include_sidechain: bool = False) -> tuple[str, list[str], list] | None:
    """JSON 객체 -> (role, texts, tools) 또는 None(버릴 줄)."""
    typ = obj.get("type")
    if typ not in ("user", "assistant"):
        return None
    if obj.get("isSidechain") and not include_sidechain:
        return None
    if obj.get("isMeta") or obj.get("isCompactSummary"):
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict) or msg.get("model") == "<synthetic>":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        blocks: list = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = content
    else:
        return None
    texts, tools = [], []
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
            name, inp = b.get("name"), b.get("input")
            if isinstance(name, str) and name:
                tools.append((name, inp if isinstance(inp, dict) else {}))
        # tool_result / thinking / image 는 버린다
    if not texts and not tools:
        return None
    return typ, texts, tools


def iter_messages(root: str | Path | None = None, *, since: datetime | None = None,
                  until: datetime | None = None, include_sidechain: bool = False,
                  stats: dict | None = None) -> Iterator[Message]:
    """파일 순서대로 메시지를 낸다(정렬은 ``load_messages``). since <= ts < until."""
    st = _stats(stats)
    if since is not None and since.tzinfo is None:  # naive는 시스템 로컬 시각으로 본다
        since = since.astimezone()
    if until is not None and until.tzinfo is None:
        until = until.astimezone()
    cutoff = since.timestamp() - 86400 if since is not None else None
    for path, obj in iter_records(root if root is not None else default_projects_dir(),
                                  stats=st, modified_after=cutoff):
        ts = parse_timestamp(obj.get("timestamp"))
        if ts is None:
            continue
        if since is not None and ts < since:
            continue
        if until is not None and ts >= until:
            continue
        got = extract_message(obj, include_sidechain=include_sidechain)
        if got is None:
            continue
        role, texts, tools = got
        sid = obj.get("sessionId")
        st["messages"] += 1
        yield Message(ts=ts, role=role, texts=texts, tools=tools,
                      cwd=obj.get("cwd") if isinstance(obj.get("cwd"), str) else "",
                      session_id=sid if isinstance(sid, str) and sid else path.stem,
                      uuid=obj.get("uuid") if isinstance(obj.get("uuid"), str) else "",
                      path=str(path), sidechain=bool(obj.get("isSidechain")))


def load_messages(root: str | Path | None = None, **kw: Any) -> list[Message]:
    return sorted(iter_messages(root, **kw), key=lambda m: m.ts)
