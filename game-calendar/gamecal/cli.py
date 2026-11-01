import argparse
import sys

from . import classify as C
from .config import add_appid, load_config, load_games
from .models import Entry
from .runner import run
from .state import load_state


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="gamecal.json", help="설정 파일 (기본 gamecal.json)")
    p = argparse.ArgumentParser(prog="gamecal", description="내 게임 캘린더")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", parents=[common], help="뉴스 수집/ics/다이제스트/경보")
    r.add_argument("--dry-run", action="store_true", help="파일과 상태를 저장하지 않음 (LLM 미사용)")
    sub.add_parser("list", parents=[common], help="추적 게임 및 예정된 서비스 종료 표시")
    a = sub.add_parser("add", parents=[common], help="appid 추가")
    a.add_argument("appid", type=int)
    args = p.parse_args(argv)

    if args.cmd == "add":
        ok = add_appid(args.config, args.appid)
        print(("추가됨: %d" if ok else "이미 있음: %d") % args.appid)
        return 0
    cfg = load_config(args.config)
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
