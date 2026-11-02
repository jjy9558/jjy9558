import argparse
import json
import os
import sys
from datetime import datetime

from .config import ConfigError, load_config, state_path
from .counters import Counters, dish_line
from .engine import CANCEL_FLAG, Nagger
from .excuse import generate
from .fakes import SystemClock
from .report import month_data, render_html, render_markdown
from .timeutil import fmt_duration


def _parser():
    p = argparse.ArgumentParser(prog="nagger", description="생활 잔소리 엔진")
    p.add_argument("--config", default=None, help="config.json 경로 (없으면 기본값; ./config.json 이 있으면 자동 사용)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="데몬 실행")
    r.add_argument("--once", action="store_true", help="한 번만 점검하고 종료")
    r.add_argument("--dry-run", action="store_true", help="잠자기를 실제로 하지 않고 메시지만")
    r.add_argument("--poll", type=int, help="폴링 간격(초) 덮어쓰기")
    sub.add_parser("status", help="데몬 상태/오늘 요약")
    rp = sub.add_parser("report", help="리포트")
    g = rp.add_mutually_exclusive_group()
    g.add_argument("--month", nargs="?", const="", default=None, help="YYYY-MM (생략하면 이번 달)")
    g.add_argument("--week", action="store_true", help="주간 카운터 리포트")
    rp.add_argument("--last", action="store_true", help="--week 와 함께: 지난 주")
    rp.add_argument("--html", help="월간 리포트를 이 경로에 HTML로 저장")
    rp.add_argument("--md", help="월간 리포트를 이 경로에 Markdown으로 저장")
    e = sub.add_parser("excuse", help="지각 변명 초안")
    e.add_argument("--late", type=int, required=True, help="늦는 시간(분)")
    e.add_argument("--to", default="", help="받는 사람 (예: 팀장)")
    e.add_argument("--where", choices=["office", "remote"], default="office")
    e.add_argument("--seed", type=int)
    e.add_argument("--llm", action="store_true", help="설정된 로컬 LLM도 시도 (실패하면 템플릿)")
    c = sub.add_parser("count", help="카운터 올리기")
    c.add_argument("kind")
    c.add_argument("-n", type=int, default=1)
    sub.add_parser("cancel-sleep", help="진행 중인 잠자기 카운트다운 취소")
    return p


def _config(args):
    path = args.config
    if path is None and os.path.exists("config.json"):
        path = "config.json"
    return load_config(path)


def _counters(cfg):
    return Counters(state_path(cfg, "counters.jsonl"), cfg["counters"])


def cmd_run(cfg, args, out):
    if args.poll:
        cfg["poll_seconds"] = args.poll
    from .platform import get_platform
    plat = get_platform()
    n = Nagger(cfg, plat, dry_run=args.dry_run, write=out)
    server = None
    if cfg["events"]["enabled"]:
        from .events_server import EventServer
        ev = cfg["events"]
        server = EventServer(ev["host"], ev["port"], ev["token"], cfg["counters"],
                             lambda k, c: n.counters.add(k, n.clock.now(), c)).start()
        out("이벤트 수신기: http://%s:%d/api/event" % (ev["host"], server.port))
    out("life-nagger 시작. 잠자기 %s%s. 종료: Ctrl+C" % (
        "허용" if cfg["mombot"]["allow_sleep"] else "꺼짐(기본)", " [dry-run]" if args.dry_run else ""))
    try:
        n.run(max_ticks=1 if args.once else None)
    except KeyboardInterrupt:
        out("안녕. 잔소리는 계속된다.")
    finally:
        if server:
            server.stop()
    return 0


def cmd_status(cfg, args, out):
    now = SystemClock().now()
    try:
        with open(state_path(cfg, "status.json"), encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        out("상태 파일이 없어요. 먼저 `python -m nagger run` 을 켜 주세요.")
        st = None
    if st:
        age = (now - datetime.fromisoformat(st["updated"])).total_seconds()
        alive = age <= 3 * st["poll_seconds"] + 5
        out("데몬: %s (마지막 점검 %d초 전)" % ("살아있음" if alive else "멈춘 듯?", age))
        out("포그라운드: %s / 입력 없음 %d초" % (st["foreground"] or "-", st["idle_seconds"]))
        mb = st["mombot"]
        out("엄마봇: %s (경고 %d단계, 잠자기 %s)" % (mb["state"], mb["stage"], "허용" if mb["allow_sleep"] else "꺼짐"))
        out("연속 착석: %d분" % st["sitting_minutes"])
        w = st["work_today"]
        out("오늘 작업 %s / 게임 %s" % (fmt_duration(w.get("work", 0)), fmt_duration(w.get("game", 0))))
        out(dish_line(st["dish_days"]))
        for e in st["errors"]:
            out("오류: " + e)
    return 0


def cmd_report(cfg, args, out):
    now = SystemClock().now()
    if args.week:
        out(_counters(cfg).weekly_report(now, -1 if args.last else 0))
        return 0
    month = args.month or now.strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        out("--month는 YYYY-MM 형식이어야 합니다")
        return 2
    try:
        with open(state_path(cfg, "work.json"), encoding="utf-8") as f:
            days = json.load(f).get("days", {})
    except FileNotFoundError:
        days = {}
    data = month_data(days, month, now.date())
    md = render_markdown(data)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(render_html(data))
        out("HTML 저장: " + args.html)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(md)
        out("Markdown 저장: " + args.md)
    if not (args.html or args.md):
        out(md)
    return 0


def cmd_excuse(cfg, args, out):
    drafts, honest, src = generate(args.late, args.to, args.where, args.seed, cfg["llm"], args.llm)
    out("지각 변명 초안 (%s)" % ("로컬 LLM" if src == "llm" else "템플릿"))
    for i, d in enumerate(drafts, 1):
        out("\n[%d] %s" % (i, d))
    out("\n[정직] %s" % honest)
    return 0


def cmd_count(cfg, args, out):
    c = _counters(cfg)
    now = SystemClock().now()
    try:
        c.add(args.kind, now, args.n)
    except ValueError as e:
        out(str(e))
        return 2
    meta = cfg["counters"][args.kind]
    if args.kind == "dishes":
        out("설거지 완료 도장 쾅. 방치 카운터 리셋. (오늘 %d%s)" % (c.today_total("dishes", now), meta["unit"]))
    else:
        out("%s +%d (오늘 %d%s)" % (meta["label"], args.n, c.today_total(args.kind, now), meta["unit"]))
    return 0


def cmd_cancel(cfg, args, out):
    with open(state_path(cfg, CANCEL_FLAG), "w") as f:
        f.write("cancel\n")
    out("취소 요청을 남겼어요. 카운트다운 중이면 다음 점검(최대 5초)에 멈춥니다.")
    return 0


def main(argv=None, out=print):
    args = _parser().parse_args(argv)
    try:
        cfg = _config(args)
    except ConfigError as e:
        out("설정 오류: %s" % e)
        return 2
    fn = {"run": cmd_run, "status": cmd_status, "report": cmd_report, "excuse": cmd_excuse,
          "count": cmd_count, "cancel-sleep": cmd_cancel}[args.cmd]
    try:
        return fn(cfg, args, out)
    except Exception as e:
        if args.cmd == "run":
            out("실행 실패: %s" % e)
            return 1
        raise
