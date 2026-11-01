"""Known-word list: words the learner already knows and wants skipped."""
from __future__ import annotations

import os
import re
from typing import Iterable, List, Set

_VALID_WORD = re.compile(r"[a-z]+(?:['-][a-z]+)*")


def normalize_word(raw: str) -> str:
    """Lowercase a word and strip surrounding punctuation and whitespace."""
    return raw.strip().strip(".,;:!?\"'()[]{}").replace("’", "'").lower()


def load_known(path: str) -> Set[str]:
    """Load known words, one per line. Blank lines and lines starting with '#' are ignored.

    A missing file means an empty known list.
    """
    words: Set[str] = set()
    if not os.path.isfile(path):
        return words
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            word = normalize_word(stripped)
            if word:
                words.add(word)
    return words


def add_known_words(path: str, words: Iterable[str]) -> List[str]:
    """Append new words to the known-word file. Returns the words actually added."""
    existing = load_known(path)
    to_add: List[str] = []
    for raw in words:
        word = normalize_word(raw)
        if not word or not _VALID_WORD.fullmatch(word):
            continue
        if word in existing or word in to_add:
            continue
        to_add.append(word)
    if to_add:
        needs_newline = False
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                needs_newline = fh.read(1) != b"\n"
        with open(path, "a", encoding="utf-8") as fh:
            if needs_newline:
                fh.write("\n")
            for word in to_add:
                fh.write(word + "\n")
    return to_add
