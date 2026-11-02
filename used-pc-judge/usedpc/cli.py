"""명령줄 인터페이스.

  python -m usedpc judge listing.txt [--use "로컬 LLM 30B" --budget 150] [--md out.md]
  python -m usedpc compare listings.txt [--md out.md]
  python -m usedpc prices show [섹션]
  python -m usedpc prices set "RTX 4070 Super" 68만
  python -m usedpc prices stamp
"""

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

from . import __version__
from .compare import compare_texts
from .judge import judge
from .listing_info import parse_price_text
from .parser import parse_listing, split_listings
from .perf import parse_use
from .prices import PriceError, PriceTable
from .questions import make_questions
from .report import (render_compare, render_compare_md, render_judgment, render_judgment_md,
                     render_prices)
from .upgrade import advise


def _read_text(path: str) -> str:
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _make_llm(args):
    if not getattr(args, "llm", False):
        return None
    from .llm import LLMError, OpenAICompatClient
    try:
        return OpenAICompatClient(args.llm_url, args.llm_model, allow_remote=args.allow_remote)
    except LLMError as e:
        print("오류:", e, file=sys.stderr)
        raise SystemExit(2)


def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--prices", help="가격표 JSON 경로 (기본: prices/parts_used_kr.json 또는 $USEDPC_PRICES)")
    sp.add_argument("--today", help="기준 날짜 YYYY-MM-DD (기본: 오늘; 테스트용)")
    sp.add_argument("--md", metavar="OUT.md", help="Markdown 리포트를 이 파일로 저장")
    sp.add_argument("--llm", action="store_true", help="로컬 LLM(OpenAI 호환)으로 빈칸 보충 (선택)")
    sp.add_argument("--llm-url", help="예: http://127.0.0.1:1234/v1 (기본 $USEDPC_LLM_URL)")
    sp.add_argument("--llm-model", help="모델 이름 (기본 $USEDPC_LLM_MODEL)")
    sp.add_argument("--allow-remote", action="store_true", help="로컬/사설 주소가 아닌 LLM 서버도 허용 (글이 외부로 전송됨)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="usedpc", description="C6 중고 PC 사냥꾼: 적정가 판정 + 업그레이드 추천")
    ap.add_argument("--version", action="version", version="usedpc " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    j = sub.add_parser("judge", help="매물 글 하나를 판정 (파일 경로 또는 - 로 표준입력)")
    j.add_argument("file")
    j.add_argument("--use", help='용도: "게임 1440p", "로컬 LLM 30B", "영상편집"')
    j.add_argument("--budget", help="총 예산(만원). 매물 가격 + 교체 비용 상한. 예: 150")
    j.add_argument("--no-questions", action="store_true", help="판매자 질문 생략")
    _add_common(j)

    c = sub.add_parser("compare", help="'---' 로 구분된 여러 매물을 가성비 순으로 비교")
    c.add_argument("file")
    _add_common(c)

    pr = sub.add_parser("prices", help="가격표 보기/수정")
    ps = pr.add_subparsers(dest="pcmd")
    sh = ps.add_parser("show", help="가격표 출력")
    sh.add_argument("section", nargs="?", help="gpu, cpu, board, ram_per_gb, adjust ...")
    sh.add_argument("--prices")
    st = ps.add_parser("set", help='가격 수정: prices set "RTX 4070 Super" 68만')
    st.add_argument("part")
    st.add_argument("price", help="85만 / 850000 / 85(=85만원) / 850k. adjust.* 항목은 숫자 그대로")
    st.add_argument("--new", action="store_true", help="중고가가 아니라 신품 기준가(new 섹션)를 수정")
    st.add_argument("--prices")
    sp = ps.add_parser("stamp", help="기준일(as_of)을 오늘로 갱신하고 placeholder 표시를 끔")
    sp.add_argument("--keep-placeholder", action="store_true", help="placeholder 표시는 유지")
    sp.add_argument("--prices")
    return ap


def _load_table(path: Optional[str]) -> PriceTable:
    try:
        return PriceTable.load(path)
    except PriceError as e:
        print("오류:", e, file=sys.stderr)
        raise SystemExit(2)


def cmd_judge(args) -> int:
    table = _load_table(args.prices)
    text = _read_text(args.file)
    listings = split_listings(text)
    if not listings:
        print("오류: 매물 텍스트가 비어 있습니다.", file=sys.stderr)
        return 2
    if len(listings) > 1:
        print("[안내] '---' 로 구분된 매물이 {}개입니다. 첫 번째만 판정합니다 (여러 개 비교는 compare 사용).".format(len(listings)))
    llm = _make_llm(args)
    p = parse_listing(listings[0], today=args.today, llm=llm)
    j = judge(p, table, args.today)
    qs = [] if args.no_questions else make_questions(p, j)
    plan = None
    if args.use:
        try:
            use = parse_use(args.use)
        except ValueError as e:
            print("오류:", e, file=sys.stderr)
            return 2
        budget = None
        if args.budget:
            v = parse_price_text(args.budget)
            if v is None:
                print("오류: 예산을 이해할 수 없습니다: {}".format(args.budget), file=sys.stderr)
                return 2
            budget = v / 10000.0
        plan = advise(p, table, use, budget, args.today, judgment=j)
    elif args.budget:
        print("[안내] --budget 은 --use 와 함께 써야 업그레이드 추천에 반영됩니다.")
    print(render_judgment(j, qs, plan))
    if args.md:
        Path(args.md).write_text(render_judgment_md(j, qs, plan), encoding="utf-8")
        print("\nMarkdown 리포트 저장: " + args.md)
    return 0


def cmd_compare(args) -> int:
    table = _load_table(args.prices)
    text = _read_text(args.file)
    llm = _make_llm(args)
    rows = compare_texts(text, table, args.today, llm=llm)
    if not rows:
        print("오류: 매물 텍스트가 비어 있습니다.", file=sys.stderr)
        return 2
    print(render_compare(rows))
    if args.md:
        Path(args.md).write_text(render_compare_md(rows), encoding="utf-8")
        print("\nMarkdown 리포트 저장: " + args.md)
    return 0


def cmd_prices(args) -> int:
    if not args.pcmd:
        print("사용법: prices show|set|stamp  (--help 참고)", file=sys.stderr)
        return 2
    table = _load_table(getattr(args, "prices", None))
    if args.pcmd == "show":
        print(render_prices(table, args.section))
        return 0
    if args.pcmd == "set":
        try:
            sec, key = table.resolve(args.part)
            if sec == "adjust":
                try:
                    value = float(args.price)
                except ValueError:
                    raise PriceError("adjust 항목은 숫자여야 합니다: {}".format(args.price))
                old = table.data["adjust"].get(key)
                table.data["adjust"][key] = value
                print("{}.{}: {} -> {}".format(sec, key, old, value))
            else:
                # gpu/cpu/board 는 단위 없는 작은 수를 만원으로, 나머지(램 GB당 등)는 원으로 해석
                value = parse_price_text(args.price, bare_man=sec in ("gpu", "cpu", "board"))
                if value is None or value <= 0:
                    raise PriceError("가격을 이해할 수 없습니다: {}".format(args.price))
                sec2, key2, old = table.set_price(args.part, value, new=args.new)
                print("{} / {}: {} -> {:,}원".format(sec2, key2, "{:,}원".format(old) if isinstance(old, (int, float)) else old, value))
            table.save()
        except PriceError as e:
            print("오류:", e, file=sys.stderr)
            return 2
        print("저장됨: {}  (기준일은 그대로입니다. 전체를 갱신했으면 'prices stamp')".format(table.path))
        return 0
    if args.pcmd == "stamp":
        table.stamp(date.today(), placeholder=True if args.keep_placeholder else False)
        table.save()
        print("as_of={}, placeholder={}  저장됨: {}".format(table.data["as_of"], table.data["placeholder"], table.path))
        return 0
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        if args.cmd == "judge":
            return cmd_judge(args)
        if args.cmd == "compare":
            return cmd_compare(args)
        if args.cmd == "prices":
            return cmd_prices(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    except FileNotFoundError as e:
        print("오류: 파일을 찾을 수 없습니다: {}".format(e.filename), file=sys.stderr)
        return 2
    return 2
