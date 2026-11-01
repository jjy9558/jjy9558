"""Parsers: RSS 2.0 / Atom feeds and simple HTML list pages."""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

from .fetch import decode
from .models import Item


class _Text(HTMLParser):
    BLOCK = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_html(text):
    if not text:
        return ""
    p = _Text()
    p.feed(text)
    p.close()
    return re.sub(r"\s+", " ", "".join(p.parts)).strip()


def parse_date(value):
    """RFC 822 (RSS) or ISO 8601 (Atom) -> ISO string in UTC, or None."""
    if not value:
        return None
    value = value.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        iso = re.sub(r"Z$", "+00:00", value)
        iso = re.sub(r"(\.\d+)", "", iso)  # Python 3.9 fromisoformat is picky about fractions
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, name):
    c = _child(el, name)
    return (c.text or "").strip() if c is not None else ""


def _comments(el, raw_summary):
    for c in el:
        if _local(c.tag) == "comments" and (c.text or "").strip().isdigit():  # slash:comments
            return int(c.text.strip())
    m = re.search(r"Comments:\s*(\d+)", raw_summary or "")  # hnrss puts it in the description
    return int(m.group(1)) if m else None


_BOILERPLATE = re.compile(r"(Article URL|Comments URL):\s*\S+|Points:\s*\d+|# Comments:\s*\d+")


def _summary(raw):
    return re.sub(r"\s+", " ", _BOILERPLATE.sub(" ", strip_html(raw))).strip()  # hnrss footer


def parse_feed(data, source, max_items=100):
    if isinstance(data, str):
        data = re.sub(r"^\s*<\?xml[^>]*\?>", "", data).encode("utf-8")
    root = ET.fromstring(data.strip())
    items = []
    if _local(root.tag) == "feed":  # Atom
        for entry in [e for e in root if _local(e.tag) == "entry"]:
            url = ""
            for link in entry:
                if _local(link.tag) == "link" and link.get("rel", "alternate") == "alternate":
                    url = link.get("href", "")
                    break
            raw = _text(entry, "summary") or _text(entry, "content")
            items.append(Item(
                title=strip_html(_text(entry, "title")), url=url.strip(), source=source,
                summary=_summary(raw), comments=_comments(entry, raw),
                published=parse_date(_text(entry, "published") or _text(entry, "updated"))))
    else:  # RSS 2.0 / RSS 1.0 (RDF)
        for it in [e for e in root.iter() if _local(e.tag) == "item"]:
            raw = _text(it, "description") or _text(it, "encoded")
            items.append(Item(
                title=strip_html(_text(it, "title")), url=_text(it, "link") or _text(it, "guid"),
                source=source, summary=_summary(raw), comments=_comments(it, raw),
                published=parse_date(_text(it, "pubDate") or _text(it, "date"))))
    return [i for i in items if i.title and i.url][:max_items]


class _LinkExtractor(HTMLParser):
    """Collect <a href> links inside every element matching tag (+ optional class)."""

    def __init__(self, tag, cls=None):
        super().__init__(convert_charrefs=True)
        self.tag, self.cls = tag.lower(), cls
        self.open, self.cur, self.links = 0, None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.open:
            if tag == self.tag:
                self.open += 1
        elif tag == self.tag and (not self.cls or self.cls in (a.get("class") or "").split()):
            self.open = 1
        if tag == "a" and self.open and self.cur is None and a.get("href"):
            self.cur = [a["href"], []]

    def handle_endtag(self, tag):
        if tag == "a" and self.cur is not None:
            self.links.append((self.cur[0], re.sub(r"\s+", " ", "".join(self.cur[1])).strip()))
            self.cur = None
        if self.open and tag == self.tag:
            self.open -= 1

    def handle_data(self, data):
        if self.cur is not None:
            self.cur[1].append(data)


def parse_html(data, source, base_url, rules, max_items=100):
    """rules: {"regex": "...(?P<url>...)...(?P<title>...)..."} or {"tag": "a", "class": "title"}.
    Optional "href_regex" filters the links found."""
    text = decode(data) if isinstance(data, bytes) else data
    if rules.get("regex"):
        pairs = [(m.group("url"), strip_html(m.group("title")))
                 for m in re.finditer(rules["regex"], text, re.S | re.I)]
    elif rules.get("tag"):
        p = _LinkExtractor(rules["tag"], rules.get("class"))
        p.feed(text)
        p.close()
        pairs = p.links
    else:
        raise ValueError("html source needs rules.regex or rules.tag")
    href_re = re.compile(rules["href_regex"]) if rules.get("href_regex") else None
    seen, items = set(), []
    for href, title in pairs:
        url = urljoin(base_url, unescape(href.strip()))
        if not title or url in seen or (href_re and not href_re.search(url)):
            continue
        seen.add(url)
        items.append(Item(title=title, url=url, source=source))
    return items[:max_items]
