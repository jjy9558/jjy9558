"""Subtitle lint / limits shared by translate, review and qa."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .srt import Cue, format_timecode


@dataclass
class Limits:
    cps: float = 14.0          # max characters per second
    max_chars: int = 22        # per line (Korean default; use 42 for English)
    max_lines: int = 2
    min_dur: float = 0.7       # seconds
    max_dur: float = 7.0
    min_gap: float = 0.0       # seconds; 0 = only report overlaps


@dataclass
class Issue:
    index: int
    code: str
    severity: str  # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return "[%s] #%d %s: %s" % (self.severity, self.index, self.code, self.message)


def char_count(text: str) -> int:
    return len(text.replace("\n", ""))


def cps_of(text: str, duration_ms: int) -> float:
    return char_count(text) / max(duration_ms / 1000.0, 0.001)


def lint(cues: List[Cue], limits: Limits = None, check_numbering: bool = True) -> List[Issue]:
    L = limits or Limits()
    issues: List[Issue] = []
    prev = None
    for n, c in enumerate(cues, 1):
        def add(code, sev, msg):
            issues.append(Issue(c.index, code, sev, msg))
        if check_numbering and c.index != n:
            add("numbering", "error", "번호가 %d여야 하는데 %d입니다" % (n, c.index))
        if c.end <= c.start:
            add("duration", "error", "끝 시간이 시작 시간보다 빠르거나 같음 (%s)" % format_timecode(c.start))
        if prev is not None:
            if c.start < prev.end:
                add("overlap", "error", "이전 자막(#%d)과 %dms 겹침" % (prev.index, prev.end - c.start))
            elif (c.start - prev.end) < L.min_gap * 1000:
                add("gap", "warning", "이전 자막과 간격 %dms (최소 %dms)" % (c.start - prev.end, L.min_gap * 1000))
        text = c.text
        if not text.strip():
            add("empty", "error", "빈 자막")
        else:
            dur = c.end - c.start
            if 0 < dur < L.min_dur * 1000:
                add("too_short", "warning", "표시 시간 %.2fs < %.2fs" % (dur / 1000, L.min_dur))
            if dur > L.max_dur * 1000:
                add("too_long_dur", "warning", "표시 시간 %.2fs > %.2fs" % (dur / 1000, L.max_dur))
            if dur > 0:
                v = cps_of(text, dur)
                if v > L.cps:
                    add("cps", "warning", "읽기 속도 %.1f CPS > %.1f" % (v, L.cps))
            lines = text.split("\n")
            if len(lines) > L.max_lines:
                add("lines", "warning", "%d줄 (최대 %d줄)" % (len(lines), L.max_lines))
            for ln in lines:
                if len(ln) > L.max_chars:
                    add("line_length", "warning", "한 줄 %d자 > %d자: %s" % (len(ln), L.max_chars, ln))
                    break
            if any(not ln.strip() for ln in lines):
                add("empty_line", "warning", "자막 안에 빈 줄이 있음")
        prev = c
    return issues
