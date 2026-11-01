"""수동 검수용 CSV 내보내기/가져오기 (엑셀 호환: UTF-8 BOM)."""

from __future__ import annotations

import csv
from typing import Callable, Dict, List, Optional, Tuple

from .masking import Masker
from .tm import TranslationMemory
from .units import Unit, source_hash

FIELDS = ["hash", "kind", "file", "context", "source", "translation", "origin"]


def export_csv(path, units: List[Unit], tm: TranslationMemory, only_untranslated: bool = False) -> int:
    rows: Dict[str, dict] = {}
    for u in units:
        if u.source in rows:
            continue
        ctx = (f"[{u.speaker}] " if u.speaker else "") + (f"(이전) {u.prev}" if u.prev else "")
        rows[u.source] = {"hash": source_hash(u.source), "kind": u.kind, "file": u.file, "context": ctx,
                          "source": u.source, "translation": "", "origin": ""}
    origins = {s: o for s, _, o in tm.items()}
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for src, row in rows.items():
            tr = tm.get(src)
            if tr is not None:
                row["translation"] = tr
                row["origin"] = origins.get(src, "")
            if only_untranslated and tr is not None:
                continue
            w.writerow(row)
            n += 1
    return n


def import_csv(path, tm: TranslationMemory, masker: Optional[Masker] = None,
               finalize: Optional[Callable[[str, str], Optional[str]]] = None) -> Tuple[int, List[str]]:
    """번역 열이 채워진 행을 manual 로 저장. 제어코드가 어긋난 행은 경고만 남기고 건너뛴다."""
    imported = 0
    warnings: List[str] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for lineno, row in enumerate(csv.DictReader(f), 2):
            src = row.get("source") or ""
            tr = row.get("translation") or ""
            if not src or not tr.strip():
                continue
            if masker is not None and not masker.check(src, tr):
                warnings.append(f"{lineno}행: 제어코드/변수가 원문과 다릅니다. 건너뜀: {src[:40]!r}")
                continue
            if finalize is not None:
                tr2 = finalize(src, tr)
                if tr2 is None:
                    warnings.append(f"{lineno}행: 번역문이 올바르지 않습니다. 건너뜀: {src[:40]!r}")
                    continue
                tr = tr2
            if tm.put(src, tr, origin="manual", overwrite=True):
                imported += 1
    tm.commit()
    return imported, warnings
