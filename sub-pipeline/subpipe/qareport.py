"""Markdown QA report built on qa.lint."""
from __future__ import annotations

from collections import OrderedDict
from typing import List, Optional

from .qa import Issue, Limits, cps_of, lint
from .srt import Cue

CODE_LABELS = OrderedDict([
    ("numbering", "번호 연속성"), ("overlap", "겹침"), ("duration", "시간 역전/0초"),
    ("empty", "빈 큐"), ("empty_line", "빈 줄"), ("gap", "간격 부족"),
    ("too_short", "표시 시간 짧음"), ("too_long_dur", "표시 시간 김"),
    ("cps", "초당 글자 수(CPS)"), ("line_length", "줄 길이"), ("lines", "줄 수"),
])


def render_md(name: str, cues: List[Cue], limits: Limits, issues: Optional[List[Issue]] = None,
              notice: Optional[str] = None) -> str:
    issues = lint(cues, limits) if issues is None else issues
    errs = sum(1 for i in issues if i.severity == "error")
    cps_vals = [cps_of(c.text, c.end - c.start) for c in cues if c.text.strip() and c.end > c.start]
    out = ["# QA 리포트: %s" % name, ""]
    if notice:
        out += [notice, ""]
    out += ["- 자막 수: %d" % len(cues),
            "- 오류 %d건 / 경고 %d건" % (errs, len(issues) - errs),
            "- 기준: CPS %g 이하, 한 줄 %d자 이하, %d줄 이하, 표시 시간 %g~%gs" % (
                limits.cps, limits.max_chars, limits.max_lines, limits.min_dur, limits.max_dur)]
    if cps_vals:
        out.append("- CPS 평균 %.1f / 최대 %.1f" % (sum(cps_vals) / len(cps_vals), max(cps_vals)))
    out += ["", "## 항목별 요약", "", "| 항목 | 건수 |", "|---|---|"]
    for code, label in CODE_LABELS.items():
        out.append("| %s | %d |" % (label, sum(1 for i in issues if i.code == code)))
    out += ["", "## 상세", ""]
    if not issues:
        out.append("문제 없음.")
    else:
        out += ["| 번호 | 항목 | 등급 | 내용 |", "|---|---|---|---|"]
        for i in issues:
            msg = i.message.replace("|", "\\|").replace("\n", " ")
            out.append("| %d | %s | %s | %s |" % (i.index, CODE_LABELS.get(i.code, i.code), i.severity, msg))
    return "\n".join(out) + "\n"
