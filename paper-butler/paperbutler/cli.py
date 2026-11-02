"""명령줄 인터페이스: python -m paperbutler <명령> ..."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, List, Optional, Sequence

from .acquire import AcquireError
from .advice import REFUND_DISCLAIMER
from .butler import AddResult, Butler
from .config import Config
from .extract import DOC_TYPES, SUBTYPE_LABEL, TYPE_ALIASES
from .masking import MaskingFilter, mask_id, mask_text
from .parsing import fmt_md, won

OUT = sys.stdout


def emit(s: str = "", file: Any = None) -> None:
    """모든 출력은 마스킹을 거친다."""
    print(mask_text(s), file=file or sys.stdout)


def err(s: str) -> None:
    emit(s, file=sys.stderr)


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--home", default=argparse.SUPPRESS, help="데이터 폴더 (기본: ~/.paperbutler 또는 $PAPERBUTLER_HOME)")
    p.add_argument("--today", default=argparse.SUPPRESS, help="기준 날짜 YYYY-MM-DD (테스트/시연용)")
    p.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="자세한 로그")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    ap = argparse.ArgumentParser(
        prog="python -m paperbutler", description="개인 서류 비서: 고지서·영수증·결제문자를 읽고 추적합니다. (모든 처리는 로컬)",
        epilog="--home/--today/-v 는 명령 앞뒤 어디에 써도 됩니다.")
    ap.add_argument("--home", default=None)
    ap.add_argument("--today", default=None)
    ap.add_argument("-v", "--verbose", action="store_true", default=False)
    sub = ap.add_subparsers(dest="cmd", metavar="명령")

    p = sub.add_parser("add", parents=[common], help="파일(txt/pdf/jpg/png) 또는 '-'(표준입력 붙여넣기) 추가")
    p.add_argument("inputs", nargs="+", metavar="file|-")
    p.add_argument("--type", default="auto", choices=sorted(TYPE_ALIASES), help="문서 종류 강제 (기본 auto)")
    p.add_argument("--redact-only", action="store_true", help="저장하지 않고 마스킹된 텍스트 사본만 출력")
    p.add_argument("-o", "--output", help="--redact-only 결과를 파일로 저장")
    p.add_argument("--no-llm", action="store_true", help="LLM 보완 사용 안 함")

    p = sub.add_parser("due", parents=[common], help="납부기한·환불기한 임박 목록")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--overdue-days", type=int, default=30, help="지난 기한도 며칠 전까지 보여줄지")

    p = sub.add_parser("ledger", parents=[common], help="월별 가계부")
    p.add_argument("--month", help="YYYY-MM (기본: 이번 달)")
    p.add_argument("--no-detail", action="store_true", help="카테고리 합계만")

    p = sub.add_parser("export-csv", parents=[common], help="CSV 내보내기 (UTF-8 BOM, 엑셀용)")
    p.add_argument("--month", help="YYYY-MM (가계부 전체는 생략)")
    p.add_argument("--what", choices=["ledger", "docs"], default="ledger")
    p.add_argument("-o", "--output", help="출력 파일 (기본: <home>/export-<what>.csv)")

    p = sub.add_parser("ics", parents=[common], help="납부기한 캘린더(.ics) 만들기")
    p.add_argument("-o", "--output", help="출력 파일 (기본: <home>/paperbutler.ics)")
    p.add_argument("--days", type=int, default=None, help="오늘부터 N일 이내만 (기본: 지난 30일~전체)")
    p.add_argument("--no-refunds", action="store_true", help="환불 기한은 제외")
    p.add_argument("--include-done", action="store_true", help="납부 완료 표시한 것도 포함")

    p = sub.add_parser("find", parents=[common], help="저장한 서류 검색")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("inbox", parents=[common], help="폴더(예: 폰에서 동기화되는 폴더)의 새 파일을 모두 처리")
    p.add_argument("folder")
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--min-age", type=float, default=3.0, help="수정 후 N초 지난 파일만(동기화 중 파일 건너뜀)")
    p.add_argument("--type", default="auto", choices=sorted(TYPE_ALIASES))
    p.add_argument("--no-llm", action="store_true")

    p = sub.add_parser("show", parents=[common], help="문서 상세 보기")
    p.add_argument("id", type=int)

    p = sub.add_parser("done", parents=[common], help="납부/처리 완료 표시")
    p.add_argument("id", type=int)
    p.add_argument("--undo", action="store_true")

    p = sub.add_parser("refunds", parents=[common], help="환불/반품 기한 목록")
    p.add_argument("--days", type=int, default=30)

    sub.add_parser("doctor", parents=[common], help="사용 가능한 입력 도구(pdftotext/tesseract/LLM) 점검")
    return ap


# --------------------------------------------------------------------------
def _setup_logging(home: str, verbose: bool) -> List[logging.Handler]:
    logger = logging.getLogger("paperbutler")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handlers: List[logging.Handler] = []
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.DEBUG if verbose else logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    sh.addFilter(MaskingFilter())
    handlers.append(sh)
    try:
        os.makedirs(home, exist_ok=True)
        fh = RotatingFileHandler(os.path.join(home, "paperbutler.log"), maxBytes=512_000,
                                 backupCount=2, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        fh.addFilter(MaskingFilter())
        handlers.append(fh)
    except OSError:
        pass
    for h in handlers:
        logger.addHandler(h)
    return handlers


def _teardown_logging(handlers: Sequence[logging.Handler]) -> None:
    logger = logging.getLogger("paperbutler")
    for h in handlers:
        logger.removeHandler(h)
        h.close()


def _dday(d: dt.date, today: dt.date) -> str:
    n = (d - today).days
    if n == 0:
        return "오늘"
    return "D-%d" % n if n > 0 else "%d일 지남" % -n


def _print_add(r: AddResult) -> None:
    if r.status == "error":
        emit("[오류] %s: %s" % (r.name, r.message))
        return
    if r.status == "duplicate":
        emit("[건너뜀] %s: %s" % (r.name, r.message))
        return
    ext, adv = r.ext, r.advice
    kind = DOC_TYPES.get(ext.doc_type, ext.doc_type)
    if ext.subtype and ext.doc_type in ("bill", "notice", "card_sms"):
        kind += "·" + (SUBTYPE_LABEL.get(ext.subtype, ext.subtype) if ext.doc_type == "bill" else ext.subtype)
    emit("#%d [%s] %s" % (r.doc_id, kind, r.name))
    emit("  요약: " + adv.summary)
    emit("  할 일: " + adv.todo)
    emit("  판정: %s — %s" % (adv.verdict_display, "; ".join(adv.reasons)))
    if ext.customer_no:
        emit("  고객/납부번호: " + mask_id(ext.customer_no) + " (저장은 하되 출력은 마스킹)")
    for n in r.notes:
        emit("  참고: " + n)
    if ext.confidence < 0.7:
        emit("  주의: 자동 분류 확신이 낮습니다(%.0f%%). 원본을 한 번 확인하세요." % (ext.confidence * 100))
    if r.refund_deadline:
        emit("  " + REFUND_DISCLAIMER)


def cmd_add(b: Butler, a: argparse.Namespace) -> int:
    rc = 0
    if a.redact_only:
        if a.output and len(a.inputs) != 1:
            err("-o 는 입력이 하나일 때만 쓸 수 있습니다.")
            return 2
        for inp in a.inputs:
            try:
                out = b.redact_file(inp)
            except AcquireError as e:
                err("[오류] %s: %s" % (inp, e))
                rc = 1
                continue
            if a.output:
                with open(a.output, "w", encoding="utf-8") as f:
                    f.write(out + "\n")
                emit("마스킹 사본 저장: %s" % a.output)
                if os.path.splitext(inp)[1].lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"):
                    emit("※ 이미지 원본의 픽셀은 가려지지 않습니다. 텍스트 사본만 마스킹됩니다.")
            else:
                emit(out)
        return rc
    force = TYPE_ALIASES.get(a.type)
    for inp in a.inputs:
        if inp == "-":
            data = sys.stdin.read()
            if not data.strip():
                err("[오류] 표준입력이 비어 있습니다.")
                rc = 1
                continue
            results = b.add_text(data, "pasted.txt", force, not a.no_llm)
        else:
            results = b.add_file(inp, force, not a.no_llm)
        for r in results:
            _print_add(r)
            if r.status == "error":
                rc = 1
    return rc


def cmd_due(b: Butler, a: argparse.Namespace) -> int:
    today = b.today
    rep = b.due(a.days, a.overdue_days)
    emit("== 납부·기한 목록 (%s 기준, %d일 이내) ==" % (fmt_md(today), a.days))
    if not rep["docs"]:
        emit("  (없음)")
    for r in rep["docs"]:
        d = dt.date.fromisoformat(r["due_date"])
        label = SUBTYPE_LABEL.get(r["subtype"], r["subtype"] or DOC_TYPES.get(r["doc_type"], ""))
        who = r["issuer"] or r["merchant"] or ""
        tag = " [자동이체]" if r["auto_debit"] else ""
        emit("  [%s] %s %s %s %s%s  (#%d)" % (_dday(d, today), fmt_md(d, today), who, label,
                                              won(r["amount"]) if r["amount"] is not None else "", tag, r["id"]))
    if rep["refunds"]:
        emit("== 환불·반품 기한 ==")
        for r in rep["refunds"]:
            d = dt.date.fromisoformat(r["deadline"])
            emit("  [%s] %s %s %s  (#%d)" % (_dday(d, today), fmt_md(d, today), r["merchant"] or "상점",
                                            won(r["amount"]), r["doc_id"]))
        emit("  " + REFUND_DISCLAIMER)
    return 0


def cmd_ledger(b: Butler, a: argparse.Namespace) -> int:
    month = a.month or b.today.strftime("%Y-%m")
    if len(month) != 7 or month[4] != "-":
        err("--month 는 YYYY-MM 형식이어야 합니다 (예: 2026-11).")
        return 2
    rep = b.ledger(month)
    emit("== %s 가계부 ==" % month)
    emit("총 지출 %s (%d건)" % (won(rep["total"]), rep["count"]))
    for cat, (amt, n) in rep["totals"]:
        emit("  %-6s %12s  %d건" % (cat, "{:,}원".format(amt), n))
    if not a.no_detail and rep["rows"]:
        emit("-- 내역 --")
        for r in rep["rows"]:
            ts = r["ts"][5:16] if r["time_known"] else r["ts"][5:10]
            kind = "취소" if r["kind"] == "cancel" else "승인"
            flag = ""
            if r["kind"] == "approve" and r["status"] == "canceled":
                flag = "  (취소됨, 합계 제외)"
            elif r["kind"] == "cancel" and r["matched_to"]:
                flag = "  (승인과 상쇄, 합계 제외)"
            elif r["kind"] == "cancel":
                flag = "  (짝 없는 취소: 환불로 차감)"
            inst = "" if not r["installment"] else " %d개월" % r["installment"]
            emit("  %s  %-14s %10s %s%s [%s]%s" % (ts, (r["merchant"] or "")[:14], "{:,}원".format(r["amount"]),
                                                  kind, inst, r["category"] or "기타", flag))
    return 0


def cmd_export(b: Butler, a: argparse.Namespace) -> int:
    out = a.output or os.path.join(str(b.cfg.home), "export-%s%s.csv" % (a.what, ("-" + a.month) if a.month else ""))
    if a.what == "ledger":
        n = b.export_ledger_csv(out, a.month)
    else:
        n = b.export_docs_csv(out)
    emit("CSV %d행 저장: %s (UTF-8 BOM, 엑셀에서 바로 열림)" % (n, out))
    return 0


def cmd_ics(b: Butler, a: argparse.Namespace) -> int:
    out = a.output or os.path.join(str(b.cfg.home), "paperbutler.ics")
    n = b.write_ics(out, a.days, not a.no_refunds, a.include_done)
    emit("일정 %d개를 저장했습니다: %s (각 일정에 3일 전 알림)" % (n, out))
    return 0


def cmd_find(b: Butler, a: argparse.Namespace) -> int:
    q = " ".join(a.query)
    hits = b.find(q, a.limit)
    if not hits:
        emit("'%s' 검색 결과가 없습니다." % q)
        return 0
    emit("'%s' 검색 결과 %d건" % (q, len(hits)))
    for h in hits:
        emit("  #%d [%s] %s" % (h["id"], h["verdict"], h["summary"]))
        if h["snippet"]:
            emit("      …%s…" % h["snippet"])
    return 0


def cmd_inbox(b: Butler, a: argparse.Namespace) -> int:
    try:
        rep = b.inbox(a.folder, a.recursive, a.min_age, TYPE_ALIASES.get(a.type), not a.no_llm)
    except AcquireError as e:
        err("[오류] %s" % e)
        return 1
    for r in rep.results:
        _print_add(r)
    emit("== 인박스 결과: 새로 추가 %d, 중복 %d, 이미 처리한 파일 %d, 동기화 중 %d, 무시 %d, 오류 %d ==" % (
        len(rep.added), len(rep.duplicates), rep.skipped_seen, rep.skipped_young, rep.ignored, len(rep.errors)))
    return 1 if rep.errors else 0


def cmd_show(b: Butler, a: argparse.Namespace) -> int:
    d = b.show(a.id)
    if d is None:
        err("#%d 문서를 찾을 수 없습니다." % a.id)
        return 1
    emit("#%d  %s" % (d["id"], DOC_TYPES.get(d["doc_type"], d["doc_type"])))
    emit("  요약: %s" % d["summary"])
    emit("  할 일: %s" % d["todo"])
    ret = " %d년" % d["retention_years"] if d["verdict"] == "보관" and d["retention_years"] else ""
    emit("  판정: %s%s — %s" % (d["verdict"], ret, "; ".join(d["reasons"])))
    for k, label in (("issuer", "기관"), ("merchant", "가맹점"), ("due_date", "납부기한"),
                     ("billing_month", "청구월"), ("event_dt", "일시")):
        if d.get(k):
            emit("  %s: %s" % (label, d[k]))
    if d["amount"] is not None:
        emit("  금액: %s" % won(d["amount"]))
    if d["customer_no_masked"]:
        emit("  고객/납부번호: %s" % d["customer_no_masked"])
    emit("  자동이체: %s%s" % ("예" if d["auto_debit"] else "아니오",
                             (" (매월 %d일)" % d["auto_debit_day"]) if d["auto_debit_day"] else ""))
    emit("  원본: %s" % os.path.join(str(b.cfg.home), d["stored_path"] or ""))
    emit("  완료 표시: %s" % ("예" if d["done"] else "아니오"))
    for r in d["refunds"]:
        emit("  환불 기한: %s (구매일 %s + %d일)" % (r["deadline"], r["purchase_date"], r["policy_days"]))
    return 0


def cmd_done(b: Butler, a: argparse.Namespace) -> int:
    if not b.mark_done(a.id, not a.undo):
        err("#%d 문서를 찾을 수 없습니다." % a.id)
        return 1
    emit("#%d %s" % (a.id, "완료 표시를 해제했습니다." if a.undo else "납부/처리 완료로 표시했습니다."))
    return 0


def cmd_refunds(b: Butler, a: argparse.Namespace) -> int:
    today = b.today
    end = (today + dt.timedelta(days=a.days)).isoformat()
    rows = b.db.refunds_between(today.isoformat(), end)
    emit("== 환불·반품 기한 (%d일 이내) ==" % a.days)
    if not rows:
        emit("  (없음)")
    for r in rows:
        d = dt.date.fromisoformat(r["deadline"])
        emit("  [%s] %s %s %s — 구매 %s + %d일  (#%d)" % (
            _dday(d, today), fmt_md(d, today), r["merchant"] or "상점", won(r["amount"]),
            r["purchase_date"], r["policy_days"], r["doc_id"]))
    emit("  " + REFUND_DISCLAIMER)
    return 0


def cmd_doctor(b: Butler, a: argparse.Namespace) -> int:
    cfg = b.cfg
    emit("데이터 폴더: %s" % cfg.home)
    emit("pdftotext: %s" % (b.which(cfg.pdftotext_cmd) or "없음 (PDF 는 텍스트 붙여넣기로 대체 가능)"))
    emit("tesseract: %s" % (b.which(cfg.tesseract_cmd) or "없음 (이미지는 VLM 또는 붙여넣기로 대체)"))
    emit("LLM/VLM: %s" % (("%s (%s)" % (cfg.llm_url, cfg.llm_model or "모델 미지정")) if b.llm else "미설정 (규칙 기반만 사용)"))
    emit("이미지 백엔드: %s, OCR 언어: %s" % (cfg.image_backend, cfg.ocr_lang))
    return 0


COMMANDS = {
    "add": cmd_add, "due": cmd_due, "ledger": cmd_ledger, "export-csv": cmd_export, "ics": cmd_ics,
    "find": cmd_find, "inbox": cmd_inbox, "show": cmd_show, "done": cmd_done, "refunds": cmd_refunds,
    "doctor": cmd_doctor,
}


def main(argv: Optional[Sequence[str]] = None, *, llm: Any = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    today = None
    if a.today:
        try:
            today = dt.date.fromisoformat(a.today)
        except ValueError:
            err("--today 는 YYYY-MM-DD 형식이어야 합니다.")
            return 2
    cfg = Config.load(a.home)
    handlers = _setup_logging(str(cfg.home), a.verbose)
    try:
        with Butler(cfg, llm=llm, today=today) as b:
            return COMMANDS[a.cmd](b, a)
    except KeyboardInterrupt:
        err("중단되었습니다.")
        return 130
    finally:
        _teardown_logging(handlers)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
