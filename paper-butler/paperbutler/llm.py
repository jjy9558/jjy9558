"""선택적 LLM 연동 (OpenAI 호환 /chat/completions, 로컬 VLM 권장).

- 규칙이 비운 필드만 LLM이 채운다. 규칙이 확신하면 규칙이 이긴다.
- LLM 으로 보내는 텍스트는 항상 mask_text 를 거친다(이미지 전사 제외).
- 기본적으로 localhost/사설망 주소만 허용한다. 외부 주소는 allow_remote 가 필요.
- 표준 라이브러리(urllib)만 사용.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import ipaddress
import json
import logging
import mimetypes
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .extract import DOC_TYPES, Extraction, _completeness
from .masking import mask_text
from .parsing import parse_amount_str

log = logging.getLogger("paperbutler.llm")


class LLMError(Exception):
    pass


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "ip6-localhost") or host.endswith((".local", ".lan", ".internal", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


class LLMClient:
    """OpenAI 호환 엔드포인트 클라이언트."""

    def __init__(self, base_url: str, model: str, api_key: str = "", vision_model: str = "",
                 timeout: int = 120, allow_remote: bool = False, opener: Any = None):
        if not base_url:
            raise LLMError("LLM 주소(PAPERBUTLER_LLM_URL)가 설정되지 않았습니다.")
        if not allow_remote and not is_local_url(base_url):
            raise LLMError(
                "개인 서류 보호를 위해 로컬/사설망 LLM 주소만 허용합니다: %s "
                "(정말 외부로 보내려면 PAPERBUTLER_ALLOW_REMOTE_LLM=1)" % base_url)
        self.base_url = base_url.rstrip("/")
        self.model = model or "local-model"
        self.vision_model = vision_model or model or "local-model"
        self.api_key = api_key
        self.timeout = timeout
        # 환경의 프록시를 타지 않도록 (로컬 서버)
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # -- 저수준 -------------------------------------------------------------
    def chat(self, messages: List[dict], model: str = "", max_tokens: int = 900,
             temperature: float = 0.0) -> str:
        body = json.dumps({
            "model": model or self.model, "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body, headers=headers)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"] or ""
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
            raise LLMError("LLM 호출 실패: %s" % type(e).__name__) from e

    # -- 고수준 -------------------------------------------------------------
    def extract_fields(self, masked_text: str) -> Dict[str, Any]:
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT + "\n\n---\n" + masked_text[:6000]},
        ]
        return parse_json_object(self.chat(msgs))

    def transcribe_image(self, path: str) -> str:
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) > 15 * 1024 * 1024:
            raise LLMError("이미지가 너무 큽니다(15MB 초과). 해상도를 줄여 다시 찍어 주세요.")
        uri = "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": TRANSCRIBE_PROMPT},
            {"type": "image_url", "image_url": {"url": uri}},
        ]}]
        return self.chat(msgs, model=self.vision_model, max_tokens=2000)


SYSTEM_PROMPT = (
    "당신은 한국어 서류(고지서, 카드 결제 문자, 영수증, 안내문)에서 정보를 추출하는 도구입니다. "
    "반드시 JSON 객체 하나만 출력하세요. 모르는 값은 null 로 두고 추측하지 마세요. "
    "주민등록번호·계좌번호·카드번호는 출력하지 마세요."
)
USER_PROMPT = (
    "아래 문서에서 다음 키만 담은 JSON 을 출력하세요.\n"
    '{"doc_type": "bill|card_sms|receipt|notice|parcel|ad|unknown", "subtype": "", '
    '"issuer": "기관명", "merchant": "가맹점", "amount": 정수(원), "due_date": "YYYY-MM-DD", '
    '"event_dt": "YYYY-MM-DD HH:MM", "billing_month": "YYYY-MM", "auto_debit": true|false, '
    '"is_cancel": true|false, "installment": 0}'
)
TRANSCRIBE_PROMPT = (
    "이 사진은 종이 서류(고지서/영수증/우편물)입니다. 보이는 글자를 줄바꿈을 유지해 그대로 옮겨 적으세요. "
    "요약·해석·추가 설명 없이 글자만 출력하세요. 읽을 수 없는 부분은 건너뛰세요."
)


def parse_json_object(s: str) -> Dict[str, Any]:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise LLMError("LLM 응답에서 JSON 을 찾지 못했습니다.")
    try:
        obj = json.loads(s[i:j + 1])
    except ValueError as e:
        raise LLMError("LLM 응답 JSON 파싱 실패") from e
    if not isinstance(obj, dict):
        raise LLMError("LLM 응답이 객체가 아닙니다.")
    return obj


# --------------------------------------------------------------------------
# 검증 / 병합
# --------------------------------------------------------------------------
_REV_TYPES = {v: k for k, v in DOC_TYPES.items()}


def _s(v: Any, n: int = 40) -> str:
    if not isinstance(v, str):
        return ""
    v = re.sub(r"[\x00-\x1f\x7f]", " ", v).strip()
    return v[:n]


def _bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v.strip().lower() in ("true", "yes", "예", "y", "1"):
            return True
        if v.strip().lower() in ("false", "no", "아니오", "n", "0"):
            return False
    return None


def _date_str(v: Any, with_time: bool = False) -> Optional[str]:
    if not isinstance(v, str):
        return None
    m = re.match(r"\s*(20\d{2})-(\d{1,2})-(\d{1,2})(?:[ T]+(\d{1,2}):(\d{2}))?", v)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        dt.date(y, mo, d)
    except ValueError:
        return None
    if with_time and m.group(4) is not None:
        h, mi = int(m.group(4)), int(m.group(5))
        if h > 23 or mi > 59:
            return "%04d-%02d-%02d" % (y, mo, d)
        return "%04d-%02d-%02d %02d:%02d" % (y, mo, d, h, mi)
    return "%04d-%02d-%02d" % (y, mo, d)


def sanitize_llm_fields(d: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 응답을 타입/범위 검증해 안전한 필드만 남긴다."""
    out: Dict[str, Any] = {}
    if not isinstance(d, dict):
        return out
    dtp = d.get("doc_type")
    if isinstance(dtp, str):
        dtp = dtp.strip()
        dtp = dtp if dtp in DOC_TYPES else _REV_TYPES.get(dtp)
        if dtp:
            out["doc_type"] = dtp
    for k in ("subtype", "issuer", "merchant"):
        v = _s(d.get(k))
        if v:
            out[k] = v
    a = d.get("amount")
    if isinstance(a, bool):
        a = None
    if isinstance(a, (int, float)):
        a = int(a)
    elif isinstance(a, str):
        a = parse_amount_str(a)
    else:
        a = None
    if a is not None and 0 < a <= 10 ** 11:
        out["amount"] = a
    v = _date_str(d.get("due_date"))
    if v:
        out["due_date"] = v
    v = _date_str(d.get("doc_date"))
    if v:
        out["doc_date"] = v
    v = _date_str(d.get("event_dt"), with_time=True)
    if v:
        out["event_dt"] = v
    bm = d.get("billing_month")
    if isinstance(bm, str) and re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", bm.strip()):
        out["billing_month"] = bm.strip()
    for k in ("auto_debit", "is_cancel"):
        b = _bool(d.get(k))
        if b is not None:
            out[k] = b
    inst = d.get("installment")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool) and 0 <= int(inst) <= 60:
        out["installment"] = int(inst)
    return out


_FILL_FIELDS = ("subtype", "issuer", "merchant", "amount", "due_date", "event_dt", "doc_date", "billing_month")


def merge_extraction(rule: Extraction, llm_fields: Dict[str, Any], confident: float = 0.7) -> Extraction:
    """규칙 결과에 LLM 결과를 병합.

    - 규칙이 확신(confidence >= confident)이면: 비어 있는 필드만 채운다.
    - 확신이 낮으면(< confident): 비어 있는 필드를 채우고, 불리언/할부도 받아들인다.
    - 문서 종류는 규칙이 unknown 이거나 확신이 0.5 미만일 때만 LLM 이 바꾼다.
    """
    f = sanitize_llm_fields(llm_fields)
    res = copy.deepcopy(rule)
    filled: List[str] = []
    low = rule.confidence < confident

    if "doc_type" in f and f["doc_type"] != rule.doc_type and (
            rule.doc_type == "unknown" or rule.confidence < 0.5):
        res.doc_type = f["doc_type"]
        res.sources["doc_type"] = "llm"
        filled.append("doc_type")
    for k in _FILL_FIELDS:
        if k in f and not getattr(res, k):
            setattr(res, k, f[k])
            res.sources[k] = "llm"
            filled.append(k)
            if k == "event_dt":
                res.time_known = ":" in f[k]
    if low:
        if "auto_debit" in f and f["auto_debit"] and not res.auto_debit:
            res.auto_debit = True
            res.sources["auto_debit"] = "llm"
            filled.append("auto_debit")
        if "is_cancel" in f and f["is_cancel"] and not res.is_cancel:
            res.is_cancel = True
            res.sources["is_cancel"] = "llm"
            filled.append("is_cancel")
        if "installment" in f and not res.installment and f["installment"] >= 2:
            res.installment = f["installment"]
            res.sources["installment"] = "llm"
            filled.append("installment")
    if filled:
        res.notes.append("LLM이 채운 항목: " + ", ".join(filled))
        res.confidence = round(max(rule.confidence, 0.5 + 0.3 * _completeness(res)), 3)
    return res


def enrich_with_llm(ext: Extraction, text: str, client: Any, threshold: float = 0.7) -> Extraction:
    """필요할 때만(LLM 호출) 병합. 실패해도 규칙 결과를 그대로 돌려준다."""
    if client is None or not ext.needs_llm(threshold):
        return ext
    try:
        data = client.extract_fields(mask_text(text))
    except Exception as e:  # LLM 은 선택 기능: 어떤 오류든 규칙 결과 유지
        log.warning("LLM 보완 실패(규칙 결과 사용): %s", type(e).__name__)
        ext.notes.append("LLM 보완 실패: 규칙 결과만 사용")
        return ext
    return merge_extraction(ext, data, threshold)
