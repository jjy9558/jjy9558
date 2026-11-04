"""오케스트레이션: 획득 -> 분석 -> (선택)LLM -> 저장 -> 가계부/환불/ICS/검색."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import contract
from . import ics as icsmod
from .acquire import AcquireError, SUPPORTED_EXT, acquire_text
from .advice import ACTION, DISCARD, KEEP, REFUND_DISCLAIMER, advise
from .categories import categorize
from .config import Config
from .extract import DOC_TYPES, SUBTYPE_LABEL, TYPE_ALIASES, Extraction, analyze
from .llm import LLMClient, LLMError, enrich_with_llm
from .masking import mask_id, mask_text, redact_document
from .parsing import fmt_md, normalize_text, won
from .storage import Database

log = logging.getLogger("paperbutler")

MAX_TEXT_CHARS = 200_000
NO_REFUND_CATEGORIES = {"식비", "교통", "공과금", "의료", "구독"}
DEFAULT_OVERDUE_DAYS = 30
TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", ".partial", ".swp", "~", ".syncthing")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def split_messages(text: str) -> List[str]:
    """[Web발신]/[국외발신] 표지가 2개 이상이면 문자 메시지별로 나눈다(문자 내보내기 파일 등)."""
    markers = re.findall(r"(?m)^\s*\[(?:Web발신|국외발신)\]", text)
    if len(markers) < 2:
        return [text]
    parts = re.split(r"(?m)^(?=\s*\[(?:Web발신|국외발신)\])", text)
    parts = [p.strip() for p in parts if p.strip()]
    return parts if len(parts) >= 2 else [text]


def _safe_name(name: str) -> str:
    base = os.path.basename(name) or "doc"
    base = re.sub(r"[^\w.\-가-힣]+", "_", base).strip("._") or "doc"
    return base[:80]


@dataclass
class AddResult:
    status: str                       # added | duplicate | error
    doc_id: Optional[int] = None
    name: str = ""
    ext: Optional[Extraction] = None
    advice: Any = None
    notes: List[str] = field(default_factory=list)
    payment_status: str = ""
    refund_deadline: Optional[str] = None
    message: str = ""
    stored_path: str = ""


@dataclass
class InboxReport:
    results: List[AddResult] = field(default_factory=list)
    skipped_seen: int = 0
    skipped_young: int = 0
    ignored: int = 0

    @property
    def added(self) -> List[AddResult]:
        return [r for r in self.results if r.status == "added"]

    @property
    def errors(self) -> List[AddResult]:
        return [r for r in self.results if r.status == "error"]

    @property
    def duplicates(self) -> List[AddResult]:
        return [r for r in self.results if r.status == "duplicate"]


class Butler:
    def __init__(self, cfg: Config, llm: Any = None, today: Optional[dt.date] = None,
                 now: Optional[dt.datetime] = None, db: Optional[Database] = None,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 runner: Callable[..., Any] = subprocess.run):
        self.cfg = cfg
        self._today = today
        self._now = now
        self.which = which
        self.runner = runner
        cfg.home.mkdir(parents=True, exist_ok=True)
        self.db = db or Database(cfg.db_path)
        self.llm = llm if llm is not None else self._make_llm()

    # -- 기본 ---------------------------------------------------------------
    @property
    def today(self) -> dt.date:
        return self._today or dt.date.today()

    @property
    def now(self) -> dt.datetime:
        return self._now or dt.datetime.now()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Butler":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _make_llm(self) -> Any:
        if not self.cfg.llm_url:
            return None
        try:
            return LLMClient(self.cfg.llm_url, self.cfg.llm_model, self.cfg.llm_api_key,
                             self.cfg.vision_model, self.cfg.llm_timeout, self.cfg.allow_remote_llm)
        except LLMError as e:
            log.warning("LLM 비활성화: %s", e)
            return None

    # -- 추가 ---------------------------------------------------------------
    def redact_file(self, path: str) -> str:
        """--redact-only: 저장 없이 마스킹된 텍스트만 만든다."""
        if path == "-":
            text = sys.stdin.read()
        else:
            text = acquire_text(path, self.cfg, self.llm, self.which, self.runner).text
        return redact_document(normalize_text(text))

    def add_text(self, text: str, name: str = "pasted.txt", force_type: Optional[str] = None,
                 use_llm: bool = True) -> List[AddResult]:
        raw = text.encode("utf-8")
        file_sha = hashlib.sha256(raw).hexdigest()
        return self._add_content(text, "text", file_sha, name, raw, None, force_type, use_llm)

    def add_file(self, path: str, force_type: Optional[str] = None, use_llm: bool = True,
                 file_sha: Optional[str] = None) -> List[AddResult]:
        name = os.path.basename(path)
        if not os.path.isfile(path):
            return [AddResult("error", name=name, message="파일이 없습니다: %s" % name)]
        try:
            sha = file_sha or sha256_file(path)
        except OSError as e:
            return [AddResult("error", name=name, message="파일을 읽을 수 없습니다: %s" % e.strerror)]
        row = self.db.find_doc_by_sha(sha)
        if row is not None:
            return [AddResult("duplicate", row["id"], name, message="이미 추가된 파일입니다 (#%d)" % row["id"])]
        try:
            acq = acquire_text(path, self.cfg, self.llm, self.which, self.runner)
        except AcquireError as e:
            return [AddResult("error", name=name, message=str(e))]
        return self._add_content(acq.text, acq.method, sha, name, None, path, force_type, use_llm)

    def _add_content(self, text: str, method: str, file_sha: str, name: str,
                     raw: Optional[bytes], src_path: Optional[str], force_type: Optional[str],
                     use_llm: bool) -> List[AddResult]:
        parts = split_messages(text) if method == "text" else [text]
        results = []
        for i, part in enumerate(parts):
            sha = file_sha if len(parts) == 1 else hashlib.sha256((file_sha + ":%d" % i).encode()).hexdigest()
            label = name if len(parts) == 1 else "%s#%d" % (name, i + 1)
            results.append(self._ingest(part, method, sha, file_sha, label, name, raw, src_path,
                                        force_type, use_llm))
        return results

    def _ingest(self, text: str, method: str, sha: str, file_sha: str, label: str, name: str,
                raw: Optional[bytes], src_path: Optional[str], force_type: Optional[str],
                use_llm: bool) -> AddResult:
        row = self.db.find_doc_by_sha(sha)
        if row is not None:
            return AddResult("duplicate", row["id"], label, message="이미 추가된 문서입니다 (#%d)" % row["id"])
        if force_type in TYPE_ALIASES:
            force_type = TYPE_ALIASES[force_type]
        if not normalize_text(text):
            return AddResult("error", name=label, message="내용이 비어 있습니다.")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]
        today = self.today
        ext = analyze(text, today, force_type)
        if use_llm and self.llm is not None:
            ext = enrich_with_llm(ext, text, self.llm, self.cfg.llm_threshold)

        category = ""
        if ext.doc_type in ("card_sms", "receipt") and ext.merchant:
            category = categorize(ext.merchant, self.cfg.category_rules)
        elif ext.doc_type in ("card_sms", "receipt"):
            category = "기타"

        folder_day = today
        if ext.event_dt:
            try:
                folder_day = dt.date.fromisoformat(ext.event_dt[:10])
            except ValueError:
                pass
        dest = self._dest_path(folder_day, file_sha, name)

        notes: List[str] = list(ext.notes)
        pay_status = ""
        refund_deadline: Optional[dt.date] = None
        refund_days: Optional[int] = None
        with self.db.transaction():
            adv0 = advise(ext, today, category=category)
            doc_id = self.db.insert_document(
                sha256=sha, added_at=self.now.isoformat(timespec="seconds"), orig_name=name,
                stored_path=str(dest.relative_to(self.cfg.home)), method=method,
                doc_type=ext.doc_type, subtype=ext.subtype, issuer=ext.issuer, merchant=ext.merchant,
                amount=ext.amount, due_date=ext.due_date, event_dt=ext.event_dt, doc_date=ext.doc_date,
                billing_month=ext.billing_month, customer_no=ext.customer_no,
                auto_debit=int(ext.auto_debit), auto_debit_day=ext.auto_debit_day,
                installment=ext.installment, is_cancel=int(ext.is_cancel),
                summary=adv0.summary, todo=adv0.todo, verdict=adv0.verdict,
                reasons=json.dumps(adv0.reasons, ensure_ascii=False), retention_years=adv0.retention_years,
                confidence=ext.confidence,
                text=mask_text(normalize_text(text)),
                extra=json.dumps({"scores": ext.scores, "sources": ext.sources, "notes": ext.notes,
                                  "flags": ext.flags, "category": category, "card": ext.card,
                                  "online": ext.online}, ensure_ascii=False))
            self._store_original(dest, raw, src_path)

            # 가계부
            if ext.doc_type in ("card_sms", "receipt") and ext.amount:
                pay_status, extra_notes = self._record_payment(doc_id, ext, category)
                notes.extend(extra_notes)
            # 환불 기한
            if ext.doc_type == "receipt" and not ext.is_cancel and ext.amount and pay_status != "duplicate":
                refund_deadline, refund_days, rnote = self._record_refund(doc_id, ext, category)
                if rnote:
                    notes.append(rnote)
            adv = advise(ext, today, category=category, pay_status=pay_status,
                         refund_deadline=refund_deadline, refund_days=refund_days)
            self.db.update_document(
                doc_id, summary=adv.summary, todo=adv.todo, verdict=adv.verdict,
                reasons=json.dumps(adv.reasons, ensure_ascii=False), retention_years=adv.retention_years)
        log.info("문서 추가 #%s 종류=%s 판정=%s", doc_id, ext.doc_type, adv.verdict)
        return AddResult("added", doc_id, label, ext, adv, notes, pay_status,
                         refund_deadline.isoformat() if refund_deadline else None,
                         stored_path=str(dest))

    # -- 저장 보조 ------------------------------------------------------------
    def _dest_path(self, day: dt.date, file_sha: str, name: str) -> Path:
        d = self.cfg.docs_dir / ("%04d" % day.year) / ("%02d" % day.month)
        return d / ("%s_%s_%s" % (day.strftime("%Y%m%d"), file_sha[:8], _safe_name(name)))

    def _store_original(self, dest: Path, raw: Optional[bytes], src_path: Optional[str]) -> None:
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        for p in (dest.parent, dest.parent.parent, dest.parent.parent.parent):
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass
        if src_path is not None:
            shutil.copy2(src_path, dest)
        else:
            dest.write_bytes(raw or b"")
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass

    def _record_payment(self, doc_id: int, ext: Extraction, category: str) -> Tuple[str, List[str]]:
        notes: List[str] = []
        if ext.event_dt:
            ts = ext.event_dt if len(ext.event_dt) > 10 else ext.event_dt + " 00:00"
            tk = ext.time_known
        else:
            ts = self.today.isoformat() + " 00:00"
            tk = False
            notes.append("결제 일시를 찾지 못해 오늘 날짜로 기록")
        merchant = ext.merchant or "(가맹점 미상)"
        res = self.db.add_payment(
            doc_id, ts[:16], tk, merchant, int(ext.amount or 0), ext.is_cancel, ext.installment,
            category, ext.card, self.cfg.dedupe_minutes)
        msgs = {
            "duplicate": "같은 가맹점·금액의 결제가 %d분 안에 이미 있어 중복으로 처리(가계부에 추가 안 함)" % self.cfg.dedupe_minutes,
            "canceled_match": "취소 내역을 기존 승인과 짝지어 가계부에서 상쇄함",
            "unmatched_cancel": "짝이 되는 승인 내역을 찾지 못한 취소 — 환불로 별도 기록",
        }
        if res.status in msgs:
            notes.append(msgs[res.status])
        return res.status, notes

    def _record_refund(self, doc_id: int, ext: Extraction, category: str
                       ) -> Tuple[Optional[dt.date], Optional[int], str]:
        days, source = None, ""
        if ext.refund_days is not None:
            if ext.refund_days == 0:
                return None, 0, "영수증에 교환/환불 불가 문구가 있어 환불 기한은 추적하지 않음"
            days, source = ext.refund_days, "text"
        elif ext.online and category not in NO_REFUND_CATEGORIES:
            days, source = self.cfg.default_refund_days, "default_online"
        if not days:
            return None, None, ""
        try:
            purchase = dt.date.fromisoformat((ext.event_dt or self.today.isoformat())[:10])
        except ValueError:
            purchase = self.today
        deadline = purchase + dt.timedelta(days=days)
        note = ("영수증 문구 기준 %d일" % days) if source == "text" else "온라인 구매 청약철회 일반 기준 %d일" % days
        self.db.add_refund(doc_id, ext.merchant, ext.amount, purchase.isoformat(), days,
                           deadline.isoformat(), source, note)
        return deadline, days, "환불/반품 기한 %s 기록 (%s)" % (deadline.isoformat(), note)

    # -- 인박스 ---------------------------------------------------------------
    def inbox(self, folder: str, recursive: bool = False, min_age: float = 3.0,
              force_type: Optional[str] = None, use_llm: bool = True) -> InboxReport:
        root = Path(folder).expanduser()
        if not root.is_dir():
            raise AcquireError("폴더가 없습니다: %s" % folder)
        report = InboxReport()
        files = sorted((root.rglob("*") if recursive else root.iterdir()), key=lambda p: str(p))
        now = self.now.timestamp() if self._now else __import__("time").time()
        for p in files:
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            nm = p.name
            if nm.startswith(".") or nm.endswith(TEMP_SUFFIXES) or p.suffix.lower() not in SUPPORTED_EXT - {""}:
                report.ignored += 1
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if min_age > 0 and (now - st.st_mtime) < min_age:
                report.skipped_young += 1
                continue
            key = str(p.resolve())
            seen = self.db.seen_get(key)
            if seen is not None and seen["size"] == st.st_size and seen["mtime_ns"] == st.st_mtime_ns:
                report.skipped_seen += 1
                continue
            sha = sha256_file(str(p))
            results = self.add_file(str(p), force_type, use_llm, file_sha=sha)
            report.results.extend(results)
            if all(r.status in ("added", "duplicate") for r in results):
                self.db.seen_set(key, st.st_size, st.st_mtime_ns, sha)
        return report

    # -- 조회 ---------------------------------------------------------------
    def due(self, days: int = 14, overdue_days: int = DEFAULT_OVERDUE_DAYS) -> Dict[str, list]:
        today = self.today
        start = (today - dt.timedelta(days=overdue_days)).isoformat()
        end = (today + dt.timedelta(days=days)).isoformat()
        docs = self.db.docs_due_between(start, end)
        refunds = self.db.refunds_between(today.isoformat(), end)
        return {"docs": list(docs), "refunds": list(refunds)}

    # -- 기계용 JSON (contract.py 스키마 v1) -------------------------------------
    def due_payload(self, days: int = 14, overdue_days: int = DEFAULT_OVERDUE_DAYS) -> Dict[str, Any]:
        """due 명령과 같은 기간의 항목(문서 + 환불)을 due 순으로 돌려준다."""
        today = self.today
        start = (today - dt.timedelta(days=overdue_days)).isoformat()
        end = (today + dt.timedelta(days=days)).isoformat()
        items = [self._bill_item(r) for r in self.db.docs_due_between(start, end)]
        items += [self._refund_item(r) for r in self.db.refunds_between(today.isoformat(), end)]
        return self._payload(items)

    def refunds_payload(self, days: int = 30) -> Dict[str, Any]:
        """refunds 명령과 같은 기간(오늘부터 N일 이내)의 환불·반품 기한만 돌려준다."""
        today = self.today
        end = (today + dt.timedelta(days=days)).isoformat()
        return self._payload([self._refund_item(r) for r in self.db.refunds_between(today.isoformat(), end)])

    def _payload(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        obj = {"version": contract.SCHEMA_VERSION, "generated_for": self.today.isoformat(),
               "items": sorted(items, key=contract.sort_key)}
        return contract.validate(obj)

    def _bill_item(self, r: Any) -> Dict[str, Any]:
        due = dt.date.fromisoformat(r["due_date"])
        label = SUBTYPE_LABEL.get(r["subtype"], r["subtype"] or DOC_TYPES.get(r["doc_type"], "서류"))
        return {
            "kind": contract.KIND_BILL,
            "title": mask_text(label),
            "merchant": mask_text(r["issuer"] or r["merchant"] or ""),
            "amount": int(r["amount"]) if r["amount"] is not None else None,
            "due": due.isoformat(),
            "days_left": (due - self.today).days,
            "auto_debit": bool(r["auto_debit"]),
            "doc_id": int(r["id"]),
        }

    def _refund_item(self, r: Any) -> Dict[str, Any]:
        deadline = dt.date.fromisoformat(r["deadline"])
        return {
            "kind": contract.KIND_REFUND,
            "title": "환불·반품 기한",
            "merchant": mask_text(r["merchant"] or ""),
            "amount": int(r["amount"]) if r["amount"] is not None else None,
            "due": deadline.isoformat(),
            "days_left": (deadline - self.today).days,
            "auto_debit": False,
            "doc_id": int(r["doc_id"]),
        }

    def ledger(self, month: str) -> Dict[str, Any]:
        rows = self.db.payments_for_month(month)
        out_rows = []
        totals: Dict[str, List[int]] = {}
        grand, count = 0, 0
        for r in rows:
            counted, signed = False, 0
            if r["kind"] == "approve" and r["status"] == "active":
                counted, signed = True, r["amount"]
            elif r["kind"] == "cancel" and r["matched_to"] is None:
                counted, signed = True, -r["amount"]
            d = dict(r)
            d["counted"], d["signed"] = counted, signed
            out_rows.append(d)
            if counted:
                cat = r["category"] or "기타"
                t = totals.setdefault(cat, [0, 0])
                t[0] += signed
                t[1] += 1
                grand += signed
                count += 1
        ordered = sorted(totals.items(), key=lambda kv: -kv[1][0])
        return {"month": month, "rows": out_rows, "totals": ordered, "total": grand, "count": count}

    def find(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        out = []
        terms = [t for t in re.sub(r"\s+", " ", query).split() if t]
        for r in self.db.search(query, limit):
            text = r["text"] or ""
            snippet = ""
            for term in terms:
                i = text.find(term)
                if i >= 0:
                    a, b = max(0, i - 25), min(len(text), i + len(term) + 35)
                    snippet = text[a:b].replace("\n", " ")
                    break
            out.append({"id": r["id"], "summary": r["summary"], "verdict": r["verdict"],
                        "doc_type": r["doc_type"], "added_at": r["added_at"],
                        "snippet": mask_text(snippet)})
        return out

    def show(self, doc_id: int) -> Optional[Dict[str, Any]]:
        r = self.db.get_document(doc_id)
        if r is None:
            return None
        d = dict(r)
        d["reasons"] = json.loads(d.get("reasons") or "[]")
        d["customer_no_masked"] = mask_id(d.get("customer_no") or "")
        d["refunds"] = [dict(x) for x in self.db.refunds_for_doc(doc_id)]
        return d

    def mark_done(self, doc_id: int, done: bool = True) -> bool:
        ok = self.db.mark_done(doc_id, done)
        if ok:
            r = self.db.get_document(doc_id)
            ext = Extraction.from_dict({
                "doc_type": r["doc_type"], "subtype": r["subtype"] or "", "issuer": r["issuer"] or "",
                "merchant": r["merchant"] or "", "amount": r["amount"], "due_date": r["due_date"],
                "billing_month": r["billing_month"], "auto_debit": bool(r["auto_debit"]),
                "auto_debit_day": r["auto_debit_day"], "confidence": r["confidence"] or 0.0,
                "flags": (json.loads(r["extra"] or "{}").get("flags") or [])})
            adv = advise(ext, self.today, done=done)
            if ext.doc_type in ("bill", "notice"):
                self.db.update_document(doc_id, todo=adv.todo, verdict=adv.verdict,
                                        reasons=json.dumps(adv.reasons, ensure_ascii=False),
                                        retention_years=adv.retention_years)
        return ok

    # -- 내보내기 -------------------------------------------------------------
    def export_ledger_csv(self, path: str, month: Optional[str] = None) -> int:
        rows = self.db.payments_for_month(month)
        header = ["일시", "가맹점", "구분", "금액", "카테고리", "할부", "상태", "집계반영", "카드", "문서ID"]
        buf = io.StringIO(newline="")
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(header)
        status_ko = {"active": "정상", "canceled": "취소됨", "cancel": "취소"}
        n = 0
        for r in rows:
            counted = (r["kind"] == "approve" and r["status"] == "active") or (
                r["kind"] == "cancel" and r["matched_to"] is None)
            amt = r["amount"] if r["kind"] == "approve" else -r["amount"]
            inst = "일시불" if not r["installment"] else "%d개월" % r["installment"]
            status = status_ko.get(r["status"], r["status"])
            if r["kind"] == "cancel":
                status = "취소(매칭)" if r["matched_to"] else "취소(미매칭)"
            ts = r["ts"] if r["time_known"] else r["ts"][:10]
            w.writerow([ts, csv_safe(r["merchant"]), "승인" if r["kind"] == "approve" else "취소", amt,
                        csv_safe(r["category"]), inst, status, "Y" if counted else "N",
                        csv_safe(r["card"]), r["doc_id"] or ""])
            n += 1
        _write_csv(path, buf.getvalue())
        return n

    def export_docs_csv(self, path: str) -> int:
        header = ["ID", "추가일", "종류", "세부", "기관/가맹점", "금액", "납부기한", "청구월", "자동이체",
                  "판정", "요약", "할일", "고객번호(마스킹)", "완료"]
        buf = io.StringIO(newline="")
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(header)
        n = 0
        for r in reversed(self.db.list_documents(100000)):
            w.writerow([r["id"], (r["added_at"] or "")[:10], DOC_TYPES.get(r["doc_type"], r["doc_type"]),
                        csv_safe(r["subtype"]), csv_safe(r["merchant"] or r["issuer"]),
                        r["amount"] if r["amount"] is not None else "", r["due_date"] or "",
                        r["billing_month"] or "", "Y" if r["auto_debit"] else "",
                        r["verdict"], csv_safe(mask_text(r["summary"] or "")),
                        csv_safe(mask_text(r["todo"] or "")), mask_id(r["customer_no"] or ""),
                        "Y" if r["done"] else ""])
            n += 1
        _write_csv(path, buf.getvalue())
        return n

    def build_ics(self, days: Optional[int] = None, include_refunds: bool = True,
                  include_done: bool = False) -> Tuple[str, int]:
        today = self.today
        if days is None:
            start, end = "0000-01-01", "9999-12-31"
            start = (today - dt.timedelta(days=DEFAULT_OVERDUE_DAYS)).isoformat()
        else:
            start = today.isoformat()
            end = (today + dt.timedelta(days=days)).isoformat()
        events: List[icsmod.VEvent] = []
        for r in self.db.docs_due_between(start, end, include_done):
            events.append(self._doc_event(r))
        if include_refunds:
            for r in self.db.refunds_between(start, end, include_done):
                d = dt.date.fromisoformat(r["deadline"])
                desc = "구매일 %s + %d일 기준. %s" % (r["purchase_date"], r["policy_days"], REFUND_DISCLAIMER)
                events.append(icsmod.VEvent(
                    uid="pb-refund-%d-%s@paperbutler.local" % (r["id"], r["deadline"]),
                    summary=mask_text("[환불기한] %s %s" % (r["merchant"] or "상점", won(r["amount"]))),
                    start=d, description=mask_text(desc), categories="환불기한"))
        cal = icsmod.build_calendar(events, now=self._utcnow(), name="서류 비서")
        return cal, len(events)

    def _utcnow(self) -> dt.datetime:
        if self._now is not None:
            n = self._now
            return n if n.tzinfo else n.replace(tzinfo=icsmod.KST)
        return dt.datetime.now(dt.timezone.utc)

    def _doc_event(self, r: Any) -> "icsmod.VEvent":
        label = SUBTYPE_LABEL.get(r["subtype"], r["subtype"] or DOC_TYPES.get(r["doc_type"], "서류"))
        who = r["issuer"] or r["merchant"] or ""
        prefix = "[자동이체]" if r["auto_debit"] else ("[납부]" if r["doc_type"] == "bill" else "[기한]")
        title = " ".join(x for x in (prefix, who, label, won(r["amount"]) if r["amount"] is not None else "") if x)
        desc_lines = [r["summary"] or "", "할 일: " + (r["todo"] or "")]
        if r["customer_no"]:
            desc_lines.append("고객번호: " + mask_id(r["customer_no"]))
        desc_lines.append("서류 비서 #%d" % r["id"])
        return icsmod.VEvent(
            uid="pb-doc-%d-%s@paperbutler.local" % (r["id"], r["sha256"][:12]),
            summary=mask_text(title), start=dt.date.fromisoformat(r["due_date"]),
            description=mask_text("\n".join(desc_lines)), categories="납부기한")

    def write_ics(self, path: str, days: Optional[int] = None, include_refunds: bool = True,
                  include_done: bool = False) -> int:
        cal, n = self.build_ics(days, include_refunds, include_done)
        with open(path, "wb") as f:
            f.write(cal.encode("utf-8"))
        return n


def csv_safe(v: Any) -> str:
    """엑셀 수식 주입 방지 + 마스킹."""
    s = mask_text("" if v is None else str(v))
    if s and s[0] in "=+-@\t\r":
        s = "'" + s
    return s


def _write_csv(path: str, content: str) -> None:
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf" + content.encode("utf-8"))
