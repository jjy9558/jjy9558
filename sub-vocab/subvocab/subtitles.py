"""Parse .srt and .vtt subtitle files into clean caption lines.

Handles SRT, WebVTT (including NOTE/STYLE blocks and cue settings), inline timing
and style tags, and YouTube auto-caption "rolling" cues where the previous line is
repeated in the next cue.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from typing import List


class SubtitleError(ValueError):
    """Raised when a subtitle file cannot be read or has no cue timings."""


@dataclass(frozen=True)
class Caption:
    """One cleaned subtitle line with the time it first appeared."""

    start: float
    text: str
    source: str = ""


@dataclass
class _Cue:
    start: float
    lines: List[str] = field(default_factory=list)


_TS = r"(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}"
_TIME_LINE_RE = re.compile(r"^\s*(" + _TS + r")\s*-->\s*(" + _TS + r")")
_TS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})$")
_TAG_RE = re.compile(r"<[^>]*>")
_SOUND_RE = re.compile(r"\[[^\]]*\]")  # [Music], [Applause]
_DASH_RE = re.compile(r"^[-–—]\s*")


def decode_bytes(data: bytes) -> str:
    """Decode subtitle bytes: UTF-8 (with or without BOM), then CP949 for Korean-era files."""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_timestamp(text: str) -> float:
    """Convert "HH:MM:SS,mmm", "HH:MM:SS.mmm" or "MM:SS.mmm" to seconds."""
    m = _TS_RE.match(text.strip())
    if not m:
        raise SubtitleError(f"잘못된 시간 형식: {text!r}")
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    millis = int(m.group(4).ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def clean_line(raw: str) -> str:
    """Strip markup, sound tags and extra whitespace from one subtitle line."""
    text = _TAG_RE.sub("", raw)  # <c>, </c>, <i>, <00:00:01.234>, <v Name>
    text = html.unescape(text)
    text = _SOUND_RE.sub(" ", text)
    text = text.replace("♪", " ").replace("♫", " ")
    text = " ".join(text.split())
    return _DASH_RE.sub("", text).strip()


def parse_cues(text: str) -> List[_Cue]:
    """Split subtitle text into cues. Works for both SRT and WebVTT.

    Lines outside a cue (WEBVTT header, cue numbers, NOTE/STYLE blocks) are ignored.
    A blank or whitespace-only line ends a cue only after it has text. YouTube writes a
    single-space line right after the timing line, and some tools leave it empty.
    """
    cues: List[_Cue] = []
    current = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = _TIME_LINE_RE.match(raw)
        if m:
            if current is not None:
                cues.append(current)
            current = _Cue(parse_timestamp(m.group(1)))
            continue
        if current is None:
            continue
        if raw.strip() == "":
            if any(line.strip() for line in current.lines):
                cues.append(current)
                current = None
            continue
        current.lines.append(raw)
    if current is not None:
        cues.append(current)
    return cues


def build_captions(cues: List[_Cue], source: str = "") -> List[Caption]:
    """Turn cues into unique caption lines in time order.

    - Consecutive identical lines are dropped (rolling duplicates).
    - A line that extends the previous line ("hello" -> "hello there") replaces it,
      keeping the earlier start time, so the growing line is not counted twice.
    """
    captions: List[Caption] = []
    for cue in cues:
        for raw in cue.lines:
            text = clean_line(raw)
            if not text:
                continue
            if captions:
                last = captions[-1]
                if text == last.text:
                    continue
                if text.startswith(last.text + " "):
                    captions[-1] = Caption(last.start, text, source)
                    continue
            captions.append(Caption(cue.start, text, source))
    return captions


def parse_subtitle_text(text: str, source: str = "") -> List[Caption]:
    """Parse decoded subtitle text (SRT or VTT) into cleaned captions."""
    if "-->" not in text:
        raise SubtitleError(f"자막 시간 정보(-->)를 찾을 수 없습니다: {source or '입력'}")
    return build_captions(parse_cues(text), source)


def load_captions(path: str) -> List[Caption]:
    """Read a .srt or .vtt file from disk and return its cleaned captions."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise SubtitleError(f"파일을 읽을 수 없습니다: {path} ({exc})") from exc
    return parse_subtitle_text(decode_bytes(data), source=os.path.basename(path))
