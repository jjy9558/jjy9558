"""명령줄 진입점: python -m sellstudio <명령>.

자동 게시·크롤링은 없습니다. 모든 출력은 복사해서 직접 붙여넣는 용도입니다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import TextIO

from . import __version__
from . import captions as cap
from . import config as cfg
from . import item as itemmod
from . import ledger as lg
from . import lintcheck
from . import markets as mk
from . import page as pg
from . import pcjudge
from . import prices
from . import privacy
from ._jjkit import llm

DEFAULT_LLM_URL = "http://127.0.0.1:8080/v1"
DEFAULT_LEDGER = "sell-ledger.json"


class CliError(Exception):
    pass


def _add_pc_opts(sp):
    sp.add_argument("--judge-dir", help="used-pc-judge 폴더 (기본: 저장소의 형제 폴더, 환경변수 SELLSTUDIO_USEDPC_DIR)")
    sp.add_argument("--judge-cmd", help='판정기 실행 명령 (예: "used-pc-judge"), 환경변수 SELLSTUDIO_JUDGE_CMD')
    sp.add_argument("--no-pc-check", action="store_true", help="PC 부품 조회를 하지 않음")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sell-studio",
        description="중고 판매 스튜디오: 사진 → 판매글 초안·상세페이지·개인정보 점검·되팔이 장부 (복사용 출력만, 자동 게시 없음)")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="사진으로 물품 폴더 만들기 (원본은 읽기만 하고 사본을 둠)")
    n.add_argument("photos", nargs="+", help="사진 파일 또는 폴더")
    n.add_argument("--out", help="물품 폴더 (기본: ./sell-<첫 사진 이름>)")
    n.add_argument("--kind", choices=("pc", "general"), default="general", help="pc 면 used-pc-judge 조회를 시도")
    n.add_argument("--name", help="상품명 (모델명 포함)")
    n.add_argument("--spec", help="사양/용량 등 한 줄")
    n.add_argument("--condition", help="상태 설명 (예: 사용 1년, 생활기스 약간) - 설명란에 들어감")
    n.add_argument("--grade", help="제목에 붙일 짧은 상태 (예: A급, 상태좋음)")
    n.add_argument("--defect", action="append", default=[], help="하자 (여러 번 가능)")
    n.add_argument("--included", action="append", default=[], help="구성품 (여러 번 가능)")
    n.add_argument("--trade", help="거래 방법 (예: 직거래 OO동, 택배 가능)")
    n.add_argument("--note", help="추가 설명")
    n.add_argument("--force", action="store_true")

    c = sub.add_parser("caption", help="사진 캡션 (로컬 VLM, --no-llm 이면 템플릿)")
    c.add_argument("item")
    c.add_argument("--no-llm", action="store_true", help="VLM 없이 템플릿 캡션")
    c.add_argument("--llm-url", help="OpenAI 호환 로컬 주소 (기본 $SELLSTUDIO_LLM_URL 또는 %s)" % DEFAULT_LLM_URL)
    c.add_argument("--llm-model")
    c.add_argument("--allow-remote", action="store_true", help="로컬/사설망이 아닌 서버도 허용 (사진이 외부로 전송됨)")

    pr = sub.add_parser("price", help="붙여넣은 시세 줄에서 중앙값과 범위 계산")
    pr.add_argument("item")
    pr.add_argument("--paste", required=True, help="시세 줄이 든 파일, - 면 표준입력 (한 줄에 매물 하나)")
    pr.add_argument("--final", help="최종 판매가를 직접 정함 (예: 27만)")
    _add_pc_opts(pr)

    d = sub.add_parser("draft", help="플랫폼별 판매글 초안 (당근/번개장터/스마트스토어)")
    d.add_argument("item")
    d.add_argument("--market", default="all", help="daangn | bunjang | smartstore | all (설정 파일에 추가한 이름도 가능)")
    d.add_argument("--price", help="가격을 직접 지정 (기본: 시세 중앙값)")
    d.add_argument("--markets", help="플랫폼 규칙 JSON (config dump markets 로 뽑아 수정)")
    d.add_argument("--rules", help="린트 규칙 JSON")
    _add_pc_opts(d)

    g = sub.add_parser("page", help="상세페이지 HTML (혼자 완결, 이미지는 상대경로)")
    g.add_argument("item")
    g.add_argument("--price")
    g.add_argument("--out", help="물품 폴더 안의 파일 이름 (기본 detail.html)")

    li = sub.add_parser("lint", help="금칙어·과장·전화/계좌/주소/송장 노출 검사 (물품 폴더, 텍스트 파일, -)")
    li.add_argument("target")
    li.add_argument("--rules", help="린트 규칙 JSON")
    li.add_argument("--strict", action="store_true", help="주의도 실패로 취급")

    pv = sub.add_parser("privacy-check", help="사진 EXIF GPS와 파일명/캡션의 송장·주소 단서 점검")
    pv.add_argument("targets", nargs="+", help="물품 폴더, 사진 파일 또는 사진 폴더")
    pv.add_argument("--strip", action="store_true", help="APP1(EXIF/XMP)을 뺀 사본을 --out 에 쓴다 (원본 불변)")
    pv.add_argument("--out", help="사본 폴더 (물품 폴더는 기본 photos_clean, 그 외 ./photos_clean)")
    pv.add_argument("--drop-orientation", action="store_true", help="회전 정보까지 지움 (사진이 눕을 수 있음)")

    lgp = sub.add_parser("ledger", help="되팔이 장부: add / sold / list / report")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ledger", default=os.environ.get("SELLSTUDIO_LEDGER", DEFAULT_LEDGER), help="장부 JSON (기본 %s)" % DEFAULT_LEDGER)
    common.add_argument("--today", help=argparse.SUPPRESS)
    ls = lgp.add_subparsers(dest="lcmd", required=True)
    a = ls.add_parser("add", parents=[common], help="매입 기록")
    a.add_argument("--name", required=True)
    a.add_argument("--buy", required=True, help="매입가 (150000, 15만)")
    a.add_argument("--buy-fee", help="매입 수수료/택배비")
    a.add_argument("--repair", help="수리·청소 비용")
    a.add_argument("--other", help="기타 비용")
    a.add_argument("--date", help="매입일 YYYY-MM-DD (처음 추가할 때만 기본 오늘)")
    a.add_argument("--hours", type=float, help="들인 시간(시간 단위): 시급 계산용")
    a.add_argument("--note")
    a.add_argument("--id", help="ID 직접 지정 (같은 이름·매입가 물건이 둘 이상일 때)")
    s = ls.add_parser("sold", parents=[common], help="판매 기록")
    s.add_argument("id", help="ID, ID 앞부분, 또는 이름")
    s.add_argument("--price", required=True, help="판매가")
    s.add_argument("--fee", help="판매 수수료 금액")
    s.add_argument("--fee-rate", type=float, help="수수료율(퍼센트, 예: 3.5)")
    s.add_argument("--market", help="daangn | bunjang | smartstore: 수수료를 안 주면 설정의 근사 수수료율 사용")
    s.add_argument("--markets", help="플랫폼 규칙 JSON")
    s.add_argument("--shipping", help="판매자가 낸 택배비")
    s.add_argument("--date", help="판매일 YYYY-MM-DD (판매 처리된 건은 생략하면 기존 날짜 유지)")
    ls.add_parser("list", parents=[common], help="장부 목록")
    r = ls.add_parser("report", parents=[common], help="순이익 요약")
    r.add_argument("--csv", help="CSV 저장 경로 (엑셀용 UTF-8 BOM)")
    r.add_argument("--json", help="JSON 저장 경로")

    cf = sub.add_parser("config", help="기본 설정 꺼내 보기")
    cf.add_argument("what", choices=("dump",))
    cf.add_argument("which", choices=("markets", "rules"))
    return p


# ---------------------------------------------------------------- helpers
def _w(out: TextIO, text: str = "") -> None:
    print(text, file=out)


def _read_text(src: str) -> str:
    if src == "-":
        raw = sys.stdin.buffer.read()
    else:
        try:
            raw = Path(src).read_bytes()
        except OSError as e:
            raise CliError("파일을 읽지 못했습니다: %s (%s)" % (src, e)) from e
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949", "replace")


def _money_arg(text: str | None, what: str) -> int | None:
    if text is None:
        return None
    v = prices.parse_won(text)
    if v is None or v < 0:
        raise CliError("%s 을(를) 읽을 수 없습니다: %r (예: 270000, 27만)" % (what, text))
    return v


def make_client(args):
    """VLM 클라이언트 또는 None. 로컬/사설 주소만 허용(jjkit)."""
    if args.no_llm:
        return None
    env = os.environ
    url = args.llm_url or env.get("SELLSTUDIO_LLM_URL") or DEFAULT_LLM_URL
    model = args.llm_model or env.get("SELLSTUDIO_LLM_MODEL") or "local-model"
    allow = bool(args.allow_remote) or llm._env_true(env.get("SELLSTUDIO_ALLOW_REMOTE_LLM"))
    return llm.LLMClient(url, model, api_key=env.get("SELLSTUDIO_LLM_KEY", ""),
                         vision_model=env.get("SELLSTUDIO_LLM_VISION_MODEL", ""), allow_remote=allow, timeout=120.0)


def _load_markets(args) -> dict:
    try:
        return cfg.load_markets(getattr(args, "markets", None))
    except ValueError as e:
        raise CliError(str(e)) from e


def _load_rules(args) -> dict:
    try:
        return cfg.load_rules(getattr(args, "rules", None))
    except ValueError as e:
        raise CliError(str(e)) from e


def _load_item(path: str) -> tuple[Path, dict]:
    try:
        return itemmod.load(path)
    except itemmod.ItemError as e:
        raise CliError(str(e)) from e


# ---------------------------------------------------------------- commands
def cmd_new(args, out):
    try:
        files, warns = itemmod.collect_images(args.photos)
    except itemmod.ItemError as e:
        raise CliError(str(e)) from e
    if not files:
        raise CliError("쓸 수 있는 사진(jpg/png/webp/gif)이 없습니다")
    dest = Path(args.out) if args.out else Path("sell-" + itemmod.slugify(files[0].stem))
    fields = {"kind": args.kind, "name": args.name, "spec": args.spec, "condition": args.condition, "grade": args.grade,
              "defects": args.defect, "included": args.included, "trade": args.trade, "notes": args.note}
    try:
        data = itemmod.create(dest, files, fields, force=args.force)
    except itemmod.ItemError as e:
        raise CliError(str(e)) from e
    for w in warns:
        _w(out, "[주의] " + w)
    _w(out, "물품 폴더를 만들었습니다: %s (사진 %d장 사본)" % (dest, len(data["photos"])))
    _w(out, "원본 사진은 읽기만 했고 바뀌지 않았습니다.")
    _w(out, "다음: privacy-check %s  ->  caption %s --no-llm  ->  price/draft/page" % (dest, dest))
    return 0


def cmd_caption(args, out):
    root, data = _load_item(args.item)
    try:
        client = make_client(args)
    except llm.LLMError as e:
        raise CliError(str(e)) from e
    if client is None:
        _w(out, "VLM 없이 템플릿 캡션을 씁니다 (--no-llm)")
    warns = cap.caption_item(root, data, client)
    itemmod.save(root, data)
    for ph in data["photos"]:
        _w(out, "%s [%s] %s" % (ph["file"], ph["caption_source"], ph["caption"]))
        if ph.get("defects"):
            _w(out, "    하자 후보: " + ", ".join(ph["defects"]))
        if ph.get("privacy_flags"):
            _w(out, "    [주의] 개인정보 의심: " + ", ".join(ph["privacy_flags"]))
    for w in warns:
        _w(out, "[주의] " + w)
    if client is not None:
        _w(out, "VLM 결과는 틀릴 수 있습니다. 사양·하자는 직접 확인하고 `new` 옵션이나 item.json 에서 고치세요.")
    return 0


def _pc_step(args, data, price_value, out) -> None:
    if getattr(args, "no_pc_check", False):
        return
    res = pcjudge.run_pc_check(data, price_value, getattr(args, "judge_cmd", None), getattr(args, "judge_dir", None))
    data["pc_check"] = res
    for line in pcjudge.render_pc_check(res):
        _w(out, line)


def cmd_price(args, out):
    root, data = _load_item(args.item)
    summary = prices.summarize(_read_text(args.paste))
    if summary.count == 0:
        for w in summary.warnings:
            _w(out, "[주의] " + w)
        raise CliError("시세를 하나도 읽지 못했습니다. 한 줄에 매물 하나, 가격은 '28만원', '270,000원' 형태로 적어 주세요")
    data["price"] = summary.to_dict()
    if args.final:
        data["price_final"] = _money_arg(args.final, "최종가")
    _w(out, "시세 %d건 (붙여넣은 값만 사용, 수집·검색 없음)" % summary.count)
    _w(out, "  최저 %s원 / 중앙값 %s원 / 최고 %s원" % tuple(format(x, ",") for x in (summary.low, summary.median, summary.high)))
    _w(out, "  가격 힌트: 중앙값 %s원 근처, 범위 %s ~ %s원" % tuple(format(x, ",") for x in (summary.median, summary.low, summary.high)))
    if data.get("price_final") is not None:
        _w(out, "  최종가(직접 지정): %s원" % format(data["price_final"], ","))
    for w in summary.warnings:
        _w(out, "[주의] " + w)
    _w(out, "시세는 상태·구성이 같은 글끼리 비교해야 의미가 있습니다. 최종 가격은 직접 정하세요.")
    _pc_step(args, data, data.get("price_final") or summary.median, out)
    itemmod.save(root, data)
    return 0


def cmd_draft(args, out):
    root, data = _load_item(args.item)
    markets = _load_markets(args)
    rules = _load_rules(args)
    names = cfg.market_names(markets)
    wanted = names if args.market == "all" else [args.market]
    for m in wanted:
        if m not in names:
            raise CliError("알 수 없는 플랫폼: %s (가능: %s)" % (m, ", ".join(names)))
    price = _money_arg(args.price, "가격")
    drafts = data.setdefault("drafts", {})
    (root / "drafts").mkdir(exist_ok=True)
    n_err = 0
    for m in wanted:
        d = mk.build_draft(data, m, markets[m], price)
        drafts[m] = d
        text = mk.render_draft(d)
        (root / "drafts" / (m + ".txt")).write_text(text, encoding="utf-8")
        _w(out, text)
        for note in d["notes"]:
            _w(out, "[안내] " + note)
        finds = lintcheck.lint_draft(d, rules)
        n_err += sum(f.severity == "error" for f in finds)
        for line in lintcheck.render_findings(finds):
            _w(out, line)
        _w(out, "")
    if data.get("kind") == "pc" or pcjudge.looks_like_pc(data):
        _pc_step(args, data, mk.suggested_price(data, price), out)
    itemmod.save(root, data)
    _w(out, "플랫폼 규칙(제목 길이 등): " + cfg.APPROX_NOTE)
    _w(out, "초안은 %s 에 저장했습니다. 복사해서 직접 등록하세요 (자동 게시는 하지 않습니다)." % (root / "drafts"))
    return 1 if n_err else 0


def cmd_page(args, out):
    root, data = _load_item(args.item)
    price = _money_arg(args.price, "가격")
    name = args.out or "detail.html"
    dest = (root / name)
    if dest.resolve().parent != root.resolve():
        raise CliError("상세페이지는 물품 폴더 바로 아래에 써야 이미지 상대경로가 맞습니다: %s" % name)
    doc, warns = pg.build_page(root, data, price)
    probs = pg.self_contained_problems(doc)
    if probs:
        raise CliError("자기완결 검사 실패: " + ", ".join(probs))
    dest.write_text(doc, encoding="utf-8")
    _w(out, "상세페이지를 저장했습니다: %s (%d바이트, 외부 의존 없음, 이미지는 상대경로)" % (dest, len(doc.encode("utf-8"))))
    for w in warns:
        _w(out, "[주의] " + w)
    finds = lintcheck.lint_text(lintcheck.html_to_text(doc), cfg.load_rules(), "detail.html")
    for line in lintcheck.render_findings(finds):
        _w(out, line)
    _w(out, "이미지와 같이 올려야 합니다: %s/ (스마트스토어에는 이미지를 따로 업로드하고 본문 HTML만 붙여넣는 방식을 권합니다)"
       % pg.choose_photo_dir(root, data)[0])
    return 1 if any(f.severity == "error" for f in finds) else 0


def cmd_lint(args, out):
    rules = _load_rules(args)
    if args.target == "-":
        findings = lintcheck.lint_text(_read_text("-"), rules, "stdin")
    else:
        p = Path(args.target)
        if p.is_dir():
            root, data = _load_item(args.target)
            findings = lintcheck.lint_item(root, data, rules)
            if not data.get("drafts") and not (root / "detail.html").exists():
                _w(out, "[안내] 아직 draft/page 결과가 없어 입력한 항목과 캡션만 검사합니다")
        elif p.is_file():
            text = _read_text(args.target)
            if p.suffix.lower() in (".html", ".htm"):
                text = lintcheck.html_to_text(text)
            findings = lintcheck.lint_text(text, rules, p.name)
        else:
            raise CliError("대상을 찾을 수 없습니다: %s" % args.target)
    for line in lintcheck.render_findings(findings):
        _w(out, line)
    errors = any(f.severity == "error" for f in findings)
    warns = any(f.severity == "warn" for f in findings)
    return 1 if errors or (args.strict and warns) else 0


def cmd_privacy(args, out):
    total_gps = 0
    for tgt in args.targets:
        p = Path(tgt)
        if p.is_dir() and itemmod.is_item_dir(p):
            root, data = _load_item(tgt)
            reports = privacy.inspect_item(root, data)
            sources = [Path(r.path) for r in reports]
            default_out = root / "photos_clean"
            _w(out, "== 물품 폴더 %s ==" % tgt)
        else:
            try:
                reports = privacy.inspect_paths([tgt])
            except itemmod.ItemError as e:
                raise CliError(str(e)) from e
            sources = [Path(r.path) for r in reports]
            root = data = None
            default_out = Path("photos_clean")
            _w(out, "== %s ==" % tgt)
        for line in privacy.render_reports(reports):
            _w(out, line)
        total_gps += sum(r.gps for r in reports)
        if args.strip:
            dest = Path(args.out) if args.out else default_out
            if len(args.targets) > 1 and args.out:
                dest = dest / (Path(tgt).name or "x")
            try:
                res = privacy.strip_copies(sources, dest, keep_orientation=not args.drop_orientation)
            except itemmod.ItemError as e:
                raise CliError(str(e)) from e
            n = sum(r["stripped"] for r in res)
            _w(out, "사본 %d장을 %s 에 썼습니다 (EXIF/XMP 제거 %d장, 원본은 바뀌지 않음)" % (len(res), dest, n))
            for r in res:
                if r["note"]:
                    _w(out, "  [주의] %s: %s" % (Path(r["src"]).name, r["note"]))
            if not args.drop_orientation:
                _w(out, "  회전 값(Orientation)만 남겼습니다. 위치·시각·기기 정보는 없습니다")
            if data is not None:
                data["privacy"] = {"gps_photos": [r.label for r in reports if r.gps], "cleaned": True,
                                   "clean_dir": Path(dest).name if Path(dest).parent == root else str(dest)}
                itemmod.save(root, data)
        elif data is not None:
            data["privacy"] = {"gps_photos": [r.label for r in reports if r.gps], "cleaned": False}
            itemmod.save(root, data)
    return 1 if (total_gps and not args.strip) else 0


def _ledger_today(args) -> date:
    try:
        return lg.parse_date(args.today, date.today())
    except lg.LedgerError as e:
        raise CliError(str(e)) from e


def cmd_ledger(args, out):
    today = _ledger_today(args)
    try:
        data = lg.load(args.ledger)
        if args.lcmd == "add":
            item, st = lg.add(data, name=args.name, buy_price=args.buy, buy_fee=args.buy_fee, repair=args.repair,
                              other_cost=args.other, buy_date=args.date, note=args.note, hours=args.hours,
                              item_id=args.id, today=today)
            if st != "unchanged":
                lg.save(args.ledger, data)
            msg = {"added": "추가했습니다", "updated": "갱신했습니다", "unchanged": "이미 같은 내용이 있어 바꾸지 않았습니다"}[st]
            _w(out, "%s: %s  %s (매입 %s원, 총비용 %s원)" % (msg, item["id"], item["name"], format(item["buy_price"], ","),
                                                         format(lg.cost_total(item), ",")))
        elif args.lcmd == "sold":
            rate = None
            if args.market and args.fee is None and args.fee_rate is None:
                markets = _load_markets(args)
                if args.market not in cfg.market_names(markets):
                    raise CliError("알 수 없는 플랫폼: %s" % args.market)
                rate = float(markets[args.market].get("fee_rate", 0.0))
            item, st = lg.sell(data, args.id, price=args.price, fee=args.fee, fee_rate=args.fee_rate, market_rate=rate,
                               market=args.market, shipping=args.shipping, sold_date=args.date, today=today)
            if st != "unchanged":
                lg.save(args.ledger, data)
            m = lg.metrics(item, today)
            msg = {"sold": "판매 기록", "updated": "판매 기록 갱신", "unchanged": "이미 같은 판매 기록이 있어 바꾸지 않았습니다"}[st]
            s = item["sold"]
            _w(out, "%s: %s  판매가 %s원 - 총비용 %s원 - 수수료 %s원(%s) - 택배비 %s원 = 순이익 %s원"
               % (msg, item["id"], format(s["price"], ","), format(m["cost_total"], ","), format(s["fee"], ","),
                  s["fee_basis"], format(s.get("shipping", 0), ","), format(m["profit"], ",")))
            if m["profit"] < 0:
                _w(out, "[주의] 손해 거래입니다")
        elif args.lcmd == "list":
            rs = lg.rows(data, today)
            if not rs:
                _w(out, "장부가 비어 있습니다")
            for r in rs:
                _w(out, "%s  %s  매입 %s원  %s" % (r["id"], r["name"], format(r["buy_price"], ","),
                                                   "판매 %s원 순이익 %s원" % (format(r["sell_price"], ","), format(r["profit"], ",")) if r["sold"] else "보유중"))
        else:  # report
            for line in lg.render_report(data, today):
                _w(out, line)
            rs = lg.rows(data, today)
            if args.csv:
                lg.write_csv(args.csv, rs)
                _w(out, "CSV 저장: %s" % args.csv)
            if args.json:
                lg.write_json(args.json, rs, lg.totals(rs))
                _w(out, "JSON 저장: %s" % args.json)
    except lg.LedgerError as e:
        raise CliError(str(e)) from e
    return 0


def cmd_config(args, out):
    obj = cfg.DEFAULT_MARKETS if args.which == "markets" else cfg.DEFAULT_RULES
    _w(out, json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


COMMANDS = {"new": cmd_new, "caption": cmd_caption, "price": cmd_price, "draft": cmd_draft, "page": cmd_page,
            "lint": cmd_lint, "privacy-check": cmd_privacy, "ledger": cmd_ledger, "config": cmd_config}


def main(argv: list[str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글/이모지로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        return COMMANDS[args.cmd](args, out)
    except CliError as e:
        print("오류: %s" % e, file=err)
        return 2
