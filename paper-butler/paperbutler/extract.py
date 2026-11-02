"""규칙 기반 분류기 + 추출기 (한국어 서류).

analyze(text) -> Extraction.  LLM 없이도 동작하며, 확신이 낮거나 필드가 비면
(needs_llm) 선택적으로 llm 모듈이 빈칸을 채운다. 규칙이 확신하면 규칙이 이긴다.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .categories import is_online_merchant
from .parsing import (
    DateTok, amount_after, find_billing_month, find_customer_no, find_datetime,
    iter_amounts, normalize_text, resolve_date, scan_dates,
)

DOC_TYPES: Dict[str, str] = {
    "bill": "고지서",
    "card_sms": "결제문자",
    "receipt": "영수증",
    "notice": "안내문",
    "parcel": "택배",
    "ad": "광고",
    "unknown": "기타",
}

# CLI --type 별칭
TYPE_ALIASES = {
    "auto": None, "bill": "bill", "고지서": "bill", "card": "card_sms", "card_sms": "card_sms",
    "sms": "card_sms", "결제": "card_sms", "결제문자": "card_sms", "receipt": "receipt", "영수증": "receipt",
    "notice": "notice", "안내문": "notice", "관공서": "notice", "parcel": "parcel", "택배": "parcel",
    "ad": "ad", "광고": "ad",
}

SUBTYPE_LABEL = {
    "전기": "전기요금", "가스": "가스요금", "수도": "수도요금", "통신": "통신요금", "관리비": "관리비",
    "건강보험": "건강보험료", "국민연금": "국민연금보험료", "자동차세": "자동차세", "재산세": "재산세",
    "주민세": "주민세", "과태료": "과태료", "카드대금": "카드대금", "기타": "고지서",
}

TAX_SUBTYPES = ("자동차세", "재산세", "주민세")
PUBLIC_INSURANCE_SUBTYPES = ("건강보험", "국민연금")


@dataclass
class Extraction:
    doc_type: str = "unknown"
    subtype: str = ""
    issuer: str = ""                 # 기관 (고지서/안내문 발행처, 카드사)
    merchant: str = ""               # 가맹점 (결제/영수증)
    amount: Optional[int] = None     # 원
    due_date: Optional[str] = None   # YYYY-MM-DD 납부기한/기한
    event_dt: Optional[str] = None   # 결제·구매 일시 'YYYY-MM-DD HH:MM' 또는 'YYYY-MM-DD'
    time_known: bool = False
    doc_date: Optional[str] = None   # 발행일/고지일
    billing_month: Optional[str] = None  # YYYY-MM
    customer_no: str = ""            # 저장은 원본, 출력은 마스킹
    auto_debit: bool = False
    auto_debit_day: Optional[int] = None
    installment: int = 0             # 할부 개월(0=일시불)
    is_cancel: bool = False
    card: str = ""
    online: bool = False
    refund_days: Optional[int] = None  # 영수증 문구에서 읽은 교환/환불 일수 (0=불가)
    confidence: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)
    sources: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)  # urgent/legal/warranty/tax_evidence/important

    @property
    def party(self) -> str:
        return self.merchant or self.issuer

    def required_fields(self) -> List[str]:
        if self.doc_type == "bill":
            req = ["issuer", "amount"]
            if not self.auto_debit_day:
                req.append("due_date")
            return req
        if self.doc_type == "card_sms":
            return ["merchant", "amount", "event_dt"]
        if self.doc_type == "receipt":
            return ["merchant", "amount"]
        if self.doc_type == "unknown":
            return ["doc_type"]
        return []

    def missing_fields(self) -> List[str]:
        out = []
        for f in self.required_fields():
            if f == "doc_type":
                out.append(f)
            elif not getattr(self, f):
                out.append(f)
        return out

    def needs_llm(self, threshold: float = 0.7) -> bool:
        return bool(self.missing_fields()) or self.confidence < threshold

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Extraction":
        names = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in names})


# --------------------------------------------------------------------------
# 점수표
# --------------------------------------------------------------------------
def _c(p: str) -> "re.Pattern[str]":
    return re.compile(p, re.I)


def _sum(t: str, pats: List[Tuple["re.Pattern[str]", float]]) -> float:
    return sum(w for rx, w in pats if rx.search(t))


BILL_SUBTYPES: Dict[str, List[Tuple["re.Pattern[str]", float]]] = {
    "전기": [(_c(r"한국전력|한전|KEPCO"), 4), (_c(r"전기\s*요금"), 4), (_c(r"전기\s*사용|전력\s*사용"), 2),
            (_c(r"kWh"), 3), (_c(r"전기료"), 1)],
    "가스": [(_c(r"도시\s*가스|가스\s*요금|가스\s*사용"), 4),
            (_c(r"서울도시가스|코원에너지|삼천리|예스코|대한도시가스|경동도시가스|인천도시가스|한국가스공사"), 3),
            (_c(r"m3"), 0.5)],
    "수도": [(_c(r"수도\s*요금|상수도|하수도|수도\s*사용|수도사업|상하수도"), 4), (_c(r"물\s*이용\s*부담금"), 2),
            (_c(r"m3|톤"), 0.5)],
    "통신": [(_c(r"(?<![A-Za-z])KT(?![A-Za-z&])|SK\s*텔레콤|SKT|LG\s*U\+|LG\s*유플러스|유플러스|알뜰폰|SK\s*브로드밴드"), 3),
            (_c(r"통신\s*요금|이동통신|휴대폰\s*요금|인터넷\s*요금|모바일\s*요금|IPTV|인터넷\s*TV|데이터\s*사용"), 3),
            (_c(r"기본료|통화료|부가서비스|단말기"), 1)],
    "관리비": [(_c(r"관리비"), 5), (_c(r"관리\s*사무소|입주자\s*대표회의|장기\s*수선|수선\s*충당"), 3),
             (_c(r"공용\s*전기|공용\s*수도|승강기|동\s*호"), 1)],
    "건강보험": [(_c(r"국민\s*건강\s*보험|건강\s*보험료?|장기\s*요양\s*보험료?"), 5), (_c(r"건강보험공단"), 3)],
    "국민연금": [(_c(r"국민\s*연금"), 5), (_c(r"연금\s*보험료"), 3)],
    "자동차세": [(_c(r"자동차\s*세"), 5), (_c(r"자동차\s*등록\s*번호|차량\s*번호|배기량"), 1)],
    "재산세": [(_c(r"재산세"), 5), (_c(r"지방교육세|도시지역분|주택분|토지분|과세\s*표준"), 2)],
    "주민세": [(_c(r"주민세"), 5), (_c(r"개인균등분|사업소분"), 2)],
    "과태료": [(_c(r"과태료|범칙금|과징금"), 5), (_c(r"위반\s*(?:일|장소|내용)|주정차|속도\s*위반|의견\s*진술"), 2)],
    "카드대금": [(_c(r"결제\s*예정\s*(?:금액|일)|이용\s*대금\s*명세서|카드\s*대금|청구\s*예정"), 4), (_c(r"카드"), 1)],
}

BILL_EVIDENCE: List[Tuple["re.Pattern[str]", float]] = [
    (_c(r"납부\s*기한|납기\s*일|납기\s*내|납부\s*기일|납입\s*기일|납부\s*마감"), 3),
    (_c(r"청구\s*금액|납부\s*금액|납부\s*하실|납부\s*할\s*(?:금액|보험료|세액)|고지\s*금액|납입\s*금액|결제\s*예정\s*금액|부과\s*금액"), 3),
    (_c(r"고지서|청구서|납부서|납부\s*고지|납부\s*안내|납부\s*바랍니다|납부하여\s*주|명세서"), 2),
    (_c(r"자동\s*이체|자동\s*납부"), 1),
    (_c(r"미납|연체|가산금"), 1),
    (_c(r"청구|고지"), 2),
    (_c(r"요금|보험료|세액|부과"), 1),
]

AD_PATTERNS: List[Tuple["re.Pattern[str]", float]] = [
    (_c(r"\(광고\)|\[광고\]|\(광고성\)|\[광고성\]|광고\s*문자"), 5),
    (_c(r"수신\s*거부|080-\d"), 3),
    (_c(r"이벤트"), 2), (_c(r"프로모션"), 2), (_c(r"할인"), 2), (_c(r"특가"), 2), (_c(r"세일"), 2),
    (_c(r"쿠폰"), 2), (_c(r"증정|사은품|경품"), 2), (_c(r"최저가"), 2), (_c(r"혜택"), 1), (_c(r"무료"), 1),
    (_c(r"분양|모델하우스"), 3), (_c(r"대출\s*(?:상담|한도|금리)|투자\s*상담"), 3),
    (_c(r"오픈|개업|회원\s*모집|모집"), 1), (_c(r"지금\s*바로|클릭|바로\s*가기|신청하세요|문의\s*주세요|상담\s*예약"), 1),
    (_c(r"https?://|bit\.ly"), 1),
]

RECEIPT_PATTERNS: List[Tuple["re.Pattern[str]", float]] = [
    (_c(r"영수증"), 5), (_c(r"매출\s*전표"), 5), (_c(r"거래\s*명세"), 3),
    (_c(r"사업자\s*(?:등록)?\s*번호"), 3), (_c(r"공급\s*가액|부가세|부가\s*가치세|과세\s*물품"), 3),
    (_c(r"합\s*계"), 3), (_c(r"단가"), 1), (_c(r"수량"), 1), (_c(r"품명|품목|상품명"), 1),
    (_c(r"승인\s*번호"), 2), (_c(r"주문\s*번호"), 2), (_c(r"주문\s*(?:이\s*)?(?:완료|내역|상품)"), 2),
    (_c(r"현금\s*영수"), 2), (_c(r"대표자|대표\s*이사"), 1), (_c(r"배송비|배송지"), 1),
    (_c(r"교환|환불"), 1), (_c(r"카드\s*결제|현금\s*결제|결제\s*수단|결제\s*방법"), 1),
    (_c(r"결제\s*금액|주문\s*일시|구매\s*일시"), 1), (_c(r"조제료|진료비|약품비"), 1),
]

PARCEL_PATTERNS: List[Tuple["re.Pattern[str]", float]] = [
    (_c(r"택배|운송장|송장\s*번호|등기\s*번호|우편물|등기\s*우편"), 3),
    (_c(r"배송\s*(?:중|완료|예정|출발|지연)|배달\s*(?:완료|예정|중)|출고|집하|간선\s*상차|도착\s*예정"), 3),
    (_c(r"문\s*앞|경비실|무인\s*택배함|택배함|수령|부재|반송|재방문|재배송"), 2),
    (_c(r"CJ\s*대한통운|한진택배|롯데택배|로젠택배|우체국택배|로켓배송|새벽\s*배송"), 2),
]

NOTICE_ORG = [
    _c(r"은행|뱅크"), _c(r"보험|생명|화재|손해"), _c(r"증권"), _c(r"캐피탈|저축은행"), _c(r"공단"),
    _c(r"세무서|국세청"), _c(r"시청|구청|군청|도청|주민센터"), _c(r"법원|검찰|경찰"), _c(r"교육청|학교"),
    _c(r"우체국"), _c(r"병원|의료원|의원"), _c(r"위원회|협회|공사"),
]
NOTICE_DOC: List[Tuple["re.Pattern[str]", float]] = [
    (_c(r"안내문"), 2), (_c(r"통지서|통지"), 2), (_c(r"공고|공문"), 2),
    (_c(r"알려\s*드립니다|안내\s*드립니다|알려드립니다|안내드립니다"), 2), (_c(r"귀하|고객님"), 1),
    (_c(r"회신"), 1), (_c(r"이의\s*신청"), 1), (_c(r"제출"), 1), (_c(r"출석"), 1), (_c(r"갱신|만기"), 2),
    (_c(r"약관|변경"), 1), (_c(r"안내"), 1), (_c(r"독촉|최고|압류|체납|지급\s*명령|소송"), 2),
]

_PAY_WORD = _c(r"승인|취소|결제\s*(?:완료|되었|됐|알림|내역)|결제(?=\s*[\d₩])|원\s*(?:결제|사용|출금)|결제하셨|사용하셨|이용하셨")
_DT_FULL = _c(r"(?:\d{1,2}[./-]\d{1,2}|20\d{2}[./-]\d{1,2}[./-]\d{1,2})\s+(?:오전|오후)?\s*\d{1,2}:\d{2}")
_CARD_WORD = _c(r"카드|체크|Pay|페이|뱅크")
_INSTALL_WORD = _c(r"일시불|\d{1,2}\s*개월|할부")
_CARD_NEG = _c(r"결제\s*예정|이용\s*대금|명세서|청구서|납부|납기")


_FOREIGN_RE = re.compile(r"(?:USD|EUR|JPY|CNY|GBP|HKD|THB|VND|\$|€|¥)\s*\d[\d,]{0,15}(?:\.\d+)?|(?<![\d,.])\d[\d,]{0,15}(?:\.\d+)?\s*(?:USD|EUR|JPY|CNY|GBP)")


def score_types(t: str) -> Dict[str, float]:
    has_krw = bool(iter_amounts(t))
    has_amt = has_krw or bool(_FOREIGN_RE.search(t))
    sc: Dict[str, float] = {}

    ad = _sum(t, AD_PATTERNS)
    sc["ad"] = min(ad, 14)

    rec = _sum(t, RECEIPT_PATTERNS)
    rec = min(rec, 16) if has_krw else min(rec, 4)
    sc["receipt"] = rec

    # 고지서
    ev = min(_sum(t, BILL_EVIDENCE), 9)
    sub_best = 0.0
    for sub, pats in BILL_SUBTYPES.items():
        sub_best = max(sub_best, _sum(t, pats))
    bill = ev + 0.5 * min(sub_best, 8)
    if ev < 3:
        bill = min(bill, 2.5)
    sc["bill"] = bill

    # 결제 문자
    card = 0.0
    if has_amt:
        if _PAY_WORD.search(t):
            card += 3
        card += 2
        if _DT_FULL.search(t):
            card += 3
        elif re.search(r"\d{1,2}:\d{2}", t):
            card += 1
        if _CARD_WORD.search(t):
            card += 2
        if _INSTALL_WORD.search(t):
            card += 2
        if re.search(r"누적|잔액|한도", t):
            card += 1
        if re.search(r"Web발신|국외발신|\[발신\]", t, re.I):
            card += 1
        n = len(t)
        if n <= 300:
            card += 1
        elif n > 600:
            card -= 3
        if t.count("\n") > 8:
            card -= 3
        if _CARD_NEG.search(t):
            card -= 6
        if re.search(r"광고", t):
            card -= 6
        if rec >= 6:
            card -= rec
        if not _PAY_WORD.search(t):
            card = min(card, 3)
    sc["card_sms"] = max(card, 0.0)

    parcel = _sum(t, PARCEL_PATTERNS)
    sc["parcel"] = min(parcel, 10)

    org = min(sum(1 for rx in NOTICE_ORG if rx.search(t)) * 2, 6)
    doc = min(_sum(t, NOTICE_DOC), 8)
    sc["notice"] = org + doc if (org and doc) else min(org + doc, 3)
    return sc


_PRIORITY = ["card_sms", "receipt", "bill", "notice", "parcel", "ad"]
_THRESHOLD = 4.0


# --------------------------------------------------------------------------
# 공통 추출기
# --------------------------------------------------------------------------
_DOC_DATE_LABEL = re.compile(r"(?:발행\s*일|고지\s*일|발송\s*일|작성\s*일|통지\s*일|발급\s*일|청구\s*일|부과\s*일|기준\s*일)\s*[:]?")
_DUE_LABEL = re.compile(
    r"납부\s*기한|납부\s*마감\s*일?|납부\s*기일|납\s*기\s*일|납기\s*내|납\s*기(?![가-힣])|납부\s*일|납입\s*기일|납입\s*일|납부\s*기간|"
    r"결제\s*예정\s*일|결제\s*일|출금\s*(?:예정)?\s*일|마감\s*일|제출\s*기한|신청\s*기한|접수\s*(?:기한|마감)|"
    r"회신\s*기한|이의\s*신청\s*기한|기한|기일|만기\s*일|만료\s*일|갱신\s*일|출석\s*일|이체\s*(?:예정)?\s*일"
)
_AFTER_DUE = re.compile(r"납기\s*후|연체|가산|체납|지연")
_UNTIL = re.compile(r"\s*\.?\s*(?:\([월화수목금토일]\))?\s*(?:[,.]\s*)?까지")
_RANGE_SEP = re.compile(r"^\s*[~\-–]\s*$")

_POS_AUTO = re.compile(r"자동\s*이체|자동\s*납부|자동\s*출금|CMS\s*출금|카드\s*자동납부", re.I)
_NEG_AUTO = re.compile(
    r"(?:자동\s*이체|자동\s*납부|자동\s*출금)\s*(?:을\s*|를\s*)?(?:신청\s*(?:하|바|해|시|안내|방법|문의|자\b)|등록\s*(?:하|바|해|시)|"
    r"가입\s*(?:하|바|해|시)|하시|하세요|미\s*신청|미신청|미등록|아님|가\s*아닌|해지|변경|신청\s*시)|"
    r"미\s*(?:신청|등록)|(?:신청|등록|가입)\s*(?:하시면|하면|시\s)"
)


def _line_bounds(t: str, pos: int) -> Tuple[int, int]:
    s = t.rfind("\n", 0, pos) + 1
    e = t.find("\n", pos)
    return s, (len(t) if e < 0 else e)


def find_doc_date(t: str, today: dt.date) -> Optional[dt.date]:
    toks = scan_dates(t)
    for lm in _DOC_DATE_LABEL.finditer(t):
        for tok in toks:
            if tok.start >= lm.end() and tok.start - lm.end() <= 6 and tok.kind == "ymd":
                return dt.date(tok.year, tok.month, tok.day)
    return None


def find_due_date(t: str, ref: dt.date) -> Optional[dt.date]:
    toks = scan_dates(t)

    def pick(tok: DateTok, idx: int) -> Optional[dt.date]:
        # '11.1 ~ 11.25' 같은 기간이면 끝 날짜
        if idx + 1 < len(toks):
            nxt = toks[idx + 1]
            if _RANGE_SEP.match(t[tok.end:nxt.start]):
                return resolve_date(nxt, ref, "future")
        return resolve_date(tok, ref, "future", tol_days=3 if tok.kind == "d" else 60)

    # 1) 라벨 뒤의 날짜
    for lm in _DUE_LABEL.finditer(t):
        before = t[max(0, lm.start() - 5):lm.start()]
        if _AFTER_DUE.search(before) or "후" in before[-2:]:
            continue
        for idx, tok in enumerate(toks):
            if tok.start < lm.end():
                continue
            gap = t[lm.end():tok.start]
            if len(gap) > 40 or gap.count("\n") > 1 or _AFTER_DUE.search(gap):
                break
            d = pick(tok, idx)
            if d:
                return d
            break
        mm = re.match(r"[\s:]*(\d{1,2})\.(\d{1,2})(?![\d.])", t[lm.end():lm.end() + 12])
        if mm:
            d = resolve_date(DateTok(0, 0, "md", None, int(mm.group(1)), int(mm.group(2))), ref, "future") \
                if 1 <= int(mm.group(1)) <= 12 and 1 <= int(mm.group(2)) <= 31 else None
            if d:
                return d
    # 2) 'OO까지'
    for idx, tok in enumerate(toks):
        if tok.kind == "d" and False:
            continue
        if _UNTIL.match(t[tok.end:tok.end + 12]):
            prev = t[max(0, tok.start - 4):tok.start]
            if re.search(r"[~\-–]\s*$", prev):  # 기간의 끝: 시작 날짜 + 기간 -> 끝 날짜
                d = resolve_date(tok, ref, "future")
            else:
                d = resolve_date(tok, ref, "future", tol_days=3 if tok.kind == "d" else 60)
            if d:
                return d
    return None


def find_auto_debit(t: str, ref: dt.date) -> Tuple[bool, Optional[int], Optional[dt.date]]:
    """(자동이체 여부, 이체일(일), 이체 예정 날짜)."""
    auto, day, date_ = False, None, None
    for line in t.split("\n"):
        if not _POS_AUTO.search(line):
            continue
        if _NEG_AUTO.search(line):
            continue
        auto = True
        toks = scan_dates(line)
        for tok in toks:
            if tok.kind == "d" and day is None:
                day = tok.day
            elif tok.kind in ("ymd", "md") and date_ is None:
                date_ = resolve_date(tok, ref, "future")
        # '자동이체 ... 25일' 같은 경우 외에 '매월 25일' 도 day 로
        if day is None:
            m = re.search(r"매월\s*(\d{1,2})\s*일", line)
            if m:
                day = int(m.group(1))
    # 별도 줄 '출금일 25일' 등은 자동이체 문맥일 때만
    if auto and day is None and date_ is None:
        m = re.search(r"(?:출금|이체)\s*(?:예정)?\s*일?\s*[:]?\s*(?:매월\s*)?(\d{1,2})\s*일", t)
        if m:
            day = int(m.group(1))
    if day is not None and not (1 <= day <= 31):
        day = None
    return auto, day, date_


# 금액 라벨
_L_ONTIME = re.compile(r"납기\s*내\s*(?:금액|요금|청구액)?")
_L_PAY = re.compile(
    r"(?:이번\s*달\s*)?(?:청구|납부|고지|결제|납입|부과)\s*(?:하실|할|예정)?\s*(?:총\s*)?(?:금액|액|요금|보험료|세액)|납부\s*세액|세액\s*합계"
)
_L_TOTAL = re.compile(r"(?:총|합계)\s*(?:청구|납부)?\s*(?:금액|액)|합\s*계(?:\s*금액)?|총\s*계|(?<![가-힣])세액")
_SKIP_AMT_CTX = re.compile(r"납기\s*후|연체|가산|체납|미납|전월|이월")


def bill_amount(t: str) -> Tuple[Optional[int], str]:
    for name, rx in (("ontime", _L_ONTIME), ("label", _L_PAY), ("total", _L_TOTAL)):
        v = amount_after(t, rx, skip_ctx=_SKIP_AMT_CTX)
        if v:
            return v, "label"
    best = None
    for line in t.split("\n"):
        if _SKIP_AMT_CTX.search(line):
            continue
        for tok in iter_amounts(line):
            if tok.value > 0 and (best is None or tok.value > best):
                best = tok.value
    return best, "fallback"


_BRACKET = re.compile(r"\[([^\[\]\n]{2,24})\]")
_BRACKET_IGNORE = re.compile(r"Web발신|국외발신|광고|발신|알림|안내|공지|결제|승인|취소")


def _bracket_tag(t: str) -> str:
    for m in _BRACKET.finditer(t[:200]):
        if not _BRACKET_IGNORE.search(m.group(1)):
            return m.group(1).strip()
    return ""


_ISSUER_RULES: Dict[str, List[Tuple["re.Pattern[str]", Optional[str]]]] = {
    "전기": [(_c(r"한국전력(?:공사)?|한전|KEPCO"), "한국전력공사")],
    "가스": [(_c(r"(서울도시가스|코원에너지|삼천리|예스코|대한도시가스|경동도시가스|인천도시가스|부산도시가스|대구도시가스|한국가스공사)"), None)],
    "수도": [(_c(r"((?:[가-힣]{2,6}\s)?[가-힣]{0,8}(?:상하수도사업소|상하수도사업본부|상수도사업본부|수도사업본부|수도사업소|수자원공사))"), None)],
    "통신": [(_c(r"SK\s*텔레콤|SKT"), "SK텔레콤"), (_c(r"LG\s*U\+|LG\s*유플러스|유플러스"), "LG유플러스"),
            (_c(r"(?<![A-Za-z])KT(?![A-Za-z&])"), "KT"), (_c(r"SK\s*브로드밴드"), "SK브로드밴드")],
    "관리비": [(_c(r"([가-힣A-Za-z0-9]{2,15}\s*관리사무소)"), None)],
    "건강보험": [(_c(r"국민\s*건강\s*보험|건강\s*보험\s*공단|건보"), "국민건강보험공단")],
    "국민연금": [(_c(r"국민\s*연금"), "국민연금공단")],
}
_LOCAL_GOV = _c(r"([가-힣]{2,8}(?:구청|시청|군청|도청))")
_METRO = _c(r"((?:서울|부산|대구|인천|광주|대전|울산|세종)(?:특별|광역)?(?:자치)?시)")
_ORG_ANY = _c(r"([가-힣]{2,12}(?:은행|뱅크|보험|생명|화재|손해보험|증권|카드|캐피탈|세무서|구청|시청|군청|법원|경찰서|공단|교육청|주민센터|우체국|병원|의원|공사))")
_CARD_BRANDS = ("삼성|신한|현대|KB국민|국민|롯데|우리|하나|NH농협|농협|BC|비씨|씨티|IBK|기업|SC제일|광주|전북|제주|수협|새마을|우체국|"
                "카카오|토스|케이|네이버|페이코|삼성페이|애플")
_CARD_ISSUER_RE = re.compile(r"((?:" + _CARD_BRANDS + r")(?:카드|은행|뱅크|페이|Pay|pay))")
_CARD_ISSUER_LOOSE = re.compile(r"((?:" + _CARD_BRANDS + r")(?:카드|은행|뱅크|페이|Pay|pay)?)")


def find_issuer(t: str, sub: str) -> str:
    for rx, canon in _ISSUER_RULES.get(sub, []):
        m = rx.search(t)
        if m:
            return canon or (m.group(1).strip() if m.groups() else m.group(0).strip())
    if sub in TAX_SUBTYPES:
        for rx in (_LOCAL_GOV, _METRO):
            m = rx.search(t)
            if m:
                return m.group(1)
        return "지방자치단체"
    if sub == "과태료":
        m = re.search(r"경찰청|[가-힣]{2,6}경찰서|도로교통공단|한국도로공사", t)
        if m:
            return m.group(0)
        m = _LOCAL_GOV.search(t)
        return m.group(1) if m else "관할 기관"
    if sub == "카드대금":
        m = _CARD_ISSUER_RE.search(t)
        if m:
            return m.group(1)
    tag = _bracket_tag(t)
    if tag:
        return tag
    m = _ORG_ANY.search(t)
    return m.group(1) if m else ""


# --------------------------------------------------------------------------
# 타입별 추출
# --------------------------------------------------------------------------
def _best_bill_subtype(t: str) -> Tuple[str, float]:
    best, bscore = "", 0.0
    for sub, pats in BILL_SUBTYPES.items():
        s = _sum(t, pats)
        if s > bscore:
            best, bscore = sub, s
    return best, bscore


def _extract_bill(t: str, ext: Extraction, today: dt.date) -> None:
    sub, sscore = _best_bill_subtype(t)
    if sscore < 3:
        sub = "기타"
    ext.subtype = sub
    ext.issuer = find_issuer(t, sub)
    if ext.issuer:
        ext.sources["issuer"] = "rule"
    amt, how = bill_amount(t)
    if amt is not None:
        ext.amount = amt
        ext.sources["amount"] = "rule" if how == "label" else "rule-guess"
        if how != "label":
            ext.notes.append("금액을 문서 안의 가장 큰 금액으로 추정")


def _extract_notice(t: str, ext: Extraction, today: dt.date) -> None:
    counts = {
        "은행": len(re.findall(r"은행|뱅크|대출|예금|적금|이자|계좌", t)),
        "보험": len(re.findall(r"보험|증권|보장|갱신|약관|해지", t)),
        "관공서": len(re.findall(r"시청|구청|군청|도청|주민센터|세무서|국세청|경찰|법원|검찰|공단|교육청|행정|지방세|민원", t)),
        "의료": len(re.findall(r"병원|의원|검진|진료", t)),
    }
    sub = max(counts, key=lambda k: counts[k])
    ext.subtype = sub if counts[sub] > 0 else "기타"
    ext.issuer = _bracket_tag(t) or (_ORG_ANY.search(t).group(1) if _ORG_ANY.search(t) else "")
    if ext.issuer:
        ext.sources["issuer"] = "rule"
    if re.search(r"납부|납입|청구|고지|과태료|세금|보험료", t):
        amt, how = bill_amount(t)
        if amt is not None and how == "label":
            ext.amount = amt
            ext.sources["amount"] = "rule"


_COURIER = re.compile(r"CJ\s*대한통운|한진택배|롯데택배|로젠택배|우체국택배|우체국|쿠팡|컬리|로켓배송|대신택배|경동택배")


def _extract_parcel(t: str, ext: Extraction, today: dt.date) -> None:
    m = _COURIER.search(t)
    ext.issuer = m.group(0).replace("로켓배송", "쿠팡") if m else _bracket_tag(t)
    if re.search(r"부재|반송|재방문|재배송|보관\s*중|찾아가|수령\s*(?:바랍|요망)", t):
        ext.subtype = "수령필요"
    elif re.search(r"배송\s*완료|배달\s*완료|문\s*앞|경비실|무인\s*택배함", t):
        ext.subtype = "배송완료"
    else:
        ext.subtype = "배송중"


def _extract_ad(t: str, ext: Extraction, today: dt.date) -> None:
    ext.subtype = "광고"
    ext.issuer = _bracket_tag(t) or (_ORG_ANY.search(t).group(1) if _ORG_ANY.search(t) else "")


# ---- 카드 문자 ----
_NAME_MASK = re.compile(r"[가-힣]\*+[가-힣]?님?")
_TAIL_LINE = re.compile(r"(?:누적|잔액|잔여|한도|사용\s*가능|포인트|적립)[^\n]*")
_DT_STRIP = re.compile(
    r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}[./]\d{1,2}|\d{1,2}월\s*\d{1,2}일)\s*(?:\([월화수목금토일]\))?\s*"
    r"(?:(?:오전|오후)?\s*\d{1,2}:\d{2}(?::\d{2})?)?|(?:오전|오후)?\s*\d{1,2}:\d{2}(?::\d{2})?"
)
_INSTALL_STRIP = re.compile(r"일시불|\(?\s*\d{1,2}\s*개월\s*(?:할부)?\s*\)?|할부\s*\d{1,2}\s*개월?")
_WORD_STRIP = re.compile(
    r"(?<![가-힣A-Za-z])(?:해외승인|해외결제|승인취소|취소승인|결제취소|승인|취소|결제완료|결제|체크카드|신용카드|체크|신용|카드|"
    r"Web발신|국외발신|해외|국내|본인|완료|사용|이용|\[발신\])(?![가-힣])"
)


def _strip_unbalanced(f: str) -> str:
    f = f.strip(" -_[]")
    if f.startswith(")"):
        f = f[1:]
    if f.endswith("(") :
        f = f[:-1]
    if f.count("(") > f.count(")") and f.startswith("("):
        f = f[1:]
    if f.count(")") > f.count("(") and f.endswith(")"):
        f = f[:-1]
    return f.strip(" -_")


def _card_merchant(t: str, first_amount_end: int, issuer_span: Optional[Tuple[int, int]]) -> str:
    # 'XX에서 4,500원 결제' 형태
    toks = iter_amounts(t)
    for tok in toks:
        head = t[max(0, tok.start - 40):tok.start]
        m = re.search(r"([^\n\[\]]{1,30}?)에서\s*$", head)
        if m:
            cand = m.group(1).strip()
            cand = _NAME_MASK.sub("", cand).strip()
            if cand:
                return cand
    s = t
    if issuer_span:
        s = s[:issuer_span[0]] + " " + s[issuer_span[1]:]
    s = re.sub(r"\[[^\[\]\n]*\]", " ", s)
    s = _TAIL_LINE.sub(" ", s)
    for tok in sorted(iter_amounts(s), key=lambda x: -x.start):
        s = s[:tok.start] + " " + s[tok.end:]
    s = _DT_STRIP.sub(" ", s)
    s = _INSTALL_STRIP.sub(" ", s)
    s = re.sub(r"\(\s*\)", " ", s)
    s = _FOREIGN_RE.sub(" ", s)
    s = _NAME_MASK.sub(" ", s)
    s = re.sub(r"\(?\b\d{4}\)?(?=\s*(?:승인|취소|결제))", " ", s)
    s = re.sub(r"\(\d{4}\)", " ", s)
    s = _WORD_STRIP.sub(" ", s)
    s = re.sub(r"[|:·,]+", " ", s)
    frags = [_strip_unbalanced(re.sub(r"\s+", " ", f)) for f in s.split("\n")]
    frags = [f for f in frags if len(f) >= 2 and re.search(r"[가-힣A-Za-z0-9]", f)]
    if not frags:
        return ""
    best = max(frags, key=len)
    return best[:40]


def _extract_card(t: str, ext: Extraction, today: dt.date) -> None:
    head_end = len(t)
    amounts = iter_amounts(t)
    chosen = None
    for tok in amounts:
        ctx = t[max(0, tok.start - 8):tok.start]
        if re.search(r"누적|잔액|잔여|한도|가능|포인트|적립", ctx):
            continue
        chosen = tok
        break
    if chosen is not None:
        ext.amount = chosen.value
        ext.sources["amount"] = "rule"
        head_end = chosen.start
    if chosen is None and _FOREIGN_RE.search(t):
        ext.notes.append("외화 결제로 보임: 원화 금액을 확인할 수 없어 가계부에 기록하지 않음")
    # 취소 여부
    ext.is_cancel = bool(re.search(r"취소", t)) and not re.search(
        r"취소\s*(?:가능|불가|시|하실|하려|문의)|(?:가능|불가).{0,6}취소", t)
    # 할부
    m = re.search(r"(\d{1,2})\s*개월(?:\s*할부)?|할부\s*(\d{1,2})\s*개월?", t)
    if m and "일시불" not in t:
        n = int(m.group(1) or m.group(2))
        if n >= 2:
            ext.installment = n
    # 카드사
    issuer_span = None
    m = _CARD_ISSUER_LOOSE.search(t[:head_end] if head_end > 0 else t)
    if m is None:
        m = _CARD_ISSUER_RE.search(t)
    if m:
        ext.issuer = m.group(1)
        if not re.search(r"카드|은행|뱅크|페이|pay$", ext.issuer, re.I):
            ext.issuer += "카드"
        issuer_span = (m.start(), m.end())
        ext.sources["issuer"] = "rule"
        c4 = re.search(r"(?<!\d)(\d{4})(?!\d)\s*(?:승인|취소|결제)|\((\d{4})\)", t)
        ext.card = ext.issuer + ("(%s)" % (c4.group(1) or c4.group(2)) if c4 else "")
    # 일시
    ref = today
    dtm, tk = find_datetime(t, ref, prefer="past")
    if dtm is not None:
        ext.event_dt = dtm.strftime("%Y-%m-%d %H:%M") if tk else dtm.strftime("%Y-%m-%d")
        ext.time_known = tk
        ext.sources["event_dt"] = "rule"
    merchant = _card_merchant(t, head_end, issuer_span)
    if merchant:
        ext.merchant = merchant
        ext.sources["merchant"] = "rule"
    ext.subtype = "취소" if ext.is_cancel else "승인"


# ---- 영수증 ----
_MERCHANT_LABEL = re.compile(r"(?:상\s*호\s*명?|가맹점\s*명?|매장\s*명?|판매\s*처|판매\s*자|업체\s*명|점포\s*명|사업장\s*명)\s*[:]?\s*([^\n]{2,40})")
_HEADER_SKIP = re.compile(r"영수증|매출\s*전표|거래\s*명세|고객\s*용|주문|Web발신|^[\W_]+$|^\d[\d\s,.:/-]*$|사업자|대표|전화|TEL|주소|감사|\*{3,}|-{3,}|={3,}", re.I)
_RECEIPT_AMT_LABELS = [
    re.compile(r"총\s*결제\s*금액|최종\s*결제\s*금액|결제\s*금액|총\s*결제액"),
    re.compile(r"받을\s*금액|청구\s*금액|승인\s*금액|카드\s*결제\s*금액|카드\s*결제"),
    re.compile(r"합\s*계(?:\s*금액)?|총\s*합계|총\s*금액|총\s*액|총\s*계"),
    re.compile(r"받은\s*금액"),
]
_ONLINE_RX = re.compile(r"온라인|인터넷|쇼핑몰|주문\s*번호|배송비|배송지|택배|www\.|https?://|\.com|\.co\.kr|앱\s*결제|모바일\s*주문|스마트스토어", re.I)
_NO_REFUND_RX = re.compile(r"(?:교환|환불|반품)\s*(?:및\s*|/\s*|,\s*)?(?:교환|환불|반품)?\s*(?:은\s*|는\s*)?(?:불가|안\s*됩|어렵)")
_REFUND_DAYS_1 = re.compile(r"(?:교환|환불|반품|청약\s*철회)[^\n.]{0,25}?(\d{1,3})\s*일\s*(?:이내|내|간|안)")
_REFUND_DAYS_2 = re.compile(r"(\d{1,3})\s*일\s*(?:이내|내)[^\n.]{0,20}?(?:교환|환불|반품)")


_LABELISH = re.compile(r"^(?:상품|품명|품목|수량|단가|금액|합계|배송|결제|카드|승인|주문|일시|사업자|대표|전화|주소|TEL|감사|영수증|교환|환불|할인|부가|공급|받은|받을|거스름)", re.I)


def _receipt_merchant(t: str) -> str:
    m = _MERCHANT_LABEL.search(t)
    if m:
        v = re.split(r"\s{2,}|\s(?:사업자|대표|전화|TEL)", m.group(1))[0].strip()
        if v:
            return v[:40]
    head = "\n".join(t.split("\n")[:3])
    tag = _BRACKET.search(head)
    if tag and not _BRACKET_IGNORE.search(tag.group(1)):
        return tag.group(1).strip()
    for line in t.split("\n")[:8]:
        line = line.strip()
        if not line or _HEADER_SKIP.search(line) or _LABELISH.match(line):
            continue
        if len(line) > 30 or ":" in line:
            continue
        if re.search(r"[가-힣A-Za-z]{2}", line) and not scan_dates(line):
            return line
    return ""


def _extract_receipt(t: str, ext: Extraction, today: dt.date) -> None:
    ext.subtype = "영수증"
    ext.merchant = _receipt_merchant(t)
    if ext.merchant:
        ext.sources["merchant"] = "rule"
    amt = None
    for rx in _RECEIPT_AMT_LABELS:
        amt = amount_after(t, rx)
        if amt:
            break
    if not amt:
        toks = [x for x in iter_amounts(t) if x.value > 0]
        amt = max((x.value for x in toks), default=None)
        if amt:
            ext.notes.append("금액을 문서 안의 가장 큰 금액으로 추정")
            ext.sources["amount"] = "rule-guess"
    else:
        ext.sources["amount"] = "rule"
    ext.amount = amt
    # 일시: 교환/환불/보증 문구 줄의 날짜는 제외
    lines = [ln for ln in t.split("\n") if not re.search(r"교환|환불|반품|유효\s*기간|보증|만료|A/S", ln)]
    dtm, tk = find_datetime("\n".join(lines), today, prefer="past")
    if dtm is not None:
        ext.event_dt = dtm.strftime("%Y-%m-%d %H:%M") if tk else dtm.strftime("%Y-%m-%d")
        ext.time_known = tk
        ext.sources["event_dt"] = "rule"
    ext.online = bool(_ONLINE_RX.search(t)) or is_online_merchant(ext.merchant)
    if _NO_REFUND_RX.search(t):
        ext.refund_days = 0
    else:
        m = _REFUND_DAYS_1.search(t) or _REFUND_DAYS_2.search(t)
        if m:
            ext.refund_days = int(m.group(1))
    m = _CARD_ISSUER_RE.search(t)
    if m:
        ext.card = m.group(1)
    ext.is_cancel = bool(re.search(r"승인\s*취소|결제\s*취소|취소\s*영수증|반품\s*영수증", t))


# --------------------------------------------------------------------------
# 진입점
# --------------------------------------------------------------------------
_FLAG_RX = {
    "urgent": re.compile(r"독촉|최고서|압류|체납|연체|지급\s*명령|소송|출석|이의\s*신청|제출|회신|갱신|만기|납부\s*(?:하여|바랍|기한)"),
    "legal": re.compile(r"압류|소송|지급\s*명령|법원|독촉|최고서|강제\s*집행|체납\s*처분"),
    "warranty": re.compile(r"보증서|품질\s*보증|A/S|워런티|보증\s*기간"),
    "tax_evidence": re.compile(r"세금\s*계산서|지출\s*증빙|소득\s*공제|경비\s*처리|매입\s*세액"),
    "important": re.compile(r"납부|청구|고지|세금|연체|압류|법원|체납|계약|등기|임대차|확정일자|증권|보험금"),
}


def _flags(t: str) -> List[str]:
    return [k for k, rx in _FLAG_RX.items() if rx.search(t)]


def _completeness(ext: Extraction) -> float:
    req = ext.required_fields()
    if not req:
        return 1.0
    have = 0
    for f in req:
        if f == "doc_type":
            continue
        if getattr(ext, f):
            have += 1
    return have / len(req)


def analyze(text: str, today: Optional[dt.date] = None,
            force_type: Optional[str] = None) -> Extraction:
    today = today or dt.date.today()
    t = normalize_text(text)
    ext = Extraction()
    scores = score_types(t)
    ext.scores = {k: round(v, 1) for k, v in scores.items()}

    ranked = sorted(_PRIORITY, key=lambda k: (-scores[k], _PRIORITY.index(k)))
    top, second = ranked[0], ranked[1]
    if force_type:
        dtype = force_type
        type_conf = 1.0
    elif scores[top] >= _THRESHOLD:
        dtype = top
        type_conf = min(1.0, scores[top] / 8.0)
        if scores[top] - scores[second] < 3:
            type_conf *= 0.75
    else:
        dtype = "unknown"
        type_conf = 0.15
    ext.doc_type = dtype
    ext.flags = _flags(t)

    # 문서일자 / 기준일
    dd = find_doc_date(t, today)
    ext.doc_date = dd.isoformat() if dd else None
    ref = dd or today

    # 공통: 번호, 자동이체, 청구월
    ext.customer_no = find_customer_no(t)
    if dtype in ("bill", "notice", "unknown"):
        auto, aday, adate = find_auto_debit(t, ref)
        ext.auto_debit, ext.auto_debit_day = auto, aday
        bm = find_billing_month(t, ref)
        if bm:
            ext.billing_month = bm
            ext.sources["billing_month"] = "rule"

    if dtype == "bill":
        _extract_bill(t, ext, today)
    elif dtype == "notice":
        _extract_notice(t, ext, today)
    elif dtype == "card_sms":
        _extract_card(t, ext, today)
    elif dtype == "receipt":
        _extract_receipt(t, ext, today)
    elif dtype == "parcel":
        _extract_parcel(t, ext, today)
    elif dtype == "ad":
        _extract_ad(t, ext, today)

    if dtype in ("bill", "notice"):
        due = find_due_date(t, ref)
        if due is None and ext.auto_debit:
            auto, aday, adate = find_auto_debit(t, ref)
            if adate:
                due = adate
            elif aday:
                tok = DateTok(0, 0, "d", None, None, aday)
                due = resolve_date(tok, ref, "future", tol_days=3)
        if due is not None:
            ext.due_date = due.isoformat()
            ext.sources["due_date"] = "rule"
    if dtype == "bill" and ext.sources.get("amount") == "rule-guess":
        type_conf *= 0.9

    comp = _completeness(ext)
    ext.confidence = round(0.6 * type_conf + 0.4 * comp, 3)
    if dtype == "unknown":
        ext.confidence = min(ext.confidence, 0.3)
    return ext
