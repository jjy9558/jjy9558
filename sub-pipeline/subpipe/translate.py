"""LLM translation with context windows, glossary, TM cache, robust parsing."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .llm import LLMUnavailable  # noqa: F401  (re-exported)
from .qa import Limits, char_count
from .segment import wrap_lines_force
from .srt import Cue

LANG_NAMES = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese",
              "es": "Spanish", "fr": "French", "de": "German", "auto": "the source language"}

TONES = {
    "formal": "Use polite speech (존댓말: ~요 / ~습니다). Keep it consistent across lines.",
    "casual": "Use informal speech (반말: ~야 / ~해 / ~다). Keep it consistent across lines.",
}
DOMAINS = {
    "general": "General-purpose subtitles; natural spoken Korean.",
    "variety": "Variety/entertainment show: lively, witty, keep jokes and reactions natural; short punchy lines.",
    "drama": "Drama/film dialogue: natural character voices, preserve emotion and subtext.",
    "it": "IT/technical content: keep standard technical terms (often leave English terms as-is or use common Korean tech terms).",
    "medical": "Medical content: use accurate standard Korean medical terminology; never guess drug names or doses.",
}
TONE_ALIASES = {"존댓말": "formal", "반말": "casual"}
DOMAIN_ALIASES = {"예능": "variety", "드라마": "drama", "IT": "it", "의학": "medical", "일반": "general"}


# ---------------------------------------------------------------- glossary
class Glossary:
    def __init__(self, pairs: Optional[Sequence[Tuple[str, str]]] = None):
        self.pairs = sorted(list(pairs or []), key=lambda p: -len(p[0]))

    @classmethod
    def load(cls, path: str) -> "Glossary":
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = f.read()
        if path.lower().endswith(".json"):
            return cls(list(json.loads(raw).items()))
        pairs = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("=>", "\t", "="):
                if sep in line:
                    a, b = line.split(sep, 1)
                    if a.strip() and b.strip():
                        pairs.append((a.strip(), b.strip()))
                    break
        return cls(pairs)

    @staticmethod
    def _pattern(term: str):
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .\-']*", term):
            return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?:'s|s)?(?![A-Za-z0-9])", re.I)
        return re.compile(re.escape(term))

    def relevant(self, text: str) -> List[Tuple[str, str]]:
        return [(t, k) for t, k in self.pairs if self._pattern(t).search(text)]

    def violations(self, src: str, draft: str) -> List[Tuple[str, str]]:
        return [(t, k) for t, k in self.relevant(src) if k not in draft]


# ---------------------------------------------------------------- TM cache
class TranslationMemory:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.data: Dict[str, str] = {}
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self.data = {str(k): str(v) for k, v in d.items()}
            except (OSError, ValueError):
                self.data = {}

    @staticmethod
    def key(sig: str, text: str) -> str:
        return hashlib.sha1((sig + "\x00" + text).encode("utf-8")).hexdigest()

    def get(self, k: str) -> Optional[str]:
        return self.data.get(k)

    def put(self, k: str, v: str) -> None:
        self.data[k] = v

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=0)
        os.replace(tmp, self.path)


# ---------------------------------------------------------------- parsing
def _clean(t) -> str:
    return " ".join(str(t).split())


def parse_batch_output(raw: str, expected_ids: Sequence[int]) -> Dict[int, str]:
    """Extract {id: text} from LLM output; tolerant of fences, prose, bad JSON."""
    expected = list(expected_ids)
    want = set(expected)
    out: Dict[int, str] = {}
    if not raw:
        return out
    s = re.sub(r"```[a-zA-Z]*", "", raw).strip()

    def accept(i, t):
        try:
            i = int(i)
        except (TypeError, ValueError):
            return
        if i in want and i not in out and t is not None and _clean(t):
            out[i] = _clean(t)

    def from_obj(obj) -> bool:
        if isinstance(obj, dict):
            for key in ("translations", "items", "result", "results", "lines"):
                if isinstance(obj.get(key), (list, dict)):
                    return from_obj(obj[key])
            if all(re.fullmatch(r"\d+", str(k)) for k in obj) and obj:
                for k, v in obj.items():
                    accept(k, v)
                return True
            if "id" in obj:
                accept(obj.get("id"), obj.get("text", obj.get("translation")))
                return True
            return False
        if isinstance(obj, list):
            if obj and all(isinstance(x, str) for x in obj) and len(obj) == len(expected):
                for i, t in zip(expected, obj):  # positional fallback
                    accept(i, t)
                return True
            for x in obj:
                if isinstance(x, dict):
                    from_obj(x)
            return True
        return False

    candidates = [s]
    for a, b in (("[", "]"), ("{", "}")):
        i, j = s.find(a), s.rfind(b)
        if 0 <= i < j:
            candidates.append(s[i:j + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if from_obj(obj) and out:
            return out
    # regex fallback for broken JSON (truncated / trailing commas / junk)
    for m in re.finditer(r'"id"\s*:\s*"?(\d+)"?\s*,\s*"(?:text|translation)"\s*:\s*"((?:[^"\\]|\\.)*)"', s):
        try:
            t = json.loads('"%s"' % m.group(2))
        except ValueError:
            t = m.group(2)
        accept(m.group(1), t)
    if out:
        return out
    # numbered-lines fallback: "1. text" / "2) text" / "3: text"
    for m in re.finditer(r"^\s*(\d+)\s*[.):\]-]\s*(.+?)\s*$", s, re.M):
        accept(m.group(1), m.group(2))
    return out


# ---------------------------------------------------------------- prompts
def _flat(text: str) -> str:
    return " ".join(text.split())


class Translator:
    def __init__(self, client, src_lang: str = "auto", tgt_lang: str = "ko", tone: str = "formal",
                 domain: str = "general", glossary: Optional[Glossary] = None,
                 limits: Optional[Limits] = None, tm: Optional[TranslationMemory] = None,
                 context: int = 2, batch_size: int = 15, retries: int = 2,
                 on_progress: Optional[Callable[[List[Optional[str]]], None]] = None):
        self.client = client
        self.src, self.tgt = src_lang, tgt_lang
        self.tone = TONE_ALIASES.get(tone, tone)
        self.domain = DOMAIN_ALIASES.get(domain, domain)
        if self.tone not in TONES:
            raise ValueError("tone must be formal|casual (존댓말|반말)")
        if self.domain not in DOMAINS:
            raise ValueError("domain must be one of %s" % ", ".join(DOMAINS))
        self.glossary = glossary or Glossary()
        self.limits = limits or Limits()
        self.tm = tm or TranslationMemory(None)
        self.context, self.batch_size, self.retries = context, max(1, batch_size), max(0, retries)
        self.on_progress = on_progress

    # -- prompt
    def system_prompt(self, gloss: List[Tuple[str, str]]) -> str:
        L = self.limits
        s = [
            "You are a professional subtitle translator (%s -> %s)." % (
                LANG_NAMES.get(self.src, self.src), LANG_NAMES.get(self.tgt, self.tgt)),
            "Rules:",
            "- Translate every numbered item separately. Never merge, split, skip or reorder items.",
            "- Output exactly one translation per input id.",
            "- Subtitle style: concise and natural; at most %d characters per line; reading speed <= %g chars/sec. "
            "Each item gives max_chars (total characters it may use); stay within it." % (L.max_chars, L.cps),
            "- Keep numbers, units, and proper nouns accurate. Do not invent content.",
            "- Tone: " + TONES[self.tone],
            "- Domain: " + DOMAINS[self.domain],
            "- CONTEXT sections are for understanding only. Do NOT translate them.",
        ]
        if gloss:
            s.append("- Glossary (the translation MUST use these exact forms):")
            s += ["    %s => %s" % (t, k) for t, k in gloss]
        s.append('- Reply with ONLY a JSON array: [{"id": 1, "text": "..."}, ...]. No commentary.')
        return "\n".join(s)

    def user_prompt(self, items: List[dict], before: List[str], after: List[str]) -> str:
        parts = []
        if before:
            parts.append("### CONTEXT BEFORE (do not translate)\n" + "\n".join(before))
        parts.append("### TRANSLATE\n" + json.dumps(items, ensure_ascii=False))
        if after:
            parts.append("### CONTEXT AFTER (do not translate)\n" + "\n".join(after))
        return "\n\n".join(parts)

    def _sig(self, text: str) -> str:
        g = "|".join("%s=%s" % p for p in self.glossary.relevant(text))
        return "%s>%s|%s|%s|%s" % (self.src, self.tgt, self.tone, self.domain, g)

    def _items(self, cues: List[Cue], idxs: List[int]) -> List[dict]:
        out = []
        for n, i in enumerate(idxs, 1):
            mc = max(4, int(cues[i].duration / 1000.0 * self.limits.cps))
            out.append({"id": n, "text": _flat(cues[i].text), "max_chars": mc})
        return out

    def _call(self, cues: List[Cue], idxs: List[int], temperature: float) -> Dict[int, str]:
        items = self._items(cues, idxs)
        lo, hi = idxs[0], idxs[-1]
        before = [_flat(c.text) for c in cues[max(0, lo - self.context):lo]]
        after = [_flat(c.text) for c in cues[hi + 1:hi + 1 + self.context]]
        gloss = self.glossary.relevant(" ".join(it["text"] for it in items))
        msgs = [{"role": "system", "content": self.system_prompt(gloss)},
                {"role": "user", "content": self.user_prompt(items, before, after)}]
        raw = self.client.chat(msgs, temperature=temperature)
        got = parse_batch_output(raw, list(range(1, len(idxs) + 1)))
        if not got and len(idxs) == 1:  # single line: accept plain text answer
            plain = raw.strip().strip('"').strip()
            if plain and not re.search(r'[\[\]{}]', plain) and "\n" not in plain:
                got = {1: _clean(plain)}
        return got

    # -- main
    def translate(self, cues: List[Cue]) -> List[Optional[str]]:
        """Return one flattened translation (or None if failed) per cue, same order."""
        res: List[Optional[str]] = [None] * len(cues)
        pending: List[int] = []
        for i, c in enumerate(cues):
            if not c.text.strip():
                res[i] = ""
                continue
            hit = self.tm.get(TranslationMemory.key(self._sig(c.text), _flat(c.text)))
            if hit is not None:
                res[i] = hit
            else:
                pending.append(i)
        if self.on_progress:
            self.on_progress(res)
        try:
            for b in range(0, len(pending), self.batch_size):
                idxs = pending[b:b + self.batch_size]
                got = self._call(cues, idxs, 0.2)
                for n, i in enumerate(idxs, 1):
                    t = got.get(n)
                    for attempt in range(self.retries):
                        if t:
                            break
                        t = self._call(cues, [i], 0.2 + 0.2 * attempt).get(1)
                    if t:
                        res[i] = t
                        self.tm.put(TranslationMemory.key(self._sig(cues[i].text), _flat(cues[i].text)), t)
                self.tm.save()
                if self.on_progress:
                    self.on_progress(res)
        finally:
            self.tm.save()  # progress is kept even if the server dies mid-run
        return res

    def condense(self, cues: List[Cue], drafts: List[Optional[str]]) -> List[Optional[str]]:
        """Optional: ask the LLM to shorten lines above the CPS limit. Still-too-fast lines stay flagged."""
        out = list(drafts)
        for i, (c, d) in enumerate(zip(cues, drafts)):
            if not d or c.duration <= 0:
                continue
            limit = int(c.duration / 1000.0 * self.limits.cps)
            if char_count(d) <= limit:
                continue
            for attempt in range(self.retries):
                msgs = [{"role": "system", "content": "You shorten Korean subtitles without changing meaning, numbers or names. "
                         "Tone: " + TONES[self.tone] + ' Reply with ONLY JSON: [{"id": 1, "text": "..."}]'},
                        {"role": "user", "content": "### TRANSLATE\n" + json.dumps(
                            [{"id": 1, "text": d, "max_chars": limit, "original": _flat(c.text)}], ensure_ascii=False)}]
                new = parse_batch_output(self.client.chat(msgs, temperature=0.3), [1]).get(1)
                if new and char_count(new) < char_count(out[i] or d):
                    out[i] = new
                    if char_count(new) <= limit:
                        break
        return out


def format_draft(text: Optional[str], limits: Limits) -> str:
    if not text:
        return ""
    return "\n".join(wrap_lines_force(text, limits.max_chars, limits.max_lines))
