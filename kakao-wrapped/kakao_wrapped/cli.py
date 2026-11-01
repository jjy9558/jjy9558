"""Command line interface: python -m kakao_wrapped <export> [options]."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .parser import FormatError, parse_file
from .render import render_html
from .stats import available_years, compute_stats

CONSENT_REMINDER = (
    "※ 대화 내보내기 파일에는 개인적인 메시지가 들어 있습니다. "
    "결과물을 공유하기 전에 단톡방 친구들의 동의를 꼭 받으세요. "
    "(이름·문장을 숨기려면 --anonymize)"
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m kakao_wrapped",
        description="카카오톡 단톡방 대화 내보내기 파일로 연말결산(Wrapped) HTML을 만듭니다. 모든 처리는 내 컴퓨터에서만 이뤄집니다.",
    )
    p.add_argument("export", help="카카오톡 대화 내보내기 파일 (.txt / .csv)")
    p.add_argument("--year", type=int, help="결산할 연도 (기본: 대화에서 가장 최근 연도)")
    p.add_argument("--out", default="wrapped.html", help="출력 HTML 경로 (기본: wrapped.html)")
    p.add_argument("--anonymize", action="store_true", help="이름을 멤버1..N으로 바꾸고 메시지 원문 인용을 모두 뺍니다")
    p.add_argument("--top", type=int, default=5, help="순위/말버릇 목록에 보여줄 인원 수 (기본: 5)")
    p.add_argument("--json", action="store_true", help="통계를 JSON으로도 저장 (출력 HTML과 같은 이름의 .json)")
    p.add_argument("--json-out", metavar="PATH", help="JSON 저장 경로를 직접 지정 (--json 포함)")
    p.add_argument("--version", action="version", version="kakao-wrapped " + __version__)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.top < 1:
        print("오류: --top 은 1 이상이어야 합니다.", file=sys.stderr)
        return 2
    if not os.path.isfile(args.export):
        print("오류: 파일을 찾을 수 없습니다: %s" % args.export, file=sys.stderr)
        return 2
    try:
        parsed = parse_file(args.export)
    except FormatError as exc:
        print("오류: %s" % exc, file=sys.stderr)
        return 2
    years = available_years(parsed.messages)
    if not years:
        print("오류: 메시지를 하나도 읽지 못했습니다.", file=sys.stderr)
        return 2
    year = args.year if args.year is not None else years[-1]
    if year not in years:
        print("오류: %d년 메시지가 없습니다. 파일에 있는 연도: %s" % (year, ", ".join(map(str, years))), file=sys.stderr)
        return 2

    stats = compute_stats(
        parsed.messages, year, top=args.top, anonymize=args.anonymize,
        title=parsed.title, fmt=parsed.fmt,
    )
    html = render_html(stats)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("형식: %s · %d년 메시지 %d개 · 멤버 %d명" % (
        parsed.fmt, year, stats["totals"]["messages"], stats["totals"]["members"]))
    print("저장했어요: %s" % args.out)

    json_path = args.json_out or (os.path.splitext(args.out)[0] + ".json" if args.json else None)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(stats, fh, ensure_ascii=False, indent=2)
        print("통계 JSON: %s" % json_path)
    if not args.anonymize:
        print(CONSENT_REMINDER, file=sys.stderr)
    return 0
