"""명령줄 진입점: python -m mbrief <명령>.

run      오늘 브리핑을 만들어 out/ 에 쓴다 (--speak, --push, --dry-run)
sources  어댑터 상태표
preview  말할 시간 예산(--seconds)에 맞춰 무엇이 읽히는지 미리보기 (파일을 쓰지 않는다)
check    out/ 의 계약 파일 두 개를 검사
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import TextIO

from . import __version__
from . import compose as C
from . import contract, deliver, render, runner
from .config import ConfigError, find_config, load_config
from .model import Context


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="morning-brief",
                                description="아침 방송국: 흩어진 아침 정보를 한 판으로 합쳐 읽어 주는 브리핑")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="설정 파일 (기본: $MBRIEF_CONFIG 또는 ./mbrief.json, 없으면 기본값)")
    common.add_argument("--date", help="이 날짜(YYYY-MM-DD) 아침으로 만들기 (기본: 오늘)")
    common.add_argument("--dry-run", action="store_true",
                        help="네트워크 호출 0, 파일 쓰기 0 (로컬 파일은 읽는다. 자비스 어댑터는 건너뜀)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", parents=[common], help="오늘 브리핑 만들기")
    r.add_argument("--speak", action="store_true", help="s21-sidekick 으로 읽어 주기 (/api/say)")
    r.add_argument("--push", action="store_true", help="ntfy 로 푸시")
    r.add_argument("--json", action="store_true", help="결과 JSON 을 표준출력으로")
    sub.add_parser("sources", parents=[common], help="어댑터 상태표")
    pv = sub.add_parser("preview", parents=[common], help="말할 시간에 맞춘 미리보기")
    pv.add_argument("--seconds", type=float, help="말할 시간(초). 기본은 설정의 speak.seconds. 300자 상한은 적용 안 함")
    ck = sub.add_parser("check", parents=[common], help="out/ 의 계약 파일(brief-latest.json, cards-latest.json) 검사")
    ck.add_argument("--out", help="검사할 폴더 (기본: 설정의 out_dir)")
    return p


def make_context(args, cfg: dict, base: Path, *, opener=None, clock=None, env=None) -> Context:
    tz = dt.timezone(dt.timedelta(minutes=int(cfg["tz_offset_minutes"])))
    if args.date:
        try:
            day = dt.date.fromisoformat(args.date)
        except ValueError:
            raise ConfigError("--date 는 YYYY-MM-DD 형식이어야 해요: %s" % args.date)
        hh, mm = (int(x) for x in str(cfg["default_time"]).split(":"))
        now = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
    else:
        now = (clock() if clock else dt.datetime.now(tz)).astimezone(tz)
    kw = {} if env is None else {"env": env}
    return Context(now=now, base_dir=base, dry_run=bool(args.dry_run), opener=opener, **kw)


def cmd_run(args, cfg, ctx, out: TextIO) -> int:
    result = runner.build(cfg, ctx)
    b = result.brief
    if args.json:
        print(runner.dumps(b), end="", file=out)
    else:
        print(b["speak_text"], file=out)
        print("(%d자 / 예산 %d자, 생략 %d건)" % (len(b["speak_text"]), b["speak_budget_chars"], b["dropped"]), file=out)
        print("", file=out)
        print("\n".join(render.sources_table(b["sources"])), file=out)
    rc = 0
    if args.dry_run:
        print("\n[dry-run] 파일을 쓰지 않고, 전달(--speak/--push)도 하지 않았어요.", file=out)
        return 0
    paths = runner.write_outputs(result, cfg, ctx)
    print("\n쓴 파일: " + ", ".join(p.name for p in paths.values()) + "  (폴더: %s)" % paths["md"].parent, file=out)
    for flag, fn, label in ((args.speak, deliver.speak, "말하기"), (args.push, deliver.push, "푸시")):
        if flag:
            res = fn(b, cfg, ctx)
            print("%s: %s" % (label, res), file=out)
            if res.startswith("오류"):
                rc = 3
    return rc


def cmd_sources(args, cfg, ctx, out: TextIO) -> int:
    items, statuses = runner.gather(cfg, ctx)
    print("\n".join(render.sources_table(statuses)), file=out)
    return 1 if any(s["status"] == "error" for s in statuses.values()) else 0


def cmd_preview(args, cfg, ctx, out: TextIO) -> int:
    result = runner.build(cfg, ctx, seconds=args.seconds, cap=False)
    cps = float(cfg["speak"]["chars_per_sec"])
    spoken_ids = {id(it): line for it, line in result.spoken}
    sec = args.seconds if args.seconds is not None else cfg["speak"]["seconds"]
    print("예산 %g초 = 약 %d자 (초당 %g자)" % (sec, result.budget_chars, cps), file=out)
    print("", file=out)
    rows, used = [], 0.0
    for it, p in result.ranked:
        line = spoken_ids.get(id(it))
        if line:
            used += len(line) / cps
        rows.append([("%g" % p), "낭독" if line else ("화면" if not it.speak else "초과"), it.source,
                     it.title])
    from ._jjkit import textwidth
    print("\n".join(textwidth.table(rows, header=["우선", "구분", "어댑터", "제목"], right_cols=[0],
                                    max_width={3: 60})), file=out)
    text = result.brief["speak_text"]
    print("\n낭독문 (%d자, 약 %.0f초):\n%s" % (len(text), len(text) / cps, text), file=out)
    if len(text) > contract.MAX_SPEAK:
        print("\n주의: 계약 파일의 speak_text 는 %d자까지만 담겨요 (run 은 자동으로 줄여요)." % contract.MAX_SPEAK, file=out)
    return 0


def cmd_check(args, cfg, ctx, out: TextIO) -> int:
    folder = Path(args.out).expanduser() if args.out else ctx.path(cfg["out_dir"])
    bad = 0
    for name, errs in contract.check_files(folder).items():
        if errs:
            bad += 1
            print("%s: 오류 %d건" % (name, len(errs)), file=out)
            for e in errs:
                print("  - " + e, file=out)
        else:
            print("%s: 정상" % name, file=out)
    return 1 if bad else 0


COMMANDS = {"run": cmd_run, "sources": cmd_sources, "preview": cmd_preview, "check": cmd_check}


def main(argv: list[str] | None = None, out: TextIO | None = None, *, opener=None, clock=None, env=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글/이모지로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    try:
        cfg_path = find_config(args.config, env)
        cfg, base = load_config(cfg_path)
        ctx = make_context(args, cfg, base, opener=opener, clock=clock, env=env)
    except ConfigError as e:
        print("설정 오류: %s" % e, file=sys.stderr)
        return 2
    return COMMANDS[args.cmd](args, cfg, ctx, out)
