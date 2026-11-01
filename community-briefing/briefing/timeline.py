"""Issue-timeline stub (roadmap): append briefing items to one JSON file per matched keyword.

File: <timeline_dir>/<keyword>.json  ->  {"keyword": ..., "events": [{date, title, url, sources, summary}]}
A future viewer can render these as "이슈 타임라인" (how a topic evolved day by day).
"""
import json
import os
import re


def _slug(keyword):
    return re.sub(r"[^\w.\-]+", "_", keyword.strip().lower()).strip("_") or "keyword"


def append(items, timeline_dir, date_str):
    os.makedirs(timeline_dir, exist_ok=True)
    touched = []
    for kw in sorted({k for item in items for k in item.matched}):
        path = os.path.join(timeline_dir, _slug(kw) + ".json")
        data = {"keyword": kw, "events": []}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (ValueError, OSError):
                pass  # corrupt file: start over rather than crash
        known = {e.get("url") for e in data.get("events", [])}
        for item in items:
            if kw in item.matched and item.url not in known:
                data.setdefault("events", []).append({"date": date_str, "title": item.title, "url": item.url,
                                                      "sources": item.sources, "summary": item.brief})
        data["events"].sort(key=lambda e: e.get("date", ""))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        touched.append(path)
    return touched
