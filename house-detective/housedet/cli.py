"""명령줄 진입점: python -m housedet <명령>."""
import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import TextIO

from . import __version__, fridge, match, recycle, store
from ._jjkit import textwidth


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="house-detective", description="냉장고 유통기한 탐정과 재활용 분리 심판")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    p.add_argument("--home", help="데이터 폴더 (기본: ~/.house-detective 또는 HOUSEDET_HOME)")
    p.add_argument("--today", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fridge", help="냉장고 품목 관리")
    fs = f.add_subparsers(dest="sub", required=True)
    a = fs.add_parser("add", help="품목 추가")
    a.add_argument("names", nargs="*", help="품목 이름들 (쉼표로도 구분)")
    a.add_argument("--date", default="오늘", help="개봉일 또는 구매일 YYYY-MM-DD (기본 오늘)")
    a.add_argument("--storage", choices=fridge.STORAGES, default="냉장")
    a.add_argument("--days", type=int, help="표 대신 쓸 보관 일수")
    a.add_argument("--expires", help="포장에 적힌 기한 YYYY-MM-DD")
    a.add_argument("--from-caption", metavar="파일", help="object-memory 캡션 텍스트 파일에서 품목 찾기")
    fs.add_parser("list", help="목록 (임박 순)")
    fs.add_parser("check", help="탐정 보고서")
    r = fs.add_parser("remove", help="번호 또는 이름으로 제거")
    r.add_argument("target")

    rc = sub.add_parser("recycle", help="분리배출 판정")
    rc.add_argument("item", nargs="*", help="품목 설명")
    rc.add_argument("--from-caption", metavar="파일", help="캡션 텍스트 파일에서 품목 찾기")

    ru = sub.add_parser("rules", help="내 분리배출 규칙 (내장 규칙을 덮어씀)")
    rs = ru.add_subparsers(dest="sub", required=True)
    ra = rs.add_parser("add")
    ra.add_argument("name")
    ra.add_argument("--bin", required=True, dest="bin_", help="배출 방법")
    ra.add_argument("--why", default="내가 정한 규칙")
    ra.add_argument("--line", default="")
    ra.add_argument("--alias", action="append", default=[])
    rs.add_parser("list")
    rr = rs.add_parser("remove")
    rr.add_argument("name")
    return p


def _today(args) -> date:
    return date.fromisoformat(args.today) if args.today else date.today()


def _read_caption(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _names(raw: list[str]) -> list[str]:
    return [n.strip() for chunk in raw for n in re.split(r"[,，]", chunk) if n.strip()]


def cmd_fridge(args, out: TextIO) -> int:
    home = store.home_dir(args.home)
    today = _today(args)
    shelf = store.builtin_shelf()
    items = store.load_fridge(home)
    if args.sub == "add":
        names = _names(args.names)
        if args.from_caption:
            try:
                text = _read_caption(args.from_caption)
            except OSError as e:
                print(f"캡션 파일을 읽을 수 없습니다: {e}", file=out)
                return 2
            found = [e["name"] for e in match.find_all(shelf, text)]
            print(f"캡션에서 찾은 품목: {', '.join(found) if found else '없음'}", file=out)
            names += [n for n in found if n not in names]
        if not names:
            print("추가할 품목이 없습니다.", file=out)
            return 2
        try:
            start = store.parse_date(args.date, today)
            exp = store.parse_date(args.expires, today) if args.expires else None
        except ValueError as e:
            print(str(e), file=out)
            return 2
        nid = max([i.get("id", 0) for i in items], default=0)
        for n in names:
            expires, basis = fridge.compute_expiry(shelf, n, args.storage, start, args.days, exp)
            nid += 1
            it = {"id": nid, "name": n, "storage": args.storage, "start": start.isoformat(),
                  "expires": expires.isoformat() if expires else None, "basis": basis}
            items.append(it)
            st, left = fridge.status(it, today)
            print(f"#{nid} {n} ({args.storage}) -> {it['expires'] or '기한 모름'} [{basis}] {fridge.left_text(st, left)}", file=out)
            if expires is None:
                print("   표에 없어서 기한을 모릅니다. 포장 표시를 보고 --expires나 --days로 알려 주세요.", file=out)
        store.save_fridge(home, items)
        print(fridge.DISCLAIMER_FOOD, file=out)
        return 0
    if args.sub == "list":
        if not items:
            print("냉장고가 비었습니다.", file=out)
            return 0
        rows = []
        for it in fridge.sorted_items(items, today):
            st, left = fridge.status(it, today)
            rows.append([it["id"], it["name"], it["storage"], it.get("expires") or "-", st, fridge.left_text(st, left)])
        print("\n".join(textwidth.table(rows, ["번호", "품목", "보관", "기한", "상태", "남음"])), file=out)
        print(fridge.DISCLAIMER_FOOD, file=out)
        return 0
    if args.sub == "check":
        print("\n".join(fridge.report(items, shelf, today)), file=out)
        return 0
    if args.sub == "remove":
        t = args.target
        keep = [i for i in items if not (str(i["id"]) == t or match.norm(i["name"]) == match.norm(t))]
        if len(keep) == len(items):
            print(f"찾을 수 없습니다: {t}", file=out)
            return 1
        store.save_fridge(home, keep)
        print(f"{len(items) - len(keep)}건 제거했습니다.", file=out)
        return 0
    return 2


def cmd_recycle(args, out: TextIO) -> int:
    home = store.home_dir(args.home)
    user = store.load_user_rules(home)
    rules = recycle.merged(user, store.builtin_recycle())
    user_names = {match.norm(r["name"]) for r in user}
    queries = []
    if args.item:
        queries.append(" ".join(args.item))
    if args.from_caption:
        try:
            text = _read_caption(args.from_caption)
        except OSError as e:
            print(f"캡션 파일을 읽을 수 없습니다: {e}", file=out)
            return 2
        found = [r["name"] for r in match.find_all(rules, text)]
        print(f"캡션에서 찾은 품목: {', '.join(found) if found else '없음'}", file=out)
        queries += found
        if not found and not queries:
            queries.append(text.strip()[:30] or "(빈 캡션)")
    if not queries:
        print("판정할 품목을 적어 주세요. 예: recycle 치킨 뼈", file=out)
        return 2
    for i, q in enumerate(queries):
        if i:
            print("", file=out)
        print("\n".join(recycle.render(recycle.judge(rules, q, user_names))), file=out)
    return 0


def cmd_rules(args, out: TextIO) -> int:
    home = store.home_dir(args.home)
    rules = store.load_user_rules(home)
    if args.sub == "add":
        rules = [r for r in rules if match.norm(r["name"]) != match.norm(args.name)]
        rules.append({"name": args.name, "aliases": args.alias, "bin": args.bin_, "why": args.why, "line": args.line})
        store.save_user_rules(home, rules)
        print(f"내 규칙 추가: {args.name} -> {args.bin_}", file=out)
        return 0
    if args.sub == "list":
        if not rules:
            print("내 규칙이 없습니다.", file=out)
        for r in rules:
            print(f"{r['name']} -> {r['bin']}", file=out)
        return 0
    keep = [r for r in rules if match.norm(r["name"]) != match.norm(args.name)]
    if len(keep) == len(rules):
        print(f"찾을 수 없습니다: {args.name}", file=out)
        return 1
    store.save_user_rules(home, keep)
    print("제거했습니다.", file=out)
    return 0


COMMANDS = {"fridge": cmd_fridge, "recycle": cmd_recycle, "rules": cmd_rules}


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글/이모지로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args, out or sys.stdout)
    except ValueError as e:
        print(f"입력 오류: {e}", file=out or sys.stdout)
        return 2
