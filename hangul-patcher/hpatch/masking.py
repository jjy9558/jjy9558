"""제어코드 마스킹/복원.

번역 전에 제어코드/변수/태그를 ``<번호>`` 플레이스홀더로 바꿔 LLM에 보내고,
번역 후 원래 문자열로 복원한다. 플레이스홀더가 하나라도 사라지거나 늘어나면
복원은 ``None`` 을 돌려주고, 호출 측은 원문으로 폴백한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# 번역문에서 플레이스홀더를 찾는 패턴 (LLM이 공백을 끼워 넣는 정도는 허용)
PLACEHOLDER_RE = re.compile(r"<\s*(\d+)\s*>")
# 원문에 우연히 들어 있는 "<3>" 같은 문자열도 토큰으로 보호한다
_LITERAL_PH = r"<\d+>"

_HANGUL_RE = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")

# RPG Maker MV/MZ 제어코드 (JSON 디코드 후의 문자열 기준: 역슬래시 1개)
RPGMAKER_PATTERNS: List[str] = [
    # \V[1] \N[2] \C[3] \I[4] \FS[20] \G ... (엔진은 연속된 영문자를 코드 하나로 읽는다)
    r"\\(?:[A-Za-z]+(?:\[[^\]\r\n]*\])?|[$.|!><^{}\\])",
    # %1 %2 (용어집 메시지 치환)
    r"%\d+",
    # 흔한 메시지 플러그인 태그
    r"(?i:</?(?:br|wordwrap|b|i|u|left|center|right)>)",
]

# Ren'Py: 이스케이프, [[ {{ 리터럴, [변수], {태그}, %(name)s, %s, %%
RENPY_PATTERNS: List[str] = [
    r"\\.",
    r"\[\[",
    r"\{\{",
    r"\[[^\[\]\r\n]*\]",
    r"\{[^{}\r\n]*\}",
    r"%\([A-Za-z_]\w*\)[#0\- +]*\d*(?:\.\d+)?[sdifrxXeEgGc]",
    r"%%",
    r"%[sdifr]",
]


@dataclass
class MaskResult:
    original: str
    masked: str  # 앞뒤 공백을 제외한 마스킹된 본문
    tokens: List[str] = field(default_factory=list)
    lead: str = ""
    trail: str = ""

    @property
    def core(self) -> str:
        return self.masked


class Masker:
    def __init__(self, patterns: Sequence[str], forbidden: str = "") -> None:
        self.patterns = list(patterns)
        self.forbidden = forbidden
        alts = [f"(?:{p})" for p in self.patterns] + [f"(?:{_LITERAL_PH})"]
        self._re = re.compile("|".join(alts))

    def with_extra(self, extra_patterns: Sequence[str]) -> "Masker":
        if not extra_patterns:
            return self
        return Masker(list(extra_patterns) + self.patterns, self.forbidden)

    def mask(self, text: str) -> MaskResult:
        stripped_l = text.lstrip()
        lead = text[: len(text) - len(stripped_l)]
        core = stripped_l.rstrip()
        trail = stripped_l[len(core):]
        tokens: List[str] = []

        def repl(m: "re.Match[str]") -> str:
            tokens.append(m.group(0))
            return f"<{len(tokens) - 1}>"

        masked = self._re.sub(repl, core)
        return MaskResult(text, masked, tokens, lead, trail)

    def needs_translation(self, mr: MaskResult) -> bool:
        """번역이 필요한 문자열인가 (글자가 있고, 이미 한글이 대부분이 아님)."""
        letters = [c for c in mr.masked if c.isalpha()]
        if not letters:
            return False
        hangul = sum(1 for c in letters if _HANGUL_RE.match(c))
        return hangul / len(letters) < 0.3

    def restore(self, translated: str, mr: MaskResult) -> Optional[str]:
        """플레이스홀더를 원래 제어코드로 복원. 검증에 실패하면 None."""
        if translated is None:
            return None
        body = translated.strip()
        if not body:
            return None
        found = [int(m.group(1)) for m in PLACEHOLDER_RE.finditer(body)]
        if sorted(found) != list(range(len(mr.tokens))):
            return None
        if self.forbidden:
            rest = PLACEHOLDER_RE.sub("", body)
            if any(c in rest for c in self.forbidden):
                return None
        text = PLACEHOLDER_RE.sub(lambda m: mr.tokens[int(m.group(1))], body)
        return mr.lead + text + mr.trail

    def check(self, source: str, translated: str) -> bool:
        """완성된 번역문(제어코드가 실제 문자열로 들어 있음)이 원문과 같은 제어코드 구성인지 검사."""
        a = self.mask(source)
        b = self.mask(translated)
        if sorted(a.tokens) != sorted(b.tokens) or not translated.strip():
            return False
        if self.forbidden:
            rest = PLACEHOLDER_RE.sub("", b.masked)
            if any(c in rest for c in self.forbidden):
                return False
        return True

    def restore_or_fallback(self, translated: str, mr: MaskResult) -> Tuple[str, bool]:
        restored = self.restore(translated, mr)
        if restored is None:
            return mr.original, False
        return restored, True


def rpgmaker_masker() -> Masker:
    return Masker(RPGMAKER_PATTERNS)


def renpy_masker() -> Masker:
    # 번역문에 맨몸의 [ { 나 역슬래시가 남아 있으면 Ren'Py 텍스트 파서가 깨지므로 거부한다
    return Masker(RENPY_PATTERNS, forbidden="[{\\")
