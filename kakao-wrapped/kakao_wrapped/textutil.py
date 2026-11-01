"""Text helpers: redaction, link extraction, simple Korean tokenisation."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional
from urllib.parse import urlsplit

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'\)\]]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 010-1234-5678, 02-123-4567, +82 10 1234 5678, 01012345678
PHONE_RES = [
    re.compile(r"\+82[-.\s]?\(?0?\d{1,2}\)?[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)\d{2,4}-\d{3,4}-\d{4}(?!\d)"),
]
RRN_RE = re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)")
CARD_RE = re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)")


def redact(text: str) -> str:
    """Mask phone numbers, e-mail addresses, ID/card numbers and URLs in a quote."""
    text = URL_RE.sub("[링크]", text)
    text = EMAIL_RE.sub("[이메일]", text)
    text = RRN_RE.sub("[번호]", text)
    text = CARD_RE.sub("[번호]", text)
    for rx in PHONE_RES:
        text = rx.sub("[전화번호]", text)
    return text


def extract_domains(text: str) -> List[str]:
    out = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,!?;:")
        if not url.lower().startswith("http"):
            url = "http://" + url
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            continue
        host = normalize_domain(host)
        if host and "." in host:
            out.append(host)
    return out


_DOMAIN_ALIASES = {"youtu.be": "youtube.com", "music.youtube.com": "youtube.com"}


def normalize_domain(host: str) -> str:
    host = host.lower().strip(".")
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return _DOMAIN_ALIASES.get(host, host)


# ---------------------------------------------------------------------------
# ㅋㅋ / emotion helpers
# ---------------------------------------------------------------------------

KKK_RUN_RE = re.compile(r"ㅋ+")


def count_char(text: str, ch: str) -> int:
    return text.count(ch)


def longest_run(text: str, ch: str = "ㅋ") -> int:
    best = 0
    for m in re.finditer(re.escape(ch) + "+", text):
        best = max(best, len(m.group(0)))
    return best


# ---------------------------------------------------------------------------
# tokenisation
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[^\w가-힣ㄱ-ㅎㅏ-ㅣ]+")
_JAMO_RE = re.compile(r"[ㄱ-ㅎㅏ-ㅣ]+")
_ONLY_DIGITS_RE = re.compile(r"^[\d_]+$")
_HANGUL_RE = re.compile(r"[가-힣]")

# endings (verb-ish) first, then particles; longest match wins.
_SUFFIXES = sorted(
    set(
        """
        했어요 했어 했다 했는데 했던 했고 했지 해요 합니다 할게 할까 하자 하고 해서 하는 한다 하네 하면
        입니다 이에요 예요 이야 이다 인데 이라 이랑 이고 이면
        에서는 에서도 에서 에게서 에게는 에게 한테는 한테도 한테서 한테 으로는 으로도 으로 이라도
        부터 까지 처럼 보다 마다 밖에 조차 만큼 같이 이나 이든 이라서 라서 라고 이라고
        이가 이는 이도 이를 이랑 이의 에는 에도 에만 에서만
        은 는 이 가 을 를 의 에 도 만 로 와 과 랑 야 나 요 네 죠 지 님 들
        """.split()
    ),
    key=len,
    reverse=True,
)

STOPWORDS = set(
    """
    진짜 그냥 너무 근데 이제 지금 그리고 그래서 하지만 그럼 그러면 그러다 아니 아님 완전 그거 이거 저거
    그게 이게 저게 나도 우리 저도 제가 내가 네가 니가 너도 나는 너는 있어 없어 있는 없는 있다 없다 같아
    같은 같이 이렇게 그렇게 저렇게 어떻게 아직 다시 혹시 일단 약간 뭔가 그래 맞아 맞네 하는 되는 하고
    해서 해도 하면 한번 뭐가 뭐야 뭐지 뭐함 어디 언제 누구 왜냐면 그래도 그리고 아무 아마 이런 그런 저런
    이건 그건 저건 이게 근데요 그쵸 그죠 오케 오키 감사 ok ㅇㅋ 그러게 하나 하긴 하여튼 어차피 정말 계속
    다들 모두 이미 방금 먼저 조금 많이 제일 가장 별로 사실 암튼 아무튼 그니까 그러니까 그리고요 근데도
    the and you for are that this with have not but
    """.split()
)


def _strip_suffix(tok: str) -> str:
    for suf in _SUFFIXES:
        if len(tok) - len(suf) >= 2 and tok.endswith(suf):
            return tok[: -len(suf)]
    return tok


def clean_for_tokens(text: str) -> str:
    text = URL_RE.sub(" ", text)
    text = EMAIL_RE.sub(" ", text)
    return text


def tokenize(text: str, drop: Optional[Iterable[str]] = None) -> List[str]:
    """Very small Korean-ish tokenizer.

    Splits on whitespace/punctuation, removes jamo-only chatter (ㅋㅋ, ㅎㅎ, ㅠㅠ ...),
    URLs, numbers, strips common particles/endings heuristically and drops stopwords.
    ``drop`` is a collection of strings (e.g. member names); tokens containing one are dropped.
    """
    drop_list = [d for d in (drop or []) if d]
    out: List[str] = []
    for raw in _SPLIT_RE.split(clean_for_tokens(text)):
        if not raw:
            continue
        tok = _JAMO_RE.sub("", raw).strip("_")
        if len(tok) < 2 or _ONLY_DIGITS_RE.match(tok):
            continue
        tok = tok.lower()
        if _HANGUL_RE.search(tok):
            tok = _strip_suffix(tok)
        if len(tok) < 2 or tok in STOPWORDS:
            continue
        if any((d in tok) if len(d) >= 2 else (d == tok) for d in drop_list):
            continue
        out.append(tok)
    return out


def has_content(text: str) -> bool:
    """True if the message has some real words (not only ㅋㅋ / links / punctuation)."""
    t = _JAMO_RE.sub("", URL_RE.sub("", text))
    return len(re.sub(r"[\W_]+", "", t)) >= 2
