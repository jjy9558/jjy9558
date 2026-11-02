"""한 줄 요약 / 할 일 / 판정(버려도 됨·보관·처리 필요) 생성."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional

from .extract import (
    PUBLIC_INSURANCE_SUBTYPES, SUBTYPE_LABEL, TAX_SUBTYPES, Extraction,
)
from .masking import mask_text
from .parsing import fmt_md, won

DISCARD = "버려도 됨"
KEEP = "보관"
ACTION = "처리 필요"

REFUND_DISCLAIMER = (
    "※ 일반적인 가이드라인(온라인 구매 청약철회 통상 7일)이며 법률 자문이 아닙니다. "
    "실제 기한은 상품 수령일·판매자 정책·상품 종류(주문제작·식품 등 제외 가능)에 따라 다를 수 있으니 "
    "판매처 약관을 확인하세요."
)

RETENTION_TAX_YEARS = 5


@dataclass
class Advice:
    summary: str = ""
    todo: str = ""
    verdict: str = KEEP
    reasons: List[str] = field(default_factory=list)
    retention_years: Optional[int] = None

    @property
    def verdict_display(self) -> str:
        if self.verdict == KEEP and self.retention_years:
            return "%s %d년" % (KEEP, self.retention_years)
        return self.verdict


def _d(s: Optional[str]) -> Optional[dt.date]:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _install(n: int) -> str:
    return "%d개월 할부" % n if n >= 2 else "일시불"


def _bill(ext: Extraction, today: dt.date, done: bool) -> Advice:
    label = SUBTYPE_LABEL.get(ext.subtype, "고지서")
    due = _d(ext.due_date)
    parts = ["%s %s" % (ext.issuer, label) if ext.issuer else label]
    if ext.amount is not None:
        parts[0] += " " + won(ext.amount)
    if ext.billing_month:
        parts[0] += " (%s분)" % ext.billing_month
    if due:
        parts.append("납부기한 " + fmt_md(due, today))
    if ext.auto_debit:
        parts.append("자동이체")
    summary = " · ".join(parts)

    # 할 일
    if due:
        md = fmt_md(due, today)
        delta = (due - today).days
        base = "%s까지 %s 납부" % (md, won(ext.amount) if ext.amount is not None else "(금액 확인)")
        if ext.auto_debit:
            if delta < 0:
                todo = "%s — 자동이체지만 기한이 %d일 지남, 출금 여부만 확인" % (base, -delta)
            else:
                todo = base + " — 자동이체라 할 일 없음"
        elif done:
            todo = base + " — 납부 완료로 표시됨"
        elif delta < 0:
            todo = "%s — 기한이 %d일 지남, 바로 납부/확인 필요" % (base, -delta)
        elif delta == 0:
            todo = base + " — 오늘이 마지막 날"
        elif delta <= 3:
            todo = "%s — D-%d, 서두르세요" % (base, delta)
        else:
            todo = "%s — 직접 납부 필요 (D-%d)" % (base, delta)
    elif ext.auto_debit and ext.auto_debit_day:
        todo = "매월 %d일 자동이체 — 할 일 없음" % ext.auto_debit_day
    elif ext.auto_debit:
        todo = "자동이체 — 할 일 없음"
    else:
        todo = "납부기한을 찾지 못함 — 원본에서 기한 확인 필요"

    # 판정
    reasons: List[str] = []
    retention = None
    is_tax = ext.subtype in TAX_SUBTYPES or ext.subtype in PUBLIC_INSURANCE_SUBTYPES or ext.subtype == "과태료"
    unpaid = not done and not ext.auto_debit and (due is not None or ext.amount is not None)
    if ext.confidence < 0.5:
        return Advice(summary, todo, KEEP, ["자동 분류가 불확실해 안전하게 보관"], None)
    if is_tax:
        retention = RETENTION_TAX_YEARS
        if unpaid:
            verdict = ACTION
            reasons.append("납부가 끝나지 않은 %s 고지서" % label)
            if ext.subtype == "과태료":
                reasons.append("기한이 지나면 가산금이 붙을 수 있음")
            reasons.append("납부 후 영수증/납부확인은 %d년 보관 권장" % retention)
        else:
            verdict = KEEP
            reasons.append("세금·보험 관련 납부 증빙: %d년 보관 권장" % retention)
            if ext.auto_debit:
                reasons.append("자동이체로 납부되지만 증빙은 남겨 두는 편이 안전")
    else:
        if unpaid:
            verdict = ACTION
            reasons.append("납부기한이 있는 고지서" if due else "납부할 금액이 있는 고지서")
            reasons.append("납부 후에는 앱/홈페이지에서 재조회할 수 있어 폐기 가능")
        else:
            verdict = DISCARD
            if ext.auto_debit:
                reasons.append("자동이체로 납부됨")
            else:
                reasons.append("납부 완료로 표시됨")
            reasons.append("납부내역은 기관 앱/홈페이지에서 다시 조회 가능 (출금 확인 후 폐기)")
    if ext.subtype == "카드대금":
        reasons.append("카드사 앱에서 명세서 재확인 가능")
    return Advice(summary, todo, verdict, reasons, retention)


def _card(ext: Extraction, today: dt.date, category: str, pay_status: str) -> Advice:
    when = ""
    if ext.event_dt:
        d = _d(ext.event_dt)
        when = fmt_md(d, today) if d else ""
        if ext.time_known and len(ext.event_dt) >= 16:
            when += " " + ext.event_dt[11:16]
    kind = "취소" if ext.is_cancel else "승인"
    parts = ["%s %s %s %s" % (ext.merchant or "가맹점 미상", won(ext.amount), _install(ext.installment), kind)]
    if when:
        parts[0] += " (%s)" % when
    if category:
        parts.append(category)
    summary = " · ".join(parts)
    if pay_status == "duplicate":
        todo = "이미 기록된 결제와 같아 중복으로 보고 추가 기록하지 않음"
    elif ext.is_cancel:
        todo = "취소 내역을 가계부에 반영 — 환불 입금은 카드사에 따라 영업일 기준 며칠 걸릴 수 있음"
    else:
        todo = "할 일 없음 — 가계부에 기록됨"
    reasons = ["결제 알림은 가계부(원장)에 기록되므로 문자 원본은 필요 없음"]
    if ext.confidence < 0.5:
        return Advice(summary, todo, KEEP, ["자동 분류가 불확실해 안전하게 보관"], None)
    return Advice(summary, todo, DISCARD, reasons, None)


def _receipt(ext: Extraction, today: dt.date, refund_deadline: Optional[dt.date],
             refund_days: Optional[int]) -> Advice:
    when = ""
    d = _d(ext.event_dt)
    if d:
        when = " (%s)" % fmt_md(d, today)
    summary = "%s 영수증 %s%s" % (ext.merchant or "상호 미상", won(ext.amount), when)
    if ext.online:
        summary += " · 온라인"
    reasons: List[str] = []
    retention = None
    if refund_deadline is not None:
        md = fmt_md(refund_deadline, today)
        delta = (refund_deadline - today).days
        if delta >= 0:
            todo = "%s까지 환불/반품 가능 (D-%d, 구매일+%d일 기준) — 마음에 안 들면 그 전에 결정" % (
                md, delta, refund_days or 0)
        else:
            todo = "환불/반품 기준 기한(%s)이 지남" % md
    else:
        todo = "할 일 없음 — 가계부에 기록됨"
    has_refund = refund_deadline is not None and refund_deadline >= today
    if ext.confidence < 0.5:
        return Advice(summary, todo, KEEP, ["자동 분류가 불확실해 안전하게 보관"], None)
    if has_refund:
        verdict = KEEP
        reasons.append("환불/반품 가능 기한(%s)까지는 보관" % fmt_md(refund_deadline, today))
        reasons.append("기한이 지나면 폐기 가능")
    elif "tax_evidence" in ext.flags:
        verdict, retention = KEEP, 5
        reasons.append("지출 증빙/경비 처리용일 수 있어 5년 보관 권장")
    elif "warranty" in ext.flags:
        verdict = KEEP
        reasons.append("보증/A·S 관련 문구가 있어 보증기간 동안 보관")
    elif ext.amount is not None and ext.amount >= 500_000:
        verdict, retention = KEEP, 1
        reasons.append("고액 결제: A/S·분쟁 대비 1년 보관 권장")
    else:
        verdict = DISCARD
        reasons.append("결제 내역은 가계부에 기록됨 (카드사 앱에서도 조회 가능)")
    return Advice(summary, todo, verdict, reasons, retention)


def _notice(ext: Extraction, today: dt.date, done: bool) -> Advice:
    due = _d(ext.due_date)
    title = "%s %s 안내" % (ext.issuer or "발신처 미상", ext.subtype if ext.subtype and ext.subtype not in ("기타", "관공서") else "")
    title = " ".join(title.split())
    parts = [title]
    if ext.amount is not None:
        parts[0] += " " + won(ext.amount)
    if due:
        parts.append("기한 " + fmt_md(due, today))
    summary = " · ".join(parts)
    urgent = "urgent" in ext.flags or "legal" in ext.flags
    if due:
        delta = (due - today).days
        when = "%s까지" % fmt_md(due, today)
        if delta < 0:
            todo = "%s 처리 필요했음 — 기한이 %d일 지남, 바로 확인" % (when, -delta)
        else:
            todo = "%s 확인·처리 (D-%d)" % (when, delta)
            if ext.amount is not None:
                todo = "%s %s 납부 등 확인·처리 (D-%d)" % (when, won(ext.amount), delta)
    elif urgent:
        todo = "내용 확인 후 필요한 회신/납부/제출 진행"
    else:
        todo = "내용만 확인 — 별도 할 일 없음"
    if ext.confidence < 0.5:
        return Advice(summary, todo, KEEP, ["자동 분류가 불확실해 안전하게 보관"], None)
    reasons: List[str] = []
    retention = None
    if (due or urgent) and not done:
        verdict = ACTION
        reasons.append("기한 또는 조치 요구 문구가 있음")
        if "legal" in ext.flags:
            reasons.append("법적 조치/독촉 관련 문구 — 원본 보관 및 신속 대응 권장")
            retention = 5
    else:
        verdict, retention = KEEP, 1
        if ext.subtype == "보험":
            retention = 3
            reasons.append("보험 계약·약관 관련 안내: 계약 종료 후까지 보관 권장")
        elif ext.subtype == "은행":
            reasons.append("금융 계약 변경/안내: 1년 보관 후 불필요하면 폐기")
        else:
            reasons.append("기관 안내문: 1년 보관 후 불필요하면 폐기")
    return Advice(summary, todo, verdict, reasons, retention)


def _parcel(ext: Extraction) -> Advice:
    state = ext.subtype or "배송중"
    summary = "%s 택배 %s" % (ext.issuer or "택배사 미상", state)
    if state == "수령필요":
        return Advice(summary, "택배 수령/재배송 요청 필요", ACTION, ["부재·반송·보관 안내가 있음"], None)
    if state == "배송완료":
        return Advice(summary, "문 앞/경비실에 온 택배 수령 확인", DISCARD,
                      ["배송 안내는 물건을 받으면 필요 없음"], None)
    return Advice(summary, "도착 대기", DISCARD, ["배송 안내: 물건 수령만 확인하면 됨"], None)


def _ad(ext: Extraction) -> Advice:
    summary = "%s 광고성 안내" % (ext.issuer or "광고")
    if "important" in ext.flags and ext.confidence < 0.9:
        return Advice(summary, "광고처럼 보이나 납부/계약 관련 단어가 있어 한 번 확인", KEEP,
                      ["광고로 분류했지만 중요 단어(납부·계약 등) 포함 — 안전하게 보관"], None)
    return Advice(summary, "할 일 없음", DISCARD,
                  ["광고성 우편/문자", "할인·이벤트 안내일 뿐 처리할 서류가 아님"], None)


def advise(ext: Extraction, today: Optional[dt.date] = None, *, category: str = "",
           pay_status: str = "", refund_deadline: Optional[dt.date] = None,
           refund_days: Optional[int] = None, done: bool = False) -> Advice:
    today = today or dt.date.today()
    t = ext.doc_type
    if t == "bill":
        a = _bill(ext, today, done)
    elif t == "card_sms":
        a = _card(ext, today, category, pay_status)
    elif t == "receipt":
        a = _receipt(ext, today, refund_deadline, refund_days)
    elif t == "notice":
        a = _notice(ext, today, done)
    elif t == "parcel":
        a = _parcel(ext)
    elif t == "ad":
        a = _ad(ext)
    else:
        a = Advice("분류하지 못한 문서", "직접 확인 필요", KEEP,
                   ["자동 분류가 불확실해 안전하게 보관"], None)
    a.summary = mask_text(a.summary)
    a.todo = mask_text(a.todo)
    a.reasons = [mask_text(r) for r in a.reasons]
    return a
