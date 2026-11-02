"""SRT/ASS 파싱과 자막 검사."""
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .model import FAIL, OK, SKIP, WARN, Result

CAT = "자막"


@dataclass
class Cue:
    start: float
    end: float
    text: str
    anchor_y: Optional[float] = None   # 0~1 (화면 위=0). ASS에서 알 수 있을 때만
    align: Optional[int] = None

    @property
    def lines(self):
        return [l.strip() for l in self.text.split("\n") if l.strip()]


def _srt_time(s):
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", s.strip())
    if not m:
        raise ValueError(s)
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")[:3]) / 1000.0


def parse_srt(text: str) -> List[Cue]:
    cues = []
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->")[:2]
        try:
            st, en = _srt_time(a), _srt_time(b.split()[0])
        except (ValueError, IndexError):
            continue
        body = re.sub(r"<[^>]+>|\{\\[^}]*\}", "", "\n".join(lines[idx + 1:]))
        cues.append(Cue(st, en, body.strip()))
    return cues


def _ass_time(s):
    m = re.match(r"(\d+):(\d+):(\d+)[.](\d+)", s.strip())
    if not m:
        raise ValueError(s)
    h, mi, se, cs = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(cs.ljust(2, "0")[:2]) / 100.0


def parse_ass(text: str) -> Tuple[List[Cue], dict]:
    """-> (cues, {'res_y': int}). 위치(\\pos, \\an, MarginV)를 anchor_y(0~1)로 환산."""
    text = text.lstrip("\ufeff")
    res_y = 288
    styles = {}
    style_fmt, ev_fmt = [], []
    section = ""
    raw = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s.lower()
            continue
        if section == "[script info]":
            m = re.match(r"PlayResY:\s*(\d+)", s, re.I)
            if m:
                res_y = int(m.group(1))
        elif section.endswith("styles]"):
            if s.lower().startswith("format:"):
                style_fmt = [x.strip().lower() for x in s[7:].split(",")]
            elif s.lower().startswith("style:") and style_fmt:
                vals = [x.strip() for x in s[6:].split(",")]
                d = dict(zip(style_fmt, vals))
                styles[d.get("name", "")] = d
        elif section == "[events]":
            if s.lower().startswith("format:"):
                ev_fmt = [x.strip().lower() for x in s[7:].split(",")]
            elif s.lower().startswith("dialogue:") and ev_fmt:
                raw.append(s[9:].lstrip())
    cues = []
    for r in raw:
        vals = r.split(",", len(ev_fmt) - 1)
        if len(vals) < len(ev_fmt):
            continue
        d = dict(zip(ev_fmt, vals))
        try:
            st, en = _ass_time(d["start"]), _ass_time(d["end"])
        except (KeyError, ValueError):
            continue
        body = d.get("text", "")
        sty = styles.get(d.get("style", "").strip(), {})
        align = int(sty.get("alignment", 2) or 2)
        try:
            mv = int(d.get("marginv", 0) or 0) or int(sty.get("marginv", 0) or 0)
        except ValueError:
            mv = 0
        m = re.search(r"\\an(\d)", body)
        if m:
            align = int(m.group(1))
        y = None
        m = re.search(r"\\pos\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)", body)
        if m and res_y:
            y = float(m.group(2)) / res_y
        elif res_y:
            if align in (1, 2, 3):
                y = 1.0 - mv / float(res_y)
            elif align in (4, 5, 6):
                y = 0.5
            else:
                y = mv / float(res_y)
        clean = re.sub(r"\{[^}]*\}", "", body).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
        cues.append(Cue(st, en, clean.strip(), y, align))
    return cues, {"res_y": res_y}


def load_cues(path: str) -> List[Cue]:
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        t = f.read()
    if path.lower().endswith(".ass") or path.lower().endswith(".ssa"):
        return parse_ass(t)[0]
    return parse_srt(t)


def cues_text(cues: List[Cue]) -> str:
    return "\n".join(c.text for c in cues)


# ---------------------------------------------------------------- 검사
def _ratio_status(bad, total, fail_ratio):
    if not bad:
        return OK
    return FAIL if total and bad / float(total) >= fail_ratio else WARN


def check_subtitles(cues: List[Cue], cfg: dict, is_ass: bool = False) -> List[Result]:
    c = cfg["subtitle"]
    out = []
    if not cues:
        return [Result("자막 파싱", WARN, "자막 큐가 없습니다", CAT)]
    # 줄 길이
    nlines = long_lines = 0
    ex = None
    too_many_lines = 0
    for q in cues:
        if len(q.lines) > c["max_lines"]:
            too_many_lines += 1
        for l in q.lines:
            nlines += 1
            if len(l) > c["max_chars_per_line"]:
                long_lines += 1
                ex = ex or l
    st = _ratio_status(long_lines, nlines, c["fail_ratio"])
    msg = ("%d/%d줄이 %d자 초과 (예: '%s')" % (long_lines, nlines, c["max_chars_per_line"], ex)
           if long_lines else "모든 줄 <= %d자" % c["max_chars_per_line"])
    if too_many_lines:
        msg += "; %d개 큐가 %d줄 초과" % (too_many_lines, c["max_lines"])
        st = WARN if st == OK else st
    out.append(Result("자막 줄 길이", st, msg, CAT))
    # CPS / 표시시간
    fast, short = 0, 0
    worst = 0.0
    for q in cues:
        dur = max(q.end - q.start, 0.001)
        cps = len(re.sub(r"\s", "", q.text)) / dur
        worst = max(worst, cps)
        if cps > c["max_cps"]:
            fast += 1
        if dur < c["min_cue_seconds"]:
            short += 1
    st = _ratio_status(fast, len(cues), c["fail_ratio"])
    msg = ("%d/%d개 큐가 %.0f CPS 초과 (최대 %.1f)" % (fast, len(cues), c["max_cps"], worst)
           if fast else "최대 %.1f CPS (한도 %.0f)" % (worst, c["max_cps"]))
    if short:
        msg += "; %d개 큐가 %.1fs 미만" % (short, c["min_cue_seconds"])
        st = WARN if st == OK else st
    out.append(Result("자막 속도(CPS)", st, msg, CAT))
    # 하단 UI 영역
    zone = 1.0 - c["ui_bottom_ratio"]
    pct = int(c["ui_bottom_ratio"] * 100)
    if is_ass and any(q.anchor_y is not None for q in cues):
        bad = [q for q in cues if q.anchor_y is not None and q.anchor_y > zone]
        if bad:
            out.append(Result("자막 하단 UI 영역", _ratio_status(len(bad), len(cues), c["fail_ratio"]),
                              "%d/%d개 큐가 하단 %d%% 영역에 위치 (제목/채널/버튼과 겹침)" % (len(bad), len(cues), pct), CAT))
        else:
            out.append(Result("자막 하단 UI 영역", OK, "모든 큐가 하단 %d%% 위" % pct, CAT))
    else:
        out.append(Result("자막 하단 UI 영역", WARN,
                          "SRT는 위치 정보가 없어 확인 불가 - 번인 시 하단 %d%%는 비우세요" % pct, CAT))
    return out


def skipped() -> Result:
    return Result("자막 검사", SKIP, "영상 옆에 .srt/.ass 없음", CAT)
