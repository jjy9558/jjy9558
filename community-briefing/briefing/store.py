"""Dedupe: URL/title normalization, in-run merging and a sqlite 'seen' store across runs."""
import json
import re
import sqlite3
from datetime import timedelta
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|ref|ref_src|source)$", re.I)


def normalize_url(url):
    p = urlsplit(url.strip())
    host = p.netloc.lower()
    for prefix in ("www.", "old.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                             if not _TRACKING.match(k)))
    scheme = "https" if p.scheme in ("http", "https") else p.scheme
    return urlunsplit((scheme, host, p.path.rstrip("/") or "/", query, ""))


def normalize_title(title):
    t = title.lower()
    t = re.sub(r"^(show|ask|tell|launch) hn\s*:\s*", "", t)
    t = re.sub(r"[^\w\s]", " ", t)  # \w keeps Hangul
    return re.sub(r"\s+", " ", t).strip()


def similar(a, b, threshold):
    if not a or not b:
        return False
    return a == b or SequenceMatcher(None, a, b).ratio() >= threshold


def merge_items(items, threshold=0.82, multi_source_boost=0.5):
    """Merge the same story found in several sources; the merged item lists every source."""
    merged = []
    for item in sorted(items, key=lambda i: -i.score):
        urls, title = {normalize_url(l["url"]) for l in item.links}, normalize_title(item.title)
        for ex in merged:
            ex_urls = {normalize_url(l["url"]) for l in ex.links}
            if urls & ex_urls or similar(title, normalize_title(ex.title), threshold):
                new_source = False
                for link in item.links:
                    if normalize_url(link["url"]) not in ex_urls or link["source"] not in ex.sources:
                        new_source = new_source or link["source"] not in ex.sources
                        ex.links.append(link)
                if len(item.summary) > len(ex.summary):
                    ex.summary = item.summary
                ex.comments = max(ex.comments or 0, item.comments or 0) or None
                ex.matched = sorted(set(ex.matched) | set(item.matched))
                if new_source:
                    ex.score += multi_source_boost
                break
        else:
            merged.append(item)
    return sorted(merged, key=lambda i: -i.score)


class SeenStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, title TEXT, "
                        "sources TEXT, first_seen TEXT)")

    def filter_new(self, items, now, threshold=0.82, remember_days=14):
        cutoff = (now - timedelta(days=remember_days)).isoformat()
        recent = [r[0] for r in self.db.execute("SELECT title FROM seen WHERE first_seen >= ?", (cutoff,))]
        fresh = []
        for item in items:
            urls = [normalize_url(l["url"]) for l in item.links]
            hit = self.db.execute("SELECT 1 FROM seen WHERE url IN (%s) LIMIT 1" % ",".join("?" * len(urls)),
                                  urls).fetchone()
            title = normalize_title(item.title)
            if not hit and not any(similar(title, t, threshold) for t in recent):
                fresh.append(item)
        return fresh

    def mark(self, items, now):
        with self.db:
            for item in items:
                for link in item.links:
                    self.db.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?,?)",
                                    (normalize_url(link["url"]), normalize_title(item.title),
                                     json.dumps(item.sources, ensure_ascii=False), now.isoformat()))

    def close(self):
        self.db.close()
