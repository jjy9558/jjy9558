"""collect -> score -> merge -> dedupe -> summarize -> render -> (deliver, timeline, tts)."""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from . import deliver, render, timeline, tts
from .config import resolve
from .fetch import Fetcher
from .parse import parse_feed, parse_html
from .score import Scorer
from .store import SeenStore, merge_items
from .summarize import OpenAICompatClient, Summarizer

_DEFAULT = object()


logger = logging.getLogger("briefing")


def parse_duration(text):
    m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", str(text).lower())
    if not m:
        raise ValueError("duration must look like 90m, 24h, 2d, 1w: %r" % text)
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(**{{"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[unit]: n})


def make_fetcher(cfg):
    f = cfg["fetch"]
    return Fetcher(f["user_agent"], f["delay_seconds"], f["timeout"], resolve(cfg, f["cache_dir"]),
                   f["respect_robots"])


def fetch_source(src, fetcher):
    kind = src.get("type", "rss")
    max_items = int(src.get("max_items", 100))
    data = fetcher.get(src["url"], delay=src.get("delay"), check_robots=(kind == "html"))
    if kind == "rss":
        return parse_feed(data, src["name"], max_items)
    if kind == "html":
        return parse_html(data, src["name"], src["url"], src.get("rules") or {}, max_items)
    raise ValueError("unknown source type %r" % kind)


def collect(cfg, fetcher):
    items, errors = [], []
    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        try:
            got = fetch_source(src, fetcher)
            logger.info("  %-20s %3d items", src["name"], len(got))
            items += got
        except Exception as e:  # one broken source must not stop the briefing
            errors.append("%s: %s" % (src.get("name"), e))
            logger.warning("  %-20s ERROR %s", src.get("name"), e)
    return items, errors


def run(cfg, since=None, dry_run=False, fetcher=None, llm_client=_DEFAULT, now=None):
    now = now or datetime.now(timezone.utc)
    interests, dd = cfg["interests"], cfg["dedupe"]
    since = parse_duration(since or cfg["output"]["since"])

    items, errors = collect(cfg, fetcher or make_fetcher(cfg))
    cutoff = (now - since).isoformat()
    items = [i for i in items if not i.published or i.published >= cutoff]

    scorer = Scorer(interests)
    items = [i for i in items if scorer.score(i)]
    items = merge_items(items, dd["title_similarity"], interests["multi_source_boost"])

    db_path = resolve(cfg, cfg["output"]["db_path"])
    if dry_run and not os.path.exists(db_path):
        db_path = ":memory:"  # dry-run never creates files
    else:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    store = SeenStore(db_path)
    try:
        items = store.filter_new(items, now, dd["title_similarity"], dd["remember_days"])
        items = items[: int(interests["top_n"])]

        if llm_client is _DEFAULT:
            llm_client = OpenAICompatClient(**cfg["llm"]) if cfg["llm"].get("enabled") else None
        summarizer = Summarizer(llm_client)
        for item in items:
            summarizer.summarize_item(item)
        overview = summarizer.overview(items)
        for err in summarizer.errors:
            logger.warning("LLM unavailable, using extractive summaries: %s", err)

        local = now.astimezone()
        date_str = local.strftime("%Y-%m-%d")
        modes = {i.brief_by for i in items} or {"-"}
        meta = {"generated_at": local.strftime("%Y-%m-%d %H:%M"),
                "summary_mode": modes.pop() if len(modes) == 1 else "mixed",
                "count": len(items), "source_errors": errors}
        clusters = render.cluster(items)
        md = render.render_markdown(date_str, overview, clusters, meta)
        data = render.to_json(date_str, overview, clusters, meta)
        result = {"markdown": md, "json": data, "items": items, "errors": errors, "paths": {}}

        if dry_run:
            return result

        out_dir = resolve(cfg, cfg["output"]["dir"])
        os.makedirs(out_dir, exist_ok=True)
        stem, n = date_str, 1
        while os.path.exists(os.path.join(out_dir, stem + ".md")):
            if not items:  # an empty re-run must not overwrite today's briefing
                result["paths"]["markdown"] = os.path.join(out_dir, stem + ".md")
                logger.info("no new items; keeping existing briefing")
                return result
            n += 1
            stem = "%s-%d" % (date_str, n)  # 2nd run of the day -> YYYY-MM-DD-2.md
        md_path, json_path = os.path.join(out_dir, stem + ".md"), os.path.join(out_dir, stem + ".json")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        result["paths"].update(markdown=md_path, json=json_path)
        store.mark(items, now)
    finally:
        store.close()

    _extras(cfg, result, date_str, stem, out_dir, overview)
    return result


def _extras(cfg, result, date_str, stem, out_dir, overview):
    """Optional steps; failures are logged, never fatal."""
    items = result["items"]
    if cfg["timeline"].get("enabled"):
        try:
            result["paths"]["timeline"] = timeline.append(items, resolve(cfg, cfg["timeline"]["dir"]), date_str)
        except Exception as e:
            logger.warning("timeline failed: %s", e)
    if cfg["tts"].get("enabled"):
        try:
            opts = {k: v for k, v in cfg["tts"].items() if k not in ("enabled", "backend")}
            audio = tts.synthesize(tts.build_script(result["json"]), os.path.join(out_dir, stem + ".mp3"),
                                   cfg["tts"].get("backend", "none"), **opts)
            result["paths"]["audio"] = audio
        except Exception as e:
            logger.warning("tts failed: %s", e)
    ntfy = cfg["delivery"]["ntfy"]
    if ntfy.get("enabled") and ntfy.get("topic"):
        try:
            body = "\n".join(l for l in overview if l) + "\n\n" + "\n".join(
                "- [%s](%s)" % (i.title, i.url) for i in items[:5])
            deliver.ntfy_send(ntfy["server"], ntfy["topic"], "커뮤니티 브리핑 " + date_str, body,
                              ntfy.get("token", ""), ntfy.get("click"))
        except Exception as e:
            logger.warning("ntfy failed: %s", e)
