"""Write vocab.md (Markdown table) and anki.tsv (Anki-importable, tab-separated)."""
from __future__ import annotations

import html
import os
from typing import List, Sequence, Tuple

from .text import group_key, highlight
from .vocab import Analysis, Entry

NAMES_LIMIT = 30


def fmt_time(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def highlight_entry(entry: Entry, sentence: str, before: str, after: str, escape=lambda s: s) -> str:
    """Wrap the surface forms in sentence that belong to entry (e.g. "decided" for "decide").

    Only the tokens actually used in the sentence are wrapped, so the bold text is the
    real inflected form, even when the headword is a different form.
    """
    if entry.is_name:
        key = entry.key
        return highlight(sentence, lambda base: base.lower() == key, before, after, escape)
    key = entry.key
    return highlight(sentence, lambda base: group_key(base) == key, before, after, escape)


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _tsv_field(text: str) -> str:
    return " ".join(text.replace("\t", " ").replace("\r", " ").replace("\n", " ").split())


def render_markdown(
    analysis: Analysis,
    entries: Sequence[Entry],
    sources: Sequence[str],
    generated_at: str,
) -> str:
    lines = [
        "# 단어장",
        "",
        f"- 자막 파일: {', '.join(sources) if sources else '-'}",
        f"- 생성: {generated_at}",
        f"- 자막 줄 {analysis.caption_count}개 · 후보 단어 {len(analysis.words)}개 · "
        f"표시 {len(entries)}개 (빈도순)",
        "",
        "| # | 단어 | 뜻 | 빈도 | 첫 등장 | 예문 |",
        "|--:|:--|:--|--:|:--:|:--|",
    ]
    for number, entry in enumerate(entries, 1):
        example = highlight_entry(entry, entry.example, "**", "**", escape=_md_cell)
        lines.append(
            f"| {number} | {entry.word} | {_md_cell(entry.meaning)} | {entry.count} "
            f"| {fmt_time(entry.first_start)} | {example} |"
        )
    if not entries:
        lines.append("| | 표시할 단어가 없습니다 | | | | |")

    lines += [
        "",
        "## 고유명사 후보",
        "",
        "문장 중간에서 대문자로 쓰이고 소문자 형태가 자막에 없는 단어입니다. 참고용입니다.",
        "",
    ]
    if analysis.names:
        lines += [
            "| 이름 | 빈도 | 첫 등장 | 예문 |",
            "|:--|--:|:--:|:--|",
        ]
        for entry in analysis.names[:NAMES_LIMIT]:
            example = highlight_entry(entry, entry.example, "**", "**", escape=_md_cell)
            lines.append(
                f"| {entry.word} | {entry.count} | {fmt_time(entry.first_start)} | {example} |"
            )
    else:
        lines.append("후보가 없습니다.")
    return "\n".join(lines) + "\n"


def render_anki_tsv(entries: Sequence[Entry]) -> str:
    """Rows: word, meaning, example (word wrapped in <b></b>), timestamp.

    The '#separator' and '#html' header lines let Anki detect tab separation and HTML.
    """
    rows = ["#separator:tab", "#html:true"]
    escape = lambda s: html.escape(s, quote=False)  # noqa: E731
    for entry in entries:
        example = highlight_entry(entry, entry.example, "<b>", "</b>", escape=escape)
        fields = (
            entry.word,
            escape(entry.meaning),
            example,
            fmt_time(entry.first_start),
        )
        rows.append("\t".join(_tsv_field(f) for f in fields))
    return "\n".join(rows) + "\n"


def write_outputs(
    out_dir: str,
    analysis: Analysis,
    entries: Sequence[Entry],
    sources: Sequence[str],
    generated_at: str,
) -> Tuple[str, str]:
    """Write vocab.md and anki.tsv into out_dir. Returns (markdown path, tsv path)."""
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "vocab.md")
    tsv_path = os.path.join(out_dir, "anki.tsv")
    with open(md_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(render_markdown(analysis, entries, sources, generated_at))
    with open(tsv_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(render_anki_tsv(entries))
    return md_path, tsv_path
