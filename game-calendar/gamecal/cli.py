import argparse
import datetime as dt
import json
import sys

from . import classify as C
from .alerts import alerts_payload
from .config import add_appid, load_config, load_games
from .dates import KST, UTC
from .models import Entry
from .runner import run
from .state import load_state


def _positive_int(s):
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 합니다")
    return n


def _write_utf8(text, stream):
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        buf.write(text.encode("utf-8"))
        buf.flush()
    else:
        stream.write(text)


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="gamecal.json", help="설정 파일 (기본 gamecal.json)")
    p = argparse.ArgumentParser(prog="gamecal", description="내 게임 캘린더")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", parents=[common], help="뉴스 수집/ics/다이제스트/경보")
    r.add_argument("--dry-run", action="store_true", help="파일과 상태를 저장하지 않음 (LLM 미사용)")
    sub.add_parser("list", parents=[common], help="추적 게임 및 예정된 서비스 종료 표시")
    al = sub.add_parser("alerts", parents=[common],
                        help="예정 일정 JSON 출력 (네트워크 미사용, state.json만 읽음)")
    al.add_argument("--json", action="store_true", help="JSON으로 stdout 출력 (필수)")
    al.add_argument("--days", type=_positive_int, default=14,
                    help="오늘부터 N일 이내 점검/이벤트/패치 (기본 14). 서비스 종료는 60일 고정")
    a = sub.add_parser("add", parents=[common], help="appid 추가")
    a.add_argument("appid", type=int)
    args = p.parse_args(argv)

    if args.cmd == "add":
        ok = add_appid(args.config, args.appid)
        print(("추가됨: %d" if ok else "이미 있음: %d") % args.appid)
        return 0
    cfg = load_config(args.config)
    if args.cmd == "alerts":
        if not args.json:
            p.error("alerts는 현재 --json 옵션과 함께 사용해야 합니다")
        st = load_state(cfg["state_file"])
        entries = [Entry.from_dict(d) for d in st["entries"].values()]
        today = dt.datetime.now(UTC).astimezone(KST).date()
        payload = alerts_payload(entries, today, days=args.days)
        _write_utf8(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", sys.stdout)
        return 0
    if args.cmd == "list":
        for appid, name in sorted(load_games(cfg).items()):
            print("%10d  %s" % (appid, name))
        st = load_state(cfg["state_file"])
        sd = [Entry.from_dict(d) for d in st["entries"].values()]
        sd = [e for e in sd if e.category == C.SHUTDOWN and not e.is_notice]
        for e in sorted(sd, key=lambda x: x.start_date_kst()):
            print("서비스 종료 %s  %s" % (e.start_date_kst(), e.game))
        return 0
    run(cfg, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
