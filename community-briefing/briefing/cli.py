"""Command line: python -m briefing run|sources|timeline"""
import argparse
import logging
import os
import sys

from . import config as config_mod
from . import timeline as timeline_mod
from .pipeline import fetch_source, make_fetcher, parse_duration, run


def _load(path):
    if not os.path.exists(path):
        sys.exit("config not found: %s  (copy config.example.json to config.json first)" % path)
    return config_mod.load(path)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows console: print Korean safely
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(prog="briefing", description="나만의 커뮤니티 브리핑 봇")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="수집 -> 필터 -> 요약 -> out/YYYY-MM-DD.md")
    p_run.add_argument("--config", default="config.json")
    p_run.add_argument("--since", help="이 기간 내 글만 (예: 24h, 2d). 기본값은 config output.since")
    p_run.add_argument("--dry-run", action="store_true", help="파일/DB/알림 없이 결과를 화면에 출력")
    p_src = sub.add_parser("sources", help="소스 목록 (--test 로 실제 가져오기 시험)")
    p_src.add_argument("--config", default="config.json")
    p_src.add_argument("--test", action="store_true")
    p_tl = sub.add_parser("timeline", help="이슈 타임라인 보기 + out/timeline.html 다시 만들기")
    p_tl.add_argument("--config", default="config.json")
    p_tl.add_argument("--topic", help="토픽/키워드 이름 (일부만 써도 됨). 없으면 목록 출력")
    p_tl.add_argument("--days", type=int, default=30, help="최근 N일 (기본 30)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = _load(args.config)

    if args.cmd == "sources":
        fetcher = make_fetcher(cfg) if args.test else None
        for src in cfg["sources"]:
            flag = "on " if src.get("enabled", True) else "off"
            line = "[%s] %-20s %-4s %s" % (flag, src["name"], src.get("type", "rss"), src["url"])
            if fetcher and src.get("enabled", True):
                try:
                    items = fetch_source(src, fetcher)
                    line += "\n       OK %d items%s" % (len(items), (" - e.g. " + items[0].title) if items else "")
                except Exception as e:
                    line += "\n       FAIL %s" % e
            print(line)
        return 0

    if args.cmd == "timeline":
        tdir = config_mod.resolve(cfg, cfg["timeline"]["dir"])
        html = config_mod.resolve(cfg, os.path.join(cfg["output"]["dir"], cfg["timeline"].get("html") or "timeline.html"))
        timeline_mod.write_html(tdir, html, args.days)
        tls = timeline_mod.load_all(tdir, args.days)
        if args.topic:
            found = timeline_mod.find(tls, args.topic)
            print(timeline_mod.render_text(found) if found else "해당 토픽/키워드 기록이 없습니다: %s" % args.topic)
        else:
            for tl in tls:
                print("%-8s %-24s %d건" % ("토픽" if tl["kind"] == "topic" else "키워드", tl["name"], len(tl["events"])))
            if not tls:
                print("기록이 없습니다. config의 timeline.enabled 를 true로 하고 run 을 실행하세요.")
        print("HTML:", html)
        return 0

    if args.since:
        parse_duration(args.since)  # validate early
    result = run(cfg, since=args.since, dry_run=args.dry_run)
    if args.dry_run:
        print(result["markdown"])
    else:
        print(result["paths"].get("markdown"))
    return 0
