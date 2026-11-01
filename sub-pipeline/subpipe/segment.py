"""Turn whisper segments (with word timestamps) into subtitle-friendly cues."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

from .srt import Cue


@dataclass
class SegConfig:
    max_chars: int = 42       # per line
    max_lines: int = 2
    min_dur: float = 1.0      # seconds
    max_dur: float = 7.0
    min_gap: float = 0.08     # seconds between cues
    gap_break: float = 0.8    # silence that forces a new cue
    min_chars: int = 12       # sentence end only breaks after this many chars


@dataclass
class Unit:
    start: int
    end: int
    raw: str  # may carry a leading space


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _ms(sec: Any) -> int:
    return int(round(float(sec) * 1000))


def units_from_segments(segments: Any, cfg: SegConfig) -> List[Unit]:
    units: List[Unit] = []
    for seg in segments:
        words = _get(seg, "words") or []
        if words:
            for w in words:
                raw = _get(w, "word")
                if raw is None or not str(raw).strip():
                    continue
                units.append(Unit(_ms(_get(w, "start")), _ms(_get(w, "end")), str(raw)))
            continue
        # no word timestamps: spread time over tokens proportionally
        s, e = _ms(_get(seg, "start")), _ms(_get(seg, "end"))
        toks: List[str] = []
        for tok in (_get(seg, "text") or "").split():
            step = max(1, cfg.max_chars // 2)
            if len(tok) > cfg.max_chars:
                chunks = [tok[i:i + step] for i in range(0, len(tok), step)]
                toks.append(" " + chunks[0])
                toks.extend(chunks[1:])
            else:
                toks.append(" " + tok)
        total = sum(len(t) for t in toks) or 1
        pos = s
        for t in toks:
            nxt = pos + int((e - s) * len(t) / total)
            units.append(Unit(pos, nxt, t))
            pos = nxt
        if toks:
            units[-1].end = e
    return units


def join_text(units: List[Unit]) -> str:
    return re.sub(r"\s+", " ", "".join(u.raw for u in units)).strip()


def wrap_lines(text: str, max_chars: int, max_lines: int = 2) -> Optional[List[str]]:
    """Balanced wrap; None if it cannot fit in max_lines of max_chars."""
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    if max_lines < 2:
        return None
    if max_lines == 2:
        best = None
        for m in re.finditer(" ", text):
            a, b = text[:m.start()], text[m.end():]
            if len(a) <= max_chars and len(b) <= max_chars:
                score = abs(len(a) - len(b))
                if best is None or score < best[0]:
                    best = (score, [a, b])
        if best:
            return best[1]
        if " " not in text and len(text) <= 2 * max_chars:
            mid = (len(text) + 1) // 2
            return [text[:mid], text[mid:]]
        return None
    lines: List[str] = []
    cur = ""
    for w in text.split(" "):
        if len(w) > max_chars:
            return None
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines if len(lines) <= max_lines else None


def wrap_lines_force(text: str, max_chars: int, max_lines: int = 2) -> List[str]:
    """Like wrap_lines but never fails (overlong text is still split; QA flags it)."""
    r = wrap_lines(text, max_chars, max_lines)
    if r is not None:
        return r
    text = " ".join(text.split())
    if max_lines < 2 or len(text) < 2:
        return [text]
    mid = len(text) // 2
    spaces = [m.start() for m in re.finditer(" ", text)]
    cut = min(spaces, key=lambda p: abs(p - mid)) if spaces else mid
    return [text[:cut].strip(), text[cut:].strip()]


_SENT_END = ".?!。？！…"


def _group(units: List[Unit], cfg: SegConfig) -> List[List[Unit]]:
    groups: List[List[Unit]] = []
    cur: List[Unit] = []
    max_dur, gap_break = int(cfg.max_dur * 1000), int(cfg.gap_break * 1000)
    for u in units:
        if cur:
            cur_text = join_text(cur)
            cand_text = join_text(cur + [u])
            flush = (
                u.start - cur[-1].end >= gap_break
                or u.end - cur[0].start > max_dur
                or wrap_lines(cand_text, cfg.max_chars, cfg.max_lines) is None
                or (cur_text[-1] in _SENT_END and len(cur_text) >= cfg.min_chars)
                or (cur_text[-1] in ",;:" and len(cur_text) >= cfg.max_chars)
            )
            if flush:
                groups.append(cur)
                cur = []
        cur.append(u)
    if cur:
        groups.append(cur)
    return groups


def _merge_short(groups: List[List[Unit]], cfg: SegConfig) -> List[List[Unit]]:
    min_dur, max_dur, gap_break = int(cfg.min_dur * 1000), int(cfg.max_dur * 1000), int(cfg.gap_break * 1000)

    def can(a: List[Unit], b: List[Unit]) -> bool:
        return (b[0].start - a[-1].end < gap_break
                and b[-1].end - a[0].start <= max_dur
                and wrap_lines(join_text(a + b), cfg.max_chars, cfg.max_lines) is not None)

    out: List[List[Unit]] = []
    i = 0
    while i < len(groups):
        g = groups[i]
        while g[-1].end - g[0].start < min_dur and i + 1 < len(groups) and can(g, groups[i + 1]):
            g = g + groups[i + 1]
            i += 1
        out.append(g)
        i += 1
    if len(out) > 1 and out[-1][-1].end - out[-1][0].start < min_dur and can(out[-2], out[-1]):
        out[-2] = out[-2] + out.pop()
    return out


def build_cues(segments: Any, cfg: Optional[SegConfig] = None) -> List[Cue]:
    cfg = cfg or SegConfig()
    units = units_from_segments(segments, cfg)
    if not units:
        return []
    groups = _merge_short(_group(units, cfg), cfg)
    cues = []
    for g in groups:
        lines = wrap_lines_force(join_text(g), cfg.max_chars, cfg.max_lines)
        cues.append(Cue(0, g[0].start, g[-1].end, "\n".join(lines)))
    min_dur, gap = int(cfg.min_dur * 1000), int(round(cfg.min_gap * 1000))
    for i, c in enumerate(cues):
        limit = cues[i + 1].start - gap if i + 1 < len(cues) else None
        want = c.start + min_dur
        if c.end < want:
            c.end = want if limit is None else max(c.end, min(want, limit))
        if limit is not None and c.end > limit:
            c.end = max(limit, c.start + 1)
        c.index = i + 1
    return cues
