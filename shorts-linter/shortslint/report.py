"""콘솔 표 / Markdown / JSON 리포트."""
import json
import unicodedata

from .model import ICONS, VideoReport


def dwidth(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, w):
    return s + " " * max(0, w - dwidth(s))


def summary_line(reports):
    f = sum(r.count("fail") for r in reports)
    w = sum(r.count("warn") for r in reports)
    return "영상 %d개 | ❌ %d  ⚠️ %d" % (len(reports), f, w)


def format_console(reports):
    lines = []
    for rep in reports:
        lines.append("")
        lines.append("%s %s" % (ICONS[rep.status], rep.path))
        w = max([dwidth(r.name) for r in rep.results] + [8])
        cat = None
        for r in rep.results:
            if r.category != cat:
                cat = r.category
                lines.append("  [%s]" % cat)
            lines.append("   %s  %s  %s" % (ICONS[r.status], pad(r.name, w), r.message))
    lines.append("")
    lines.append(summary_line(reports))
    return "\n".join(lines)


def format_markdown(reports):
    out = ["# shortslint 리포트", "", summary_line(reports), ""]
    for rep in reports:
        out.append("## %s %s" % (ICONS[rep.status], rep.path))
        out.append("")
        out.append("| 상태 | 분류 | 검사 | 내용 |")
        out.append("|---|---|---|---|")
        for r in rep.results:
            msg = r.message.replace("|", "\\|").replace("\n", " ")
            out.append("| %s | %s | %s | %s |" % (ICONS[r.status], r.category, r.name, msg))
        out.append("")
    return "\n".join(out)


def format_json(reports):
    return json.dumps({
        "summary": {"videos": len(reports),
                    "fail": sum(r.count("fail") for r in reports),
                    "warn": sum(r.count("warn") for r in reports)},
        "reports": [r.to_dict() for r in reports]}, ensure_ascii=False, indent=2)
