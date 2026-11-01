"""용어집: 고정 번역(terms) 과 그대로 두기(keep).

JSON 형식::

    {"terms": {"Alice": "앨리스"}, "keep": ["HP", "MP"],
     "style": "...", "mask_patterns": ["정규식", ...]}

텍스트 형식(.txt/.tsv): 한 줄에 ``원문<TAB>번역`` 또는 ``원문 = 번역``,
번역이 없으면 그대로 두기. ``#`` 으로 시작하는 줄은 주석.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class Glossary:
    def __init__(self, terms: Optional[Dict[str, str]] = None, keep: Optional[List[str]] = None,
                 style: str = "", mask_patterns: Optional[List[str]] = None) -> None:
        self.terms: Dict[str, str] = dict(terms or {})
        self.keep: List[str] = list(keep or [])
        self.style = style
        self.mask_patterns: List[str] = list(mask_patterns or [])

    @classmethod
    def load(cls, path) -> "Glossary":
        p = Path(path)
        text = p.read_text(encoding="utf-8-sig")
        if p.suffix.lower() == ".json":
            data = json.loads(text)
            return cls(data.get("terms"), data.get("keep"), data.get("style", ""), data.get("mask_patterns"))
        g = cls()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("\t", "=>", "="):
                if sep in line:
                    src, dst = [x.strip() for x in line.split(sep, 1)]
                    break
            else:
                src, dst = line, ""
            if not src:
                continue
            if dst:
                g.terms[src] = dst
            else:
                g.keep.append(src)
        return g

    def exact(self, source: str) -> Optional[str]:
        """문자열 전체가 용어집 항목과 같으면 그 번역(또는 원문)을 돌려준다."""
        key = source.strip()
        if key in self.terms:
            return source.replace(key, self.terms[key], 1)
        if key in self.keep:
            return source
        return None

    def relevant(self, texts: List[str]) -> Tuple[Dict[str, str], List[str]]:
        low = "\n".join(texts).lower()
        terms = {k: v for k, v in self.terms.items() if k.lower() in low}
        keep = [k for k in self.keep if k.lower() in low]
        return terms, keep

    def prompt_block(self, texts: List[str]) -> str:
        terms, keep = self.relevant(texts)
        if not terms and not keep:
            return ""
        lines = ["Glossary (mandatory):"]
        for k, v in terms.items():
            lines.append(f"- {k} => {v}")
        for k in keep:
            lines.append(f"- {k} => (keep exactly as is, do not translate)")
        return "\n".join(lines)
