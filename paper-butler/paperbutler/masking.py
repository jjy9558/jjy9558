"""개인정보 마스킹 (주민번호/카드번호/계좌번호/전화번호/고객번호).

모든 출력(콘솔, CSV, ICS, 로그, LLM 전송 텍스트)은 이 모듈을 거친다.
mask_text 는 멱등(idempotent)이다: 두 번 적용해도 결과가 같다.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

__all__ = ["mask_text", "mask_id", "MaskingFilter", "redact_document"]

# ---- 주민등록번호 -----------------------------------------------------------
_RRN = re.compile(r"(?<![\d*])(\d{2})(\d{2})(\d{2})([- ]?)([1-8])\d{6}(?!\d)")


def _rrn_sub(m: "re.Match[str]") -> str:
    mm, dd = int(m.group(2)), int(m.group(3))
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return m.group(0)
    sep = m.group(4)
    # 하이픈/공백이 없는 13자리는 날짜 검증을 통과해야만 마스킹(오탐 방지)
    return "******" + (sep if sep else "-") + "*******"


# ---- 카드번호 ---------------------------------------------------------------
_CARD_SEP = re.compile(r"(?<![\d*])([\d*]{4})([- ])([\d*]{4})\2([\d*]{4})\2(\d{4})(?!\d)")
_CARD_RUN = re.compile(r"(?<![\d*])(\d{15,16})(?!\d)")


def _luhn(num: str) -> bool:
    total, alt = 0, False
    for ch in reversed(num):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _card_run_sub(m: "re.Match[str]") -> str:
    s = m.group(1)
    if _luhn(s):
        return "*" * (len(s) - 4) + s[-4:]
    return s


# ---- 전화번호 ---------------------------------------------------------------
# 대표번호(1588-xxxx 등)는 개인정보가 아니므로 마스킹하지 않는다.
_PHONE = re.compile(
    r"(?<![\d-])(0(?:2|[3-6]\d|1[016-9]|70|80))([-. ]?)(\d{3,4})\2(\d{4})(?![\d-])"
)
_PHONE_INTL = re.compile(
    r"(?<![\d*])\+82[- ]?(?:10|11|16|17|18|19|2|[3-6]\d|70)[- ]?\d{3,4}[- ]?(\d{4})(?!\d)"
)

# ---- 계좌번호 ---------------------------------------------------------------
_ACCT_HYPHEN = re.compile(r"(?<![\d*-])\d{2,6}(?:-\d{2,8}){1,3}(?![\d-])")
_ACCT_CTX = re.compile(
    r"((?:가상\s*)?(?:입금|출금|환불|이체|급여)?\s*계좌(?:\s*번호)?\s*[:：]?\s*"
    r"(?:[가-힣]{2,8}(?:은행|뱅크|금고|농협|협동조합|증권|우체국)\s*)?)"
    r"(\d[\d-]{7,19}\d)"
)
_ACCT_BANK = re.compile(
    r"((?:은행|뱅크|농협|새마을금고|우체국|신협|수협)\s*)(\d{10,14})(?!\d)"
)

# ---- 라벨 붙은 식별번호(고객번호/납부번호 등) --------------------------------
ID_LABELS = (
    r"고객\s*번호|납부\s*자\s*번호|납부\s*번호|전자\s*납부\s*번호|수용가\s*번호|가입자\s*번호|"
    r"계약\s*번호|관리\s*번호|증권\s*번호|사용자\s*번호|청구\s*번호|고지서\s*번호|납세\s*번호|"
    r"주민\s*등록\s*번호|여권\s*번호|운전\s*면허\s*번호|회원\s*번호|사용\s*계약\s*번호"
)
_ID_LABELED = re.compile(
    r"((?:" + ID_LABELS + r")\s*[:]?\s*)([0-9A-Z][0-9A-Z-]{4,}[0-9A-Z])"
)


def mask_id(value: str) -> str:
    """식별번호 표시용 마스킹: 끝 3글자만 남기고 영숫자를 '*'로(하이픈 유지)."""
    if not value:
        return value
    alnum_total = sum(1 for c in value if c.isalnum())
    keep = 3 if alnum_total >= 7 else (1 if alnum_total >= 3 else 0)
    out = []
    seen_from_end = 0
    # 뒤에서부터 센다
    for ch in reversed(value):
        if ch.isalnum():
            if seen_from_end < keep:
                out.append(ch)
            else:
                out.append("*")
            seen_from_end += 1
        else:
            out.append(ch)
    return "".join(reversed(out))


def _labeled_sub(m: "re.Match[str]") -> str:
    val = m.group(2)
    if "*" in val:
        return m.group(0)
    return m.group(1) + mask_id(val)


def _acct_hyphen_sub(m: "re.Match[str]") -> str:
    s = m.group(0)
    digits = re.sub(r"\D", "", s)
    if not (10 <= len(digits) <= 16):
        return s
    if re.fullmatch(r"\d{3}-\d{2}-\d{5}", s):  # 사업자등록번호 형식은 공개 정보
        return s
    keep = 4
    out = []
    count = 0
    for ch in reversed(s):
        if ch.isdigit():
            out.append(ch if count < keep else "*")
            count += 1
        else:
            out.append(ch)
    return "".join(reversed(out))


def _acct_ctx_sub(m: "re.Match[str]") -> str:
    num = m.group(2)
    if "*" in num:
        return m.group(0)
    return m.group(1) + _acct_hyphen_sub_plain(num)


def _acct_hyphen_sub_plain(s: str) -> str:
    out, count = [], 0
    for ch in reversed(s):
        if ch.isdigit():
            out.append(ch if count < 4 else "*")
            count += 1
        else:
            out.append(ch)
    return "".join(reversed(out))


def mask_text(text: str, extra_secrets: Optional[Iterable[str]] = None) -> str:
    """텍스트 속 민감정보를 마스킹한다. 멱등."""
    if not text:
        return text
    s = text
    s = _RRN.sub(_rrn_sub, s)
    s = _CARD_SEP.sub(lambda m: "****-****-****-" + m.group(5) if m.group(2) == "-"
                      else "**** **** **** " + m.group(5), s)
    s = _CARD_RUN.sub(_card_run_sub, s)
    s = _PHONE_INTL.sub(lambda m: "+82-**-****-" + m.group(1), s)
    s = _PHONE.sub(lambda m: m.group(1) + "-****-" + m.group(4), s)
    s = _ID_LABELED.sub(_labeled_sub, s)
    s = _ACCT_CTX.sub(_acct_ctx_sub, s)
    s = _ACCT_BANK.sub(lambda m: m.group(1) + _acct_hyphen_sub_plain(m.group(2)), s)
    s = _ACCT_HYPHEN.sub(_acct_hyphen_sub, s)
    if extra_secrets:
        for secret in extra_secrets:
            if secret and len(secret) >= 6 and secret in s:
                s = s.replace(secret, mask_id(secret))
    return s


def redact_document(text: str, extra_secrets: Optional[Iterable[str]] = None) -> str:
    """--redact-only 용: 마스킹된 사본 텍스트(머리말 포함)."""
    body = mask_text(text, extra_secrets)
    return "[개인정보 마스킹 사본 - 주민번호/카드/계좌/전화/고객번호 가림]\n" + body


class MaskingFilter(logging.Filter):
    """로그 레코드의 메시지를 마스킹한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - 방어적
            msg = str(record.msg)
        record.msg = mask_text(msg)
        record.args = ()
        return True
