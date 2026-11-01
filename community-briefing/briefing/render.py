"""Markdown + JSON briefing output."""
import re
from collections import OrderedDict


def cluster(items):
    """Group items by topic; topics ordered by their best item's score."""
    groups = OrderedDict()
    for item in sorted(items, key=lambda i: -i.score):
        groups.setdefault(item.topic or "기타", []).append(item)
    return groups


def _md_escape(text):
    return re.sub(r"([\[\]])", r"\\\1", text)


def render_markdown(date_str, overview, clusters, meta):
    out = ["# 커뮤니티 브리핑 — %s" % date_str, "", "## 오늘의 요약", ""]
    out += ["%d. %s" % (n, line) for n, line in enumerate(overview, 1) if line]
    for topic, items in clusters.items():
        out += ["", "## %s (%d)" % (topic, len(items)), ""]
        for item in items:
            # trailing two spaces = Markdown hard line break inside the list item
            out.append("- **[%s](%s)**  " % (_md_escape(item.title), item.url))
            if item.brief and item.brief != item.title:
                out.append("  %s  " % item.brief)
            src = " · ".join("[%s](%s)" % (_md_escape(l["source"]), l["url"]) for l in item.links)
            extra = " · 댓글 %d" % item.comments if item.comments else ""
            out.append("  출처: %s%s" % (src, extra))
    if not clusters:
        out += ["", "_새로 올라온 관심 글이 없습니다._"]
    if meta.get("source_errors"):
        out += ["", "_가져오지 못한 소스: %s_" % "; ".join(meta["source_errors"])]
    out += ["", "---", "_생성 %s · 요약 방식: %s · %d건_" % (meta["generated_at"], meta["summary_mode"],
                                                    meta["count"])]
    return "\n".join(out) + "\n"


def to_json(date_str, overview, clusters, meta):
    return {
        "date": date_str, **meta, "overview": overview,
        "clusters": [{"topic": t, "items": [i.to_dict() for i in items]} for t, items in clusters.items()],
    }
