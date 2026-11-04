"""명령줄 진입점: python -m lifecourt <명령>."""
import argparse
import os
import sys
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import TextIO

from . import __version__
from . import courts, engine, llmphrase, store, wakeup
from ._jjkit import llm, notify

EPILOG = """예:
  life-court night-snack --food 치킨 --time 23:40 --kcal 1200
  life-court late --consent --minutes 25 --meeting "금요 저녁" --excuse "버스가 안 와서"
  life-court bet --a 민수 --b 지연 --condition "축구 승부 예측" --winner 민수 --stake 커피
  life-court toast 민수:개발자 지연 --occasion 송년회
  life-court wakeup --db soundwatch.db --woke 03:40     # 누가 날 깨웠나 재판
"""


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--seed", help="같은 시드면 같은 판결(기본: 입력 내용에서 자동)")
    p.add_argument("--date", help="재판일 YYYY-MM-DD (기본: 오늘)")
    p.add_argument("--md", metavar="파일", help="판결문을 마크다운 파일로 저장")
    p.add_argument("--html", metavar="파일", help="인쇄용 단일 HTML 파일로 저장")
    p.add_argument("--speak", "--say", dest="speak", action="store_true",
                   help="주문을 s21-sidekick으로 낭독(LIFECOURT_SIDEKICK_TOKEN 필요)")
    p.add_argument("--llm", action="store_true", help="로컬 LLM으로 문장 다듬기(LIFECOURT_LLM_URL 필요, 실패하면 원문)")
    p.add_argument("--data-dir", help="기록 폴더(홈 폴더 안만 허용, 기본 ~/.life-court)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="life-court", description="야식·지각·내기를 판결문으로 만드는 웃긴 재판소",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("night-snack", help="야식 재판소")
    _common(n)
    n.add_argument("--food", default="", help="먹은 것")
    n.add_argument("--time", help="먹은 시각 HH:MM (기본: 지금)")
    n.add_argument("--kcal", type=int, default=0, help="대략 칼로리")
    n.add_argument("--count", type=int, default=1, help="오늘 야식/배달 횟수(이번 것 포함)")
    n.add_argument("--me", default=os.environ.get("LIFECOURT_ME", "나"), help="피고인 이름(본인)")
    n.add_argument("--no-save", action="store_true", help="기록을 저장하지 않음(재범 집계는 읽기만)")

    l = sub.add_parser("late", help="지각 재판소(본인이 자진 피고)")
    _common(l)
    l.add_argument("--consent", action="store_true", help="피고인이 동의했음(없으면 거부)")
    l.add_argument("--me", default=os.environ.get("LIFECOURT_ME", "나"), help="본인 이름")
    l.add_argument("--defendant", help="피고인(기본: 본인). 다른 사람이면 --other-agreed 필요")
    l.add_argument("--other-agreed", action="store_true", help="본인 아닌 피고인이 직접 동의했음")
    l.add_argument("--minutes", type=int, default=0)
    l.add_argument("--meeting", default="")
    l.add_argument("--excuse", default="")
    l.add_argument("--no-save", action="store_true")

    b = sub.add_parser("bet", help="내기 판결문(저장 안 함)")
    _common(b)
    b.add_argument("--a", default="", help="첫 번째 이름")
    b.add_argument("--b", default="", help="두 번째 이름")
    b.add_argument("--condition", default="", help="내기 조건")
    b.add_argument("--winner", default="", help="이긴 사람 이름 또는 무승부")
    b.add_argument("--stake", default="치킨 한 마리")
    b.add_argument("--complaint", default="", help="패자의 항소 이유(선택)")

    t = sub.add_parser("toast", help="건배사 생성기(저장 안 함)")
    _common(t)
    t.add_argument("names", nargs="*", help="이름 또는 이름:직업")
    t.add_argument("--occasion", default="")
    t.add_argument("--count", type=int, default=5)

    w = sub.add_parser("wakeup", help="누가 날 깨웠나 재판(sound-watch 기록을 읽기 전용으로 사용)")
    _common(w)
    w.add_argument("--db", help="sound-watch DB 경로(기본: $SOUNDWATCH_HOME/soundwatch.db 또는 ./data/soundwatch.db)")
    w.add_argument("--from", dest="t_from", default="00:00", help="밤 시작 HH:MM (기본 00:00)")
    w.add_argument("--to", dest="t_to", default="07:00", help="밤 끝 HH:MM (기본 07:00, from보다 이르면 자정을 넘김)")
    w.add_argument("--woke", help="깬 시각 HH:MM(주범을 이 시각에 가장 가까운 소리로)")

    f = sub.add_parser("forget", help="저장된 기록 지우기")
    f.add_argument("--data-dir")
    f.add_argument("--night", action="store_true", help="야식 기록 전부")
    f.add_argument("--late", action="store_true", help="지각 기록 전부")
    f.add_argument("--who", help="이 이름의 지각 기록만")
    return p


class Ctx:
    def __init__(self, out: TextIO, err: TextIO, now: datetime, opener=None, llm_client=None, env=None):
        self.out, self.err, self.now, self.opener, self.llm_client = out, err, now, opener, llm_client
        self.env = os.environ if env is None else env


def _write_file(path: str, text: str) -> None:
    pth = Path(path).expanduser()
    pth.parent.mkdir(parents=True, exist_ok=True)
    pth.write_text(text, encoding="utf-8")


def _llm_client(ctx: Ctx):
    if ctx.llm_client is not None:
        return ctx.llm_client
    try:
        client = llm.client_from_env("LIFECOURT", env=ctx.env, timeout=30.0)
    except llm.LLMError as e:
        print("경고: %s (원문으로 진행)" % e, file=ctx.err)
        return None
    if client is None:
        print("경고: LIFECOURT_LLM_URL이 없어 LLM 없이 진행합니다.", file=ctx.err)
    return client


def _emit(j: engine.Judgment, args, ctx: Ctx, names: list[str]) -> int:
    if args.llm:
        client = _llm_client(ctx)
        if client is not None:
            j2 = llmphrase.polish(j, client, names)
            if not j2.llm_used:
                print("경고: LLM 문장을 쓸 수 없어 원문을 사용합니다.", file=ctx.err)
            j = j2
    ctx.out.write(engine.render_text(j))
    if args.md:
        _write_file(args.md, engine.render_md(j))
        print("마크다운 저장: %s" % args.md, file=ctx.err)
    if args.html:
        _write_file(args.html, engine.render_html(j))
        print("HTML 저장: %s" % args.html, file=ctx.err)
    if args.speak:
        token = ctx.env.get("LIFECOURT_SIDEKICK_TOKEN", "")
        if not token:
            print("경고: LIFECOURT_SIDEKICK_TOKEN이 없어 낭독을 건너뜁니다.", file=ctx.err)
        else:
            try:
                notify.sidekick_say(j.ruling, token=token, module="life-court", opener=ctx.opener,
                                    url=ctx.env.get("LIFECOURT_SIDEKICK_URL", "http://127.0.0.1:8765"))
            except notify.NotifyError as e:
                print("경고: 낭독 실패 (%s)" % e, file=ctx.err)
    return 0


def _date(args, ctx: Ctx) -> Date:
    return courts.parse_date(args.date, ctx.now.date())


def cmd_night(args, ctx: Ctx) -> int:
    d = _date(args, ctx)
    time = args.time or ctx.now.strftime("%H:%M")
    food = courts._need(args.food, "음식 이름")
    log = store.Log(store.data_dir(args.data_dir, ctx.env), "night")
    priors = courts.night_priors(log.load(), d, (d.isoformat(), time, food))
    j = courts.judge_night(food, time, d, kcal=args.kcal, today_count=args.count, priors=priors,
                           defendant=args.me, seed=args.seed)
    if not args.no_save:
        log.add({"date": d.isoformat(), "time": time, "food": food, "kcal": args.kcal})
    return _emit(j, args, ctx, [food])


def cmd_late(args, ctx: Ctx) -> int:
    if not args.consent:
        print("거부: 지각 재판은 피고인의 동의가 있어야 열립니다. 본인이 자진 신청한다면 --consent를 붙이세요.",
              file=ctx.err)
        return 2
    me = args.me.strip()
    defendant = (args.defendant or me).strip()
    is_self = defendant == me
    if not is_self and not args.other_agreed:
        print("거부: 피고인은 기본적으로 본인(%s)만 가능합니다. 다른 사람은 그 사람이 직접 동의한 경우에만 "
              "--other-agreed와 함께 신청하세요." % me, file=ctx.err)
        return 2
    d = _date(args, ctx)
    courts._need(defendant, "피고인 이름")
    log = store.Log(store.data_dir(args.data_dir, ctx.env), "late")
    priors = courts.late_priors(log.load(), defendant, d)
    j = courts.judge_late(defendant, args.minutes, args.meeting, d, excuse=args.excuse, priors=priors,
                          is_self=is_self, seed=args.seed)
    if not args.no_save:
        log.add({"date": d.isoformat(), "defendant": defendant, "self": is_self,
                 "minutes": args.minutes, "meeting": args.meeting.strip()})
    return _emit(j, args, ctx, [defendant])


def cmd_bet(args, ctx: Ctx) -> int:
    j = courts.judge_bet(args.a, args.b, args.condition, args.winner, _date(args, ctx), stake=args.stake,
                         complaint=args.complaint, seed=args.seed)
    return _emit(j, args, ctx, [args.a.strip(), args.b.strip()])


def cmd_toast(args, ctx: Ctx) -> int:
    j = courts.make_toasts(args.names, _date(args, ctx), occasion=args.occasion, count=max(1, args.count),
                           seed=args.seed)
    return _emit(j, args, ctx, [courts.parse_guest(g)[0] for g in args.names])


def cmd_wakeup(args, ctx: Ctx) -> int:
    if args.llm:  # 사람 이름이 끼어들 틈을 없애려고 wakeup은 템플릿 문장만 쓴다
        print("경고: wakeup은 --llm을 쓰지 않고 템플릿 원문으로 출력합니다.", file=ctx.err)
        args.llm = False
    d = _date(args, ctx)
    start, end = wakeup.night_window(d, args.t_from, args.t_to)
    woke = wakeup.resolve_woke(args.woke, start, end) if args.woke else None
    db = Path(args.db) if args.db else wakeup.default_db(ctx.env)
    events, dets = wakeup.read_night(db, start.timestamp(), end.timestamp())
    j = wakeup.judge_wakeup(events, dets, d, start, end, woke=woke, seed=args.seed)
    return _emit(j, args, ctx, [])


def cmd_forget(args, ctx: Ctx) -> int:
    if not (args.night or args.late or args.who):
        print("무엇을 지울지 --night, --late, --who 이름 중 하나를 고르세요.", file=ctx.err)
        return 2
    sd = store.data_dir(args.data_dir, ctx.env)
    if args.night:
        store.Log(sd, "night").save([])
    if args.late:
        store.Log(sd, "late").save([])
    if args.who:
        lg = store.Log(sd, "late")
        lg.save([e for e in lg.load() if e.get("defendant") != args.who])
    print("지웠습니다.", file=ctx.out)
    return 0


COMMANDS = {"night-snack": cmd_night, "late": cmd_late, "bet": cmd_bet, "toast": cmd_toast,
            "wakeup": cmd_wakeup, "forget": cmd_forget}


def main(argv: list[str] | None = None, out: TextIO | None = None, *, err: TextIO | None = None,
         now: datetime | None = None, opener=None, llm_client=None, env: dict | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    ctx = Ctx(out or sys.stdout, err or sys.stderr, now or datetime.now(), opener, llm_client, env)
    try:
        return COMMANDS[args.cmd](args, ctx)
    except (courts.CourtError, store.StoreError) as e:
        print("오류: %s" % e, file=ctx.err)
        return 2
