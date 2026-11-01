"""Count candidate vocabulary words across captions and keep one example per word."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from .subtitles import Caption
from .text import STOPWORDS, WORD_RE, group_key, lemmatize, token_base


@dataclass
class Entry:
    """One vocabulary item (a lemma) or one possible proper name."""

    key: str  # group key for words (see text.group_key); lowercase form for names
    word: str  # display text (same as key for words; most common form for names)
    is_name: bool = False
    count: int = 0
    first_start: float = 0.0
    first_source: str = ""
    example: str = ""  # the caption line where the word first appeared
    forms: Counter = field(default_factory=Counter)  # surface forms seen
    meaning: str = ""


@dataclass
class Analysis:
    words: List[Entry]
    names: List[Entry]
    caption_count: int


_SENTENCE_END = ".!?\"“”'"


def headword(key: str, forms: Counter) -> str:
    """Pick the displayed word for a group. It is always a form seen in the subtitles.

    1. the key itself, if that exact form appeared (e.g. "stop" in "stop"/"stopped");
    2. the key plus a silent "e", if that form appeared (e.g. "decide" for "decid");
    3. otherwise the most frequent surface form (e.g. "decided" when only it appeared).
    """
    if key in forms:
        return key
    if key + "e" in forms:
        return key + "e"
    return forms.most_common(1)[0][0]


def is_sentence_initial(text: str, pos: int) -> bool:
    """True if the token at pos starts a line or follows sentence-ending punctuation."""
    prefix = text[:pos].rstrip()
    return prefix == "" or prefix[-1] in _SENTENCE_END


def _tally(table: Dict[str, Entry], key: str, base: str, caption: Caption, is_name: bool) -> None:
    entry = table.get(key)
    if entry is None:
        entry = Entry(
            key=key,
            word=key,
            is_name=is_name,
            first_start=caption.start,
            first_source=caption.source,
            example=caption.text,
        )
        table[key] = entry
    entry.count += 1
    entry.forms[base] += 1


def analyze(captions: Iterable[Caption], known: Iterable[str] = ()) -> Analysis:
    """Build vocabulary entries from captions.

    Names: a word that appears capitalized mid-sentence and never appears in lowercase
    anywhere in the subtitles is a possible proper name; it goes to a separate list.
    Words: lowercased, lemmatized, with stopwords and known words removed.
    Entries keep first-appearance order, then sort by frequency (stable).
    """
    caps = list(captions)
    known_set = {k.lower() for k in known}

    occurrences = []  # (caption index, base token, sentence-initial?)
    lower_seen = set()
    mid_capitalized = set()
    for index, cap in enumerate(caps):
        for m in WORD_RE.finditer(cap.text):
            base = token_base(m.group(0))
            if base is None or len(base) < 2:
                continue
            initial = is_sentence_initial(cap.text, m.start())
            if base[0].islower():
                lower_seen.add(base.lower())
            elif not base.isupper() and not initial:
                mid_capitalized.add(base.lower())
            occurrences.append((index, base, initial))

    name_keys = {
        w for w in mid_capitalized
        if w not in lower_seen and w not in STOPWORDS and w not in known_set
    }

    words: Dict[str, Entry] = {}
    names: Dict[str, Entry] = {}
    for index, base, _initial in occurrences:
        cap = caps[index]
        low = base.lower()
        if low in known_set:
            continue
        if low in name_keys and base[0].isupper() and not base.isupper():
            _tally(names, low, base, cap, is_name=True)
            continue
        if low in STOPWORDS:
            continue
        lemma = lemmatize(low)
        if len(lemma) < 2 or lemma in STOPWORDS or lemma in known_set:
            continue
        _tally(words, group_key(low), low, cap, is_name=False)

    for entry in words.values():
        entry.word = headword(entry.key, entry.forms)
    for entry in names.values():
        entry.word = entry.forms.most_common(1)[0][0]

    return Analysis(
        words=sorted(words.values(), key=lambda e: -e.count),
        names=sorted(names.values(), key=lambda e: -e.count),
        caption_count=len(caps),
    )
