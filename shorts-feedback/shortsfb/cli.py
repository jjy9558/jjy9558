"""명령줄 진입점: python -m shortsfb <명령>."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import TextIO

from . import __version__, autopsy, comments, csvreport, fatigue, linter, report, store, vote
from ._jjkit import llm, notify
from ._jjkit.httpkit import is_loopback_host

FUNNEL_WARNING = ("주의: Tailscale Funnel 등으로 인터넷에 공개하면 링크를 아는 누구나 접근합니다. "
                  "링크 토큰은 비밀번호처럼 다루고, 투표가 끝나면 서버를 끄세요 (README 참고).")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shorts-feedback", description="쇼츠 피드백 도구: 썸네일 투표, 댓글 군집, "
                                "스튜디오 CSV 요약, 실패 편 부검, 제작 피로 규칙")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    p.add_argument("--data", help="데이터 폴더 (기본: $SHORTSFB_HOME 또는 ~/.shorts-feedback)")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("vote", help="썸네일 후보 투표")
    vs = v.add_subparsers(dest="action", required=True)
    c = vs.add_parser("create", help="후보 이미지로 투표 만들기")
    c.add_argument("images", nargs="+")
    c.add_argument("--question", default="")
    c.add_argument("--label", action="append", help="후보 이름(결과 표용). 이미지 수만큼 반복")
    s = vs.add_parser("serve", help="투표 서버 켜기 (기본 127.0.0.1:%d)" % vote.DEFAULT_PORT)
    s.add_argument("--poll", help="투표 ID (기본: 가장 최근)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=vote.DEFAULT_PORT)
    s.add_argument("--public-url", default="", help="공개 주소(예: https://내기기.ts.net). 안내 링크에만 쓴다")
    s.add_argument("--secure-cookie", action="store_true", help="쿠키에 Secure 속성 (https로 공개할 때)")
    s.add_argument("--trust-xff", action="store_true", help="X-Forwarded-For의 마지막 값을 클라이언트 IP로 신뢰")
    s.add_argument("--post-limit", type=int, default=6, help="IP당 분당 투표 요청 수 (기본 6)")
    r = vs.add_parser("result", help="결과 표")
    r.add_argument("--poll")
    r.add_argument("--json", action="store_true")
    vs.add_parser("list", help="만든 투표 목록")

    cm = sub.add_parser("comments", help="댓글 군집")
    cms = cm.add_subparsers(dest="action", required=True)
    cc = cms.add_parser("cluster", help="Takeout/JSON/CSV/붙여넣은 텍스트 -> 주제 군집 -> 다음 사연 주제 TOP5")
    cc.add_argument("file", help="파일 경로 (- 이면 표준입력)")
    cc.add_argument("--k", type=int, help="군집 수 (기본: 자동)")
    cc.add_argument("--seed", type=int, default=7)
    cc.add_argument("--top", type=int, default=5)
    cc.add_argument("--llm", action="store_true", help="로컬 LLM으로 이름 다듬기 (SHORTSFB_LLM_URL 필요)")
    cc.add_argument("--json", dest="json_out", help="요약 JSON 저장 경로(작성자 이름 없음)")

    cr = sub.add_parser("csv-report", help="YouTube Studio 내보내기 CSV를 주제 유형별로 요약")
    cr.add_argument("csv")
    cr.add_argument("--types", help='유형 키워드 JSON {"유형": ["키워드"]}')
    cr.add_argument("--map", action="append", default=[], metavar="필드=열이름", help="열 이름 직접 지정(예: views=조회수)")
    cr.add_argument("--low-ratio", type=float, default=0.5)
    cr.add_argument("--json", dest="json_out")

    a = sub.add_parser("autopsy", help="실패 편 부검 노트")
    asub = a.add_subparsers(dest="action", required=True)
    aa = asub.add_parser("add", help="부검 기록 추가/갱신 (같은 영상은 덮어씀)")
    aa.add_argument("video", help="영상 ID 또는 제목")
    aa.add_argument("--title", default="")
    aa.add_argument("--views", type=int)
    aa.add_argument("--causes", default="", help="쉼표로. 예: hook_weak,too_long (autopsy causes 참고)")
    aa.add_argument("--note", default="")
    aa.add_argument("--date", help="YYYY-MM-DD (기본: 오늘, 갱신이면 기존 날짜 유지)")
    asub.add_parser("list")
    ap = asub.add_parser("patterns", help="상위 원인")
    ap.add_argument("--top", type=int, default=3)
    ac = asub.add_parser("checklist", help="다음 편 기획용 체크리스트")
    ac.add_argument("--top", type=int, default=3)
    ac.add_argument("--out")
    asub.add_parser("causes", help="표준 원인 항목")

    f = sub.add_parser("fatigue", help="제작 피로 규칙")
    fs = f.add_subparsers(dest="action", required=True)
    fa = fs.add_parser("add", help="오늘(또는 --date) 만든 편수 기록")
    fa.add_argument("count", type=int, nargs="?", default=1)
    fa.add_argument("--date")
    fa.add_argument("--set", action="store_true", help="더하지 않고 그날 편수를 이 값으로 정함")
    fc = fs.add_parser("check", help="휴식 권고 한 줄")
    fc.add_argument("--today")
    fc.add_argument("--window", type=int, default=fatigue.DEFAULT_WINDOW)
    fc.add_argument("--limit", type=int, default=fatigue.DEFAULT_LIMIT)
    fc.add_argument("--streak", type=int, default=fatigue.DEFAULT_STREAK)
    fc.add_argument("--notify", action="store_true", help="피곤할 때 ntfy로도 알림 (SHORTSFB_NTFY_TOPIC)")
    fc.add_argument("--strict", action="store_true", help="피곤하면 종료 코드 1")

    rp = sub.add_parser("report", help="종합 리포트(Markdown)")
    rp.add_argument("--poll", nargs="?", const="latest", help="투표 결과 포함 (ID, 생략하면 최근)")
    rp.add_argument("--comments", dest="comments_file")
    rp.add_argument("--csv", dest="csv_file")
    rp.add_argument("--types")
    rp.add_argument("--meta", help="shorts-linter용 meta.json (있으면 titles 결과 첨부)")
    rp.add_argument("--k", type=int)
    rp.add_argument("--llm", action="store_true")
    rp.add_argument("--out")
    return p


def _client(want: bool, out: TextIO):
    if not want:
        return None
    try:
        cl = llm.client_from_env("SHORTSFB")
    except llm.LLMError as e:
        print("LLM 사용 안 함: %s" % e, file=out)
        return None
    if cl is None:
        print("LLM 사용 안 함: SHORTSFB_LLM_URL이 없어 n-gram 이름을 씁니다", file=out)
    return cl


def cmd_vote(args, out: TextIO) -> int:
    data = store.data_dir(args.data)
    if args.action == "create":
        poll = vote.create_poll(data, args.images, args.question, args.label)
        print("투표 %s를 만들었습니다 (후보 %d장)" % (poll["id"], len(poll["candidates"])), file=out)
        print("다음: shorts-feedback vote serve --poll %s" % poll["id"], file=out)
        return 0
    if args.action == "list":
        for p in vote.list_polls(data):
            n = vote.tally(p, vote.read_votes(vote.polls_dir(data) / p["id"] / "votes.jsonl"))["n"]
            print("%s  %s  후보 %d  표 %d  %s" % (p["id"], p["created"], len(p["candidates"]), n, p["question"]), file=out)
        return 0
    poll = vote.load_poll(data, args.poll)
    if args.action == "result":
        res = vote.tally(poll, vote.read_votes(vote.polls_dir(data) / poll["id"] / "votes.jsonl"))
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else vote.format_result(res), file=out)
        return 0
    srv = vote.build_server(data, poll, host=args.host, port=args.port, post_limit=args.post_limit,
                            secure_cookie=args.secure_cookie, trust_xff=args.trust_xff)
    host, port = srv.server_address[:2]
    base = args.public_url.rstrip("/") if args.public_url else "http://%s:%d" % (host, port)
    print("투표 서버: %s/v/%s/" % (base, poll["token"]), file=out)
    if not is_loopback_host(host) or args.public_url:
        print(FUNNEL_WARNING, file=out)
    print("끄기: Ctrl+C. 결과: shorts-feedback vote result --poll %s" % poll["id"], file=out)
    out.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def cmd_comments(args, out: TextIO) -> int:
    if args.file == "-":
        raws = comments.parse_comments(sys.stdin.read())[:comments.MAX_COMMENTS]
    else:
        raws = comments.load_comments(args.file)
    if not raws:
        print("댓글을 읽지 못했습니다: %s" % args.file, file=sys.stderr)
        return 2
    res = comments.cluster_comments(raws, k=args.k, seed=args.seed, top=args.top)
    client = _client(args.llm, out)
    if client is not None:
        comments.apply_names(res, comments.llm_names(client, res["clusters"]))
    print(comments.format_top(res), file=out)
    if args.json_out:
        store.write_json(Path(args.json_out), comments.public_view(res))
        print("저장: %s" % args.json_out, file=out)
    return 0


def cmd_csv_report(args, out: TextIO) -> int:
    over = {}
    for m in args.map:
        field, sep, col = m.partition("=")
        if not sep or field not in csvreport.SYNONYMS:
            print("--map 형식: 필드=열이름 (필드: %s)" % ", ".join(csvreport.SYNONYMS), file=sys.stderr)
            return 2
        over[field] = col
    rep = csvreport.build(args.csv, args.types, over, args.low_ratio)
    print(csvreport.format_text(rep), file=out)
    if args.json_out:
        store.write_json(Path(args.json_out), rep)
    return 0


def cmd_autopsy(args, out: TextIO) -> int:
    data = store.data_dir(args.data)
    if args.action == "add":
        state, e = autopsy.add(data, args.video, title=args.title, views=args.views, causes=args.causes,
                               note=args.note, when=args.date)
        print("%s: %s (원인 %s)" % ("갱신" if state == "updated" else "추가", e["video"], ",".join(e["causes"]) or "-"), file=out)
    elif args.action == "list":
        print(autopsy.format_list(data), file=out)
    elif args.action == "patterns":
        print(autopsy.format_patterns(data, args.top), file=out)
    elif args.action == "checklist":
        text = autopsy.format_checklist(data, args.top)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8", newline="\n")
            print("저장: %s" % args.out, file=out)
        else:
            print(text, file=out)
    else:
        print(autopsy.format_causes(), file=out)
    return 0


def cmd_fatigue(args, out: TextIO) -> int:
    data = store.data_dir(args.data)
    if args.action == "add":
        total = fatigue.record(data, args.date, args.count, args.set)
        print("%s 제작 %d편 기록" % (args.date or date.today().isoformat(), total), file=out)
        return 0
    today = date.fromisoformat(args.today) if args.today else None
    res = fatigue.evaluate(fatigue.load(data), today, args.window, args.limit, args.streak)
    print(res["line"], file=out)
    if res["tired"] and args.notify:
        import os
        topic = os.environ.get("SHORTSFB_NTFY_TOPIC", "")
        cfg = {"console": False}
        if topic:
            cfg["ntfy"] = {"topic": topic, "server": os.environ.get("SHORTSFB_NTFY_SERVER", "https://ntfy.sh")}
        if notify.Notifier(cfg).channels():
            print("알림:", notify.Notifier(cfg).send("제작 피로", res["line"], tags=["zzz"]), file=out)
        else:
            print("알림 채널이 없습니다 (SHORTSFB_NTFY_TOPIC)", file=out)
    return 1 if (res["tired"] and args.strict) else 0


def cmd_report(args, out: TextIO) -> int:
    data = store.data_dir(args.data)
    text = report.build_report(data, poll=args.poll, comments_file=args.comments_file, csv_file=args.csv_file,
                               types_file=args.types, meta_file=args.meta, k=args.k,
                               llm_client=_client(args.llm, out))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print("저장: %s" % args.out, file=out)
    else:
        print(text, file=out, end="")
    return 0


COMMANDS = {"vote": cmd_vote, "comments": cmd_comments, "csv-report": cmd_csv_report, "autopsy": cmd_autopsy,
            "fatigue": cmd_fatigue, "report": cmd_report}


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args, out or sys.stdout)
    except (ValueError, OSError) as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
