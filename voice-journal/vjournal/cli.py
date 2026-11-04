# -*- coding: utf-8 -*-
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

from . import checkout as C
from . import claude_ingest as CI
from . import llm as LLM
from .redact import Redactor
from .report import build_scorecard, export_month, render_scorecard_html, render_scorecard_md
from .search import search_entries, why
from .store import TS_FMT, Journal, default_db_path


def _parse_day(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_local(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).astimezone()
        except ValueError:
            continue
    raise ValueError("시각 형식: 'YYYY-MM-DD HH:MM' (%r)" % s)


def build_parser():
    p = argparse.ArgumentParser(prog="vjournal", description="음성 일기 -> 개인 지식베이스")
    p.add_argument("--db", default=None, help="sqlite 경로 (기본 ~/.vjournal/journal.db, env VJOURNAL_DB)")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="일기 한 건 추가 ('-' 이면 stdin)")
    a.add_argument("text", nargs="+")
    a.add_argument("--mood", type=int, help="기분 1~5")
    a.add_argument("--flag", action="store_true", help="아침 브리핑에 표시")
    a.add_argument("--at", help="'YYYY-MM-DD HH:MM' 으로 소급 기록")

    c = sub.add_parser("checkout", help="퇴근 전 3문답")
    c.add_argument("--win")
    c.add_argument("--blocked")
    c.add_argument("--next", dest="next_")

    m = sub.add_parser("morning", help="아침 브리핑")
    m.add_argument("--json", action="store_true", help="JSON 출력 (다른 도구 연동용, schema v1)")
    m.add_argument("--date", help="기준일 YYYY-MM-DD (기본: 오늘)")

    g = sub.add_parser("goals", help="선언 목록 / 수동 갱신")
    g.add_argument("action", nargs="?", choices=["list", "done", "progress", "drop"], default="list")
    g.add_argument("id", nargs="?", type=int)
    g.add_argument("amount", nargs="?", type=float)

    r = sub.add_parser("report", help="주간 성적표")
    r.add_argument("--week-of", help="이 날짜가 속한 주 (기본: 오늘)")
    r.add_argument("--html", help="HTML 저장 경로")
    r.add_argument("--md", help="Markdown 저장 경로 (없으면 stdout)")

    cl = sub.add_parser("claude", help="야간 작업 아침 3줄 보고 (Claude Code 트랜스크립트)")
    cl.add_argument("--projects-dir", default=None)
    cl.add_argument("--since", help="'YYYY-MM-DD HH:MM' (로컬)")
    cl.add_argument("--until", help="'YYYY-MM-DD HH:MM' (로컬, 기본 지금)")
    cl.add_argument("--hours", type=float, default=14, help="--since 없을 때 지금부터 거꾸로 (기본 14)")
    cl.add_argument("--save", action="store_true", help="보고서를 일기(kind=claude)로 저장")
    cl.add_argument("--redact-word", action="append", default=[])

    w = sub.add_parser("why", help="'왜 접었지?' 이유 문장 찾기")
    w.add_argument("topic")

    s = sub.add_parser("search", help="일기 검색 (BM25/bigram)")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)

    e = sub.add_parser("export", help="월간 Markdown")
    e.add_argument("month", help="YYYY-MM")
    e.add_argument("--out")

    sv = sub.add_parser("serve", help="HTTP 서버")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)
    sv.add_argument("--token", default=None, help="기본: env VJ_TOKEN 또는 ~/.vjournal/token")
    sv.add_argument("--cors-origin", default=None, help="S21 페이지 origin (예: https://pc.tailnet.ts.net)")

    ls = sub.add_parser("list", help="최근 기록")
    ls.add_argument("--limit", type=int, default=10)
    ls.add_argument("--tag")

    uf = sub.add_parser("unflag", help="표시 해제")
    uf.add_argument("id", type=int)
    return p


def _out(s=""):
    sys.stdout.write(s + "\n")


def main(argv=None, now=None, stdin=None, ask=None):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 2
    stdin = stdin or sys.stdin
    try:
        llm = LLM.from_env()
    except LLM.LLMError as e:
        sys.stderr.write("LLM 설정 무시: %s\n" % e)
        llm = None
    db = args.db or default_db_path()

    if args.cmd == "serve":
        from .server import load_or_create_token, make_server
        token = args.token or os.environ.get("VJ_TOKEN")
        if not token:
            token, created = load_or_create_token(os.path.join(os.path.dirname(os.path.abspath(db)), "token"))
            sys.stderr.write("토큰 파일: %s\n" % os.path.join(os.path.dirname(os.path.abspath(db)), "token"))
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            sys.stderr.write("경고: 루프백이 아닌 주소(%s)에 바인드합니다. 토큰을 꼭 지키세요.\n" % args.host)
        srv = make_server(db, token, args.host, args.port, args.cors_origin, llm=llm)
        _out("listening on http://%s:%d (Ctrl+C 로 종료)" % srv.server_address[:2])
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    j = Journal(db, now=now, llm=llm)
    try:
        return _dispatch(args, j, stdin, ask, llm)
    except ValueError as e:
        sys.stderr.write("오류: %s\n" % e)
        return 1
    finally:
        j.close()


def _dispatch(args, j, stdin, ask, llm):
    cmd = args.cmd
    if cmd == "add":
        text = stdin.read() if args.text == ["-"] else " ".join(args.text)
        ts = _parse_local(args.at).replace(tzinfo=None) if args.at else None
        res = j.add_entry(text, mood=args.mood, flag=args.flag, ts=ts)
        _out("저장 #%d %s%s" % (res["id"], res["ts"], " " + " ".join("#" + t for t in res["tags"]) if res["tags"] else ""))
        for g in res["goals_created"]:
            _out("  선언 감지 -> 목표 #%d: %s%s" % (g["id"], g["title"], " (마감 %s)" % g["deadline"] if g["deadline"] else ""))
        for p in res["progress"]:
            _out("  진행 감지 -> 목표 #%d %s" % (p["goal_id"], "접음" if p["kind"] == "drop" else "+%s" % p.get("amount")))
        return 0
    if cmd == "checkout":
        given = {"win": args.win, "blocked": args.blocked, "next": args.next_}
        if any(given.values()):
            saved = C.save_answers(j, {k: v for k, v in given.items() if v})
            _out("저장: %d개 답변" % len(saved))
            return 0
        askf = ask or (lambda q: input(q))
        C.run_checkout(j, askf, _out)
        return 0
    if cmd == "morning":
        day = _parse_day(args.date) if args.date else None
        if args.json:
            payload = C.morning_payload(j, day or j.today(), Redactor())
            _out(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _out(C.render_morning(C.morning(j, today=day)))
        return 0
    if cmd == "goals":
        if args.action == "list":
            for g in j.goals():
                tgt = " %s%s" % (g["target"], g["unit"] or "") if g["target"] else ""
                _out("#%d [%s/%s]%s %s%s" % (g["id"], g["status"], g["period"], tgt, g["title"],
                                             " ~%s" % g["deadline"] if g["deadline"] else ""))
            return 0
        if args.id is None:
            raise ValueError("목표 id 가 필요합니다")
        if j.get_goal(args.id) is None:
            raise ValueError("목표 #%d 없음" % args.id)
        if args.action == "done":
            j.complete_goal(args.id)
        elif args.action == "progress":
            j.add_progress(args.id, args.amount if args.amount is not None else 1)
        else:
            j.drop_goal(args.id)
        _out("갱신: 목표 #%d" % args.id)
        return 0
    if cmd == "report":
        on = _parse_day(args.week_of) if args.week_of else None
        sc = build_scorecard(j, on)
        md = render_scorecard_md(sc)
        if args.html:
            with open(args.html, "w", encoding="utf-8") as fh:
                fh.write(render_scorecard_html(sc))
            _out("HTML 저장: %s" % args.html)
        if args.md:
            with open(args.md, "w", encoding="utf-8") as fh:
                fh.write(md)
            _out("Markdown 저장: %s" % args.md)
        if not args.md and not args.html:
            sys.stdout.write(md)
        return 0
    if cmd == "claude":
        end = _parse_local(args.until) if args.until else datetime.now().astimezone()
        start = _parse_local(args.since) if args.since else end - timedelta(hours=args.hours)
        stats = {}
        red = Redactor(args.redact_word)
        rep = CI.build_report(args.projects_dir or CI.default_projects_dir(), start, end, llm, red, stats)
        text = CI.render_report(rep, start, end)
        _out(text)
        if stats.get("malformed"):
            _out("(깨진 줄 %d개는 건너뜀)" % stats["malformed"])
        if args.save and rep:
            j.add_entry(text, kind="claude", auto_goals=False)
            _out("저장했습니다 (kind=claude).")
        return 0
    if cmd == "why":
        items = why(j.entries(), args.topic, j.goals())
        if not items:
            _out("'%s' 에 대한 이유 문장을 못 찾았어요." % args.topic)
            return 0
        for it in items:
            _out("%s  [%s]  %s" % (it["ts"][:16], "/".join(it["markers"]), it["sentence"]))
        return 0
    if cmd == "search":
        hits = search_entries(j.entries(), args.query, args.limit)
        if not hits:
            _out("결과 없음")
        for s, e in hits:
            _out("%.2f  #%d %s  %s" % (s, e["id"], e["ts"][:16], e["text"].replace("\n", " ")[:100]))
        return 0
    if cmd == "export":
        md = export_month(j, args.month)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(md)
            _out("저장: %s" % args.out)
        else:
            sys.stdout.write(md)
        return 0
    if cmd == "list":
        for e in j.entries(limit=args.limit, tag=args.tag, newest_first=True):
            _out("#%d %s [%s] %s" % (e["id"], e["ts"][:16], e["kind"], e["text"].replace("\n", " ")[:100]))
        return 0
    if cmd == "unflag":
        _out("해제" if j.set_flag(args.id, False) else "해당 id 없음")
        return 0
    return 2
