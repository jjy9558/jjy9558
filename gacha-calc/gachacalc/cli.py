"""명령행 진입점.

사용법:
    python3 -m gachacalc pity --config 배너.json --currency 50000 --package "6480원에 300 젬"
    python3 -m gachacalc rate 플레이시간.csv
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .banner import Banner
from .currency import parse_package
from .pity import build_pity_report
from .rate import build_payslip, load_playtime_csv
from .render import render_payslip, render_pity_report
from .validation import ValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gachacalc",
        description="가챠 천장 환율 판정기 & 게임 시간 시급 명세서",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pity = sub.add_parser("pity", help="천장/확률/환율 판정")
    pity.add_argument("--config", required=True, help="배너 설정 JSON 경로")
    pity.add_argument("--pity-count", default="0",
                      help="현재 천장 카운트 (마지막 5성 이후 뽑은 횟수, 기본 0)")
    pity.add_argument("--guaranteed", action="store_true",
                      help="확정 상태 (다음 5성이 픽업 확정)")
    pity.add_argument("--currency", required=True, help="지금 보유한 재화 수")
    pity.add_argument("--package", required=True, help='충전 패키지, 예: "6480원에 300 젬"')

    rate = sub.add_parser("rate", help="플레이 시간 CSV로 시급 명세서 출력")
    rate.add_argument("csv_path", help="게임,플레이 시간,지출 형식의 CSV 경로")
    return parser


def _run_pity(args: argparse.Namespace) -> str:
    banner = Banner.load(args.config)
    package = parse_package(args.package)
    report = build_pity_report(
        banner,
        package=package,
        currency_units=args.currency,
        pity_count=args.pity_count,
        guaranteed=args.guaranteed,
    )
    return render_pity_report(report)


def _run_rate(args: argparse.Namespace) -> str:
    records = load_playtime_csv(args.csv_path)
    return render_payslip(build_payslip(records))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = _run_pity(args) if args.command == "pity" else _run_rate(args)
    except ValidationError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0
