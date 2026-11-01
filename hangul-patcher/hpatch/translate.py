"""프롬프트 구성, 배치 응답 파싱, 번역 실행(번역 메모리/용어집 연동)."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

from .glossary import Glossary
from .llm import LLMError, LLMUnreachable
from .masking import MaskResult, Masker
from .tm import TranslationMemory
from .units import Unit

DEFAULT_STYLE = "자연스러운 한국어 게임 대사체. 말투(존댓말/반말)는 화자와 문맥에 맞출 것."

SYSTEM_PROMPT = """You are a professional video-game localizer. Translate game text into natural Korean (한국어).
Source language: {src}.
Tone: {style}

Rules:
- Tokens like <0>, <1>, <2> are placeholders for game control codes, variables and tags. Copy every placeholder EXACTLY as written, keep each one exactly once, never translate, renumber or delete them. You may move them if Korean word order requires it.
- Keep line breaks: if an item has N lines (separated by \\n), output N lines.
- Items may include "speaker" and "prev" (previous line) fields. They are context only; do NOT translate or output them.
- Translate names and terms consistently. Follow the glossary exactly when given.
- Do not add notes, explanations, romanization or extra quotation marks.
- Output ONLY a JSON array with one object per input item, in the same order: [{{"id": 1, "text": "<Korean translation>"}}, ...]"""


@dataclass
class Item:
    source: str
    kind: str
    mr: MaskResult
    speaker: str = ""
    prev: str = ""

    @property
    def size(self) -> int:
        return len(self.mr.masked) + len(self.speaker) + len(self.prev) + 40


def build_messages(items: List[Item], glossary: Optional[Glossary], src_lang: str = "auto",
                   style: str = "") -> List[Dict[str, str]]:
    src = "auto-detect (probably Japanese, English or Chinese)" if src_lang in ("", "auto") else src_lang
    sys_msg = SYSTEM_PROMPT.format(src=src, style=style or DEFAULT_STYLE)
    payload = []
    for i, it in enumerate(items, 1):
        d = {"id": i, "kind": it.kind}
        if it.speaker:
            d["speaker"] = it.speaker
        if it.prev:
            d["prev"] = it.prev
        d["text"] = it.mr.masked
        payload.append(d)
    parts = []
    if glossary is not None:
        block = glossary.prompt_block([it.mr.masked for it in items])
        if block:
            parts.append(block)
    parts.append("Items:\n" + json.dumps(payload, ensure_ascii=False))
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": "\n\n".join(parts)}]


# ---------------------------------------------------------------------------
# 응답 파싱
# ---------------------------------------------------------------------------
_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*(.*?)```", re.S)
_PAIR_RE = re.compile(r'"id"\s*:\s*"?(\d+)"?\s*,\s*"(?:text|translation|tr|t)"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)
_NUMBERED_RE = re.compile(r"^\s*(\d+)\s*[.):\-]\s*(.+?)\s*$")
_TEXT_KEYS = ("text", "translation", "tr", "t", "ko")


def _clean(raw: str) -> str:
    raw = _THINK_RE.sub("", raw)
    raw = re.sub(r"^.*?</think>", "", raw, flags=re.S | re.I)  # 여는 태그가 잘린 경우
    m = _FENCE_RE.search(raw)
    if m and ("[" in m.group(1) or "{" in m.group(1)):
        raw = m.group(1)
    return raw.strip()


def _from_json_value(val, expected: List[int]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    if isinstance(val, dict):
        for key in ("translations", "items", "result", "results"):
            if isinstance(val.get(key), (list, dict)):
                return _from_json_value(val[key], expected)
        if any(k in val for k in _TEXT_KEYS) and "id" in val:
            val = [val]
        else:
            for k, v in val.items():
                try:
                    i = int(k)
                except (TypeError, ValueError):
                    continue
                if isinstance(v, str):
                    out[i] = v
            return {i: t for i, t in out.items() if i in expected}
    if isinstance(val, list):
        if val and all(isinstance(x, str) for x in val) and len(val) == len(expected):
            return {i: t for i, t in zip(expected, val)}
        for pos, x in enumerate(val):
            if isinstance(x, dict):
                text = next((x[k] for k in _TEXT_KEYS if isinstance(x.get(k), str)), None)
                try:
                    i = int(x["id"]) if "id" in x else (expected[pos] if pos < len(expected) else None)
                except (TypeError, ValueError):
                    i = None
                if text is not None and i is not None:
                    out[i] = text
    return {i: t for i, t in out.items() if i in expected}


def parse_batch_response(raw: str, expected_ids: Iterable[int]) -> Dict[int, str]:
    """LLM 응답에서 ``{id: 번역문}`` 을 최대한 뽑아낸다. 못 뽑으면 빈 dict."""
    expected = list(expected_ids)
    if not raw:
        return {}
    text = _clean(raw)
    dec = json.JSONDecoder()
    best: Dict[int, str] = {}
    # 1) JSON 값 (앞뒤에 잡설이 있어도 첫 번째 [ 또는 { 부터 시도)
    for m in re.finditer(r"[\[{]", text):
        try:
            val, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        got = _from_json_value(val, expected)
        if got:
            best = got
            break
    if len(best) >= len(expected):
        return best
    # 2) 잘린/깨진 JSON 에서 "id": N, "text": "..." 쌍만 뽑기
    out: Dict[int, str] = {}
    for m in _PAIR_RE.finditer(text):
        i = int(m.group(1))
        if i not in expected:
            continue
        try:
            out[i] = json.loads('"' + m.group(2) + '"')
        except ValueError:
            out[i] = m.group(2).replace('\\"', '"').replace("\\n", "\n")
    if len(out) > len(best):
        best = out
    if best:
        return best
    # 3) "1. 번역" 형식의 번호 줄
    for line in text.splitlines():
        m = _NUMBERED_RE.match(line)
        if m and int(m.group(1)) in expected:
            out[int(m.group(1))] = m.group(2)
    return out


def parse_single_fallback(raw: str) -> Optional[str]:
    """항목이 하나일 때, JSON 이 아닌 평문 응답도 번역문으로 받아준다."""
    text = _clean(raw or "")
    if not text or text[0] in "[{":
        return None
    text = re.sub(r"^\s*1\s*[.):\-]\s*", "", text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text.strip() or None


# ---------------------------------------------------------------------------
# 번역 실행
# ---------------------------------------------------------------------------
@dataclass
class TranslateReport:
    total_units: int = 0
    unique: int = 0
    skipped_nontext: int = 0
    cached: int = 0
    glossary: int = 0
    pending: int = 0
    to_translate: int = 0  # limit 적용 후
    batches: int = 0
    translated: int = 0
    failed: int = 0
    calls: int = 0
    aborted: bool = False
    abort_reason: str = ""
    failed_sources: List[str] = field(default_factory=list)
    sample_messages: Optional[List[Dict[str, str]]] = None


class Translator:
    def __init__(self, client, tm: TranslationMemory, masker: Masker, glossary: Optional[Glossary] = None,
                 finalize: Optional[Callable[[str, str], Optional[str]]] = None,
                 batch_size: int = 8, max_chars: int = 3000, src_lang: str = "auto", style: str = "",
                 retries: int = 1, model: str = "", log: Optional[Callable[[str], None]] = None) -> None:
        self.client = client
        self.tm = tm
        self.masker = masker
        self.glossary = glossary or Glossary()
        self.finalize = finalize
        self.batch_size = max(1, batch_size)
        self.max_chars = max_chars
        self.src_lang = src_lang
        self.style = style or self.glossary.style
        self.retries = retries
        self.model = model
        self.log = log or (lambda s: print(s, file=sys.stderr))
        self.report = TranslateReport()

    # -- 준비 ---------------------------------------------------------
    def _collect(self, units: List[Unit], dry_run: bool) -> List[Item]:
        rep = self.report
        seen: Dict[str, Unit] = {}
        for u in units:
            seen.setdefault(u.source, u)
        rep.total_units = len(units)
        rep.unique = len(seen)
        pending: List[Item] = []
        for src, u in seen.items():
            mr = self.masker.mask(src)
            if not self.masker.needs_translation(mr):
                rep.skipped_nontext += 1
                continue
            if self.tm.has(src):
                rep.cached += 1
                continue
            g = self.glossary.exact(src)
            if g is not None:
                if not dry_run:
                    self.tm.put(src, g, origin="glossary")
                rep.glossary += 1
                continue
            pending.append(Item(src, u.kind, mr, self._speaker(u.speaker), u.prev))
        if not dry_run:
            self.tm.commit()
        rep.pending = len(pending)
        return pending

    def _speaker(self, name: str) -> str:
        if not name:
            return ""
        return self.tm.get(name) or name

    def _batches(self, items: List[Item]) -> List[List[Item]]:
        out: List[List[Item]] = []
        cur: List[Item] = []
        size = 0
        for it in items:
            if cur and (len(cur) >= self.batch_size or size + it.size > self.max_chars):
                out.append(cur)
                cur, size = [], 0
            cur.append(it)
            size += it.size
        if cur:
            out.append(cur)
        return out

    # -- 실행 ---------------------------------------------------------
    def run(self, units: List[Unit], limit: Optional[int] = None, dry_run: bool = False) -> TranslateReport:
        rep = self.report
        pending = self._collect(units, dry_run)
        if limit is not None:
            pending = pending[: max(0, limit)]
        rep.to_translate = len(pending)
        batches = self._batches(pending)
        rep.batches = len(batches)
        if batches:
            rep.sample_messages = build_messages(batches[0], self.glossary, self.src_lang, self.style)
        if dry_run:
            return rep
        consecutive_errors = 0
        for n, batch in enumerate(batches, 1):
            try:
                ok = self._process_batch(batch)
            except LLMUnreachable as e:
                rep.aborted = True
                rep.abort_reason = str(e)
                self.tm.commit()
                return rep
            except LLMError as e:
                consecutive_errors += 1
                rep.failed += len(batch)
                rep.failed_sources.extend(i.source for i in batch)
                self.log(f"[경고] LLM 오류: {e}")
                if consecutive_errors >= 3:
                    rep.aborted = True
                    rep.abort_reason = f"LLM 오류가 연속 3회 발생: {e}"
                    self.tm.commit()
                    return rep
                continue
            consecutive_errors = 0
            self.tm.commit()
            self.log(f"[{n}/{len(batches)}] 번역 {rep.translated}건 / 실패 {rep.failed}건")
        self.tm.commit()
        return rep

    def _call(self, items: List[Item]) -> Dict[int, str]:
        msgs = build_messages(items, self.glossary, self.src_lang, self.style)
        raw = self.client.complete(msgs)
        self.report.calls += 1
        got = parse_batch_response(raw, range(1, len(items) + 1))
        if not got and len(items) == 1:
            single = parse_single_fallback(raw)
            if single:
                got = {1: single}
        return got

    def _accept(self, item: Item, text: Optional[str]) -> bool:
        if not text:
            return False
        restored = self.masker.restore(text, item.mr)
        if restored is None:
            return False
        if self.finalize is not None:
            restored = self.finalize(item.source, restored)
            if restored is None:
                return False
        self.tm.put(item.source, restored, origin="llm", model=self.model)
        self.report.translated += 1
        return True

    def _process_batch(self, batch: List[Item]) -> bool:
        parsed = self._call(batch)
        unresolved = [it for i, it in enumerate(batch, 1) if not self._accept(it, parsed.get(i))]
        for it in unresolved:
            done = False
            for _ in range(max(1, self.retries)):
                got = self._call([it])
                if self._accept(it, got.get(1)):
                    done = True
                    break
            if not done:
                self.report.failed += 1
                self.report.failed_sources.append(it.source)
        return True
