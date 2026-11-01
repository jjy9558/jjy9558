"""SRT / VTT parsing and writing. Times are integer milliseconds."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class Cue:
    index: int
    start: int  # ms
    end: int  # ms
    text: str

    @property
    def duration(self) -> int:
        return self.end - self.start


_TC = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})")
_TIMING = re.compile(
    r"(?P<a>(?:\d+:)?\d{1,2}:\d{1,2}[,.]\d{1,3})\s*-->\s*(?P<b>(?:\d+:)?\d{1,2}:\d{1,2}[,.]\d{1,3})"
)


def parse_timecode(s: str) -> int:
    m = _TC.fullmatch(s.strip())
    if not m:
        raise ValueError("bad timecode: %r" % s)
    h = int(m.group(1) or 0)
    frac = m.group(4).ljust(3, "0")  # "5" -> 500 ms
    return ((h * 60 + int(m.group(2))) * 60 + int(m.group(3))) * 1000 + int(frac)


def format_timecode(ms: int, sep: str = ",") -> str:
    ms = max(0, int(round(ms)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, x = divmod(rem, 1000)
    return "%02d:%02d:%02d%s%03d" % (h, m, s, sep, x)


def parse_cues(text: str) -> List[Cue]:
    """Parse SRT or VTT text (lenient: BOM, CRLF, missing/odd numbering)."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: List[Cue] = []
    for block in re.split(r"\n[ \t]*\n", text.strip("\n")):
        lines = block.split("\n")
        for pos, line in enumerate(lines):
            m = _TIMING.search(line)
            if not m:
                continue
            idx = len(cues) + 1
            if pos > 0 and lines[pos - 1].strip().isdigit():
                idx = int(lines[pos - 1].strip())
            body = "\n".join(l.rstrip() for l in lines[pos + 1:]).strip("\n")
            cues.append(Cue(idx, parse_timecode(m.group("a")), parse_timecode(m.group("b")), body))
            break
    return cues


def write_srt(cues: List[Cue], renumber: bool = True) -> str:
    out = []
    for n, c in enumerate(cues, 1):
        out.append("%d\n%s --> %s\n%s\n" % (n if renumber else c.index,
                                           format_timecode(c.start), format_timecode(c.end), c.text))
    return "\n".join(out)


def write_vtt(cues: List[Cue]) -> str:
    out = ["WEBVTT\n"]
    for c in cues:
        out.append("%s --> %s\n%s\n" % (format_timecode(c.start, "."), format_timecode(c.end, "."), c.text))
    return "\n".join(out)


def load(path: str) -> List[Cue]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return parse_cues(f.read())


def save_srt(path: str, cues: List[Cue]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(write_srt(cues))
