"""community-briefing 어댑터: out/YYYY-MM-DD[-N].json 의 overview(핵심 요약 줄)를 읽는다.

형식(community-briefing/briefing/render.py ``to_json``): {date, generated_at, count, overview: [줄...],
clusters: [{topic, items: [{title, url, summary, ...}]}]}. 하루에 새 글이 있는 재실행이면
``YYYY-MM-DD-2.json`` 처럼 번호가 붙으므로 가장 큰 번호를 쓴다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from ..model import Context, Item, SourceMissing
from ..speech import shorten

NAME = "community_briefing"
SECTION = "커뮤니티"
DEFAULTS = {"enabled": True, "dir": "", "top": 3, "max_age_days": 1, "line_chars": 70}

_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.json$")


def _latest(folder: Path, today: dt.date, max_age: int) -> Path | None:
    best: tuple[str, int, Path] | None = None
    oldest = (today - dt.timedelta(days=max_age)).isoformat()
    for p in folder.iterdir():
        m = _FILE.match(p.name)
        if not m or m.group(1) < oldest or m.group(1) > today.isoformat():
            continue
        key = (m.group(1), int(m.group(2) or 1), p)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best else None


def collect(cfg: dict, ctx: Context) -> list[Item]:
    if not cfg.get("dir"):
        raise SourceMissing("sources.community_briefing.dir (out 폴더)를 설정하세요")
    folder = ctx.path(cfg["dir"])
    if not folder.is_dir():
        raise SourceMissing("out 폴더가 없어요: %s" % folder.name)
    path = _latest(folder, ctx.today, int(cfg["max_age_days"]))
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [str(x).strip() for x in (data.get("overview") or []) if str(x).strip()]
    if not lines:  # 요약이 없으면 상위 군집의 글 제목으로 대신한다
        for cl in data.get("clusters") or []:
            for it in cl.get("items") or []:
                if it.get("title"):
                    lines.append(str(it["title"]))
    lines = lines[:max(0, int(cfg["top"]))]
    if not lines:
        return []
    chars = int(cfg["line_chars"])
    items = []
    for i, line in enumerate(lines):
        spoken = shorten(line, chars)
        items.append(Item(NAME, "news-line", "커뮤니티 %d. %s" % (i + 1, shorten(line, 60)), line,
                          30 - 2 * i, "커뮤니티 소식: " + spoken if i == 0 else spoken,
                          shorten(line, 36), None, (), card=False))
    items.append(Item(NAME, "news", "커뮤니티 브리핑 %d줄" % len(lines),
                      "\n".join("%d. %s" % (i + 1, l) for i, l in enumerate(lines)), 20, "", "", None,
                      ("전체 보기: " + path.with_suffix(".md").name,)))
    return items
