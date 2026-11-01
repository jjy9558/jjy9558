"""Tokenizing, stopwords and rule-based lemmatization for English subtitle text."""
from __future__ import annotations

import re
from typing import Callable, Optional

# A token is a run of ASCII letters, optionally with apostrophes ("don't", "John's").
# A digit next to the letters ("3rd", "2020") disqualifies it, so numbers are skipped.
WORD_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]+(?:['’][A-Za-z]+)*(?![A-Za-z0-9])")

# Function words and fillers. Known-word list is separate and user-editable.
STOPWORDS = frozenset(
    """
    a an the and or but if then else so as of to in on at by for with from up down out off
    over under into onto about above below after before between through during without within
    against among along around across behind beyond near than too very just only also even
    still yet
    i me my mine myself we us our ours ourselves you your yours yourself yourselves he him his
    himself she her hers herself it its itself they them their theirs themselves
    this that these those there here what which who whom whose when where why how
    is am are was were be been being do does did doing done have has had having
    will would shall should can could may might must
    not no nor yes yeah yep nope oh ah uh um umm hmm huh mm mhm okay ok
    all any both each few more most other some such own same
    s t ll re ve m d
    don won isn aren wasn weren doesn didn haven hasn hadn couldn wouldn shouldn
    let lets gonna wanna gotta kinda sorta like
    now again ever never
    zero one two three four five six seven eight nine ten eleven twelve twenty thirty forty
    fifty hundred thousand million
    """.split()
)

# Irregular forms that suffix rules cannot handle.
IRREGULAR = {
    "was": "be", "were": "be", "is": "be", "are": "be", "am": "be", "been": "be", "being": "be",
    "has": "have", "had": "have", "having": "have",
    "does": "do", "did": "do", "done": "do", "doing": "do",
    "went": "go", "gone": "go", "goes": "go", "going": "go",
    "made": "make", "got": "get", "gotten": "get",
    "took": "take", "taken": "take", "came": "come",
    "saw": "see", "seen": "see", "knew": "know", "known": "know",
    "thought": "think", "told": "tell", "said": "say", "found": "find",
    "gave": "give", "given": "give", "left": "leave", "felt": "feel", "kept": "keep",
    "began": "begin", "begun": "begin", "wrote": "write", "written": "write",
    "spoke": "speak", "spoken": "speak", "ran": "run", "brought": "bring", "bought": "buy",
    "caught": "catch", "taught": "teach", "understood": "understand",
    "ate": "eat", "eaten": "eat", "drove": "drive", "driven": "drive",
    "drew": "draw", "drawn": "draw", "wore": "wear", "worn": "wear",
    "chose": "choose", "chosen": "choose", "fell": "fall", "fallen": "fall",
    "forgot": "forget", "forgotten": "forget", "won": "win", "lost": "lose",
    "sold": "sell", "held": "hold", "meant": "mean", "met": "meet", "paid": "pay",
    "sat": "sit", "stood": "stand", "heard": "hear", "built": "build", "sent": "send",
    "spent": "spend", "led": "lead", "using": "use",
    "children": "child", "people": "person", "men": "man", "women": "woman",
    "feet": "foot", "teeth": "tooth", "mice": "mouse",
    "wolves": "wolf", "knives": "knife", "halves": "half",
}

# Words that look inflected but are base forms.
NO_INFLECT = frozenset(
    "news always perhaps sometimes series species christmas chaos physics politics "
    "mathematics economics athletics means".split()
)
NO_STRIP_ING = frozenset(
    "thing something nothing everything anything morning evening during string spring "
    "sibling darling ceiling wedding pudding".split()
)
NO_STRIP_ED = frozenset("hundred sacred naked beloved ragged wicked kindred".split())


def token_base(raw: str) -> Optional[str]:
    """Return the word a raw token stands for, or None for contractions to drop.

    Possessive "'s" is removed ("John's" -> "John"). Other contractions
    ("don't", "I'm") return None because they are function words anyway.
    """
    text = raw.replace("’", "'")
    if "'" not in text:
        return text
    m = re.fullmatch(r"([A-Za-z]+)'s", text, flags=re.IGNORECASE)
    return m.group(1) if m else None


def _restore_stem(stem: str) -> str:
    """Turn a stem left after stripping -ing/-ed into a stem.

    Only undoes doubled consonants (stopped -> stop, running -> run). It never adds a
    trailing "e": whether "decid" is really "decide" depends on which forms appear in
    the subtitles, so that decision is made in group_key()/headword instead.
    """
    if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in "aeioulsfz":
        return stem[:-1]
    return stem


def group_key(word: str) -> str:
    """Canonical grouping key: the lemma with a trailing silent "e" removed.

    "decide", "decided", "deciding" all map to "decid"; "make", "making", "made" to
    "mak". Keys are only used internally to group occurrences; the displayed headword
    is chosen from forms that really appear in the subtitles (see vocab.headword).
    """
    lemma = lemmatize(word)
    if len(lemma) > 3 and lemma.endswith("e"):
        return lemma[:-1]
    return lemma


def lemmatize(word: str) -> str:
    """Approximate base form of an English word using suffix rules and a small table."""
    w = word.lower()
    if w in IRREGULAR:
        return IRREGULAR[w]
    if w in NO_INFLECT or len(w) <= 3:
        return w
    if w.endswith("ing"):
        if len(w) > 4 and w not in NO_STRIP_ING and len(w) - 3 >= 3:
            return _restore_stem(w[:-3])
        return w
    if w.endswith("ed"):
        if len(w) > 4 and not w.endswith("eed") and w not in NO_STRIP_ED:
            if w.endswith("ied"):
                return w[:-3] + "y"  # tried -> try
            return _restore_stem(w[:-2])
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"  # stories -> story
    if w.endswith(("sses", "shes", "ches", "xes")) and len(w) > 4:
        return w[:-2]  # classes -> class, watches -> watch, boxes -> box
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]  # cats -> cat, houses -> house
    return w


def highlight(
    sentence: str,
    predicate: Callable[[str], bool],
    before: str,
    after: str,
    escape: Callable[[str], str] = lambda s: s,
) -> str:
    """Wrap every word token whose base form satisfies predicate in before/after.

    Text outside matched words is passed through escape (e.g. HTML escaping).
    """
    out = []
    pos = 0
    for m in WORD_RE.finditer(sentence):
        out.append(escape(sentence[pos:m.start()]))
        token = m.group(0)
        base = token_base(token)
        if base is not None and predicate(base):
            out.append(before + escape(token) + after)
        else:
            out.append(escape(token))
        pos = m.end()
    out.append(escape(sentence[pos:]))
    return "".join(out)
