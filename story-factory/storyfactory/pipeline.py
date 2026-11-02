"""seeds -> scrub -> draft -> lint -> report 를 한 번에. 같은 입력 + 같은 시드면 같은 결과."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from . import draft as draft_mod
from . import jarvis, lint as lint_mod, report as report_mod
from .model import Card, Draft
from .scrub import Scrubber, sensitive_reasons
from .seeds import Sources, collect

MARKER = ".story-factory"


@dataclass
class Options:
    sources: Sources
    out_dir: Path
    now: date
    seed: int = 17
    names: list = field(default_factory=list)
    places: list = field(default_factory=list)
    max_cards: int = 10
    max_drafts: int = 6
    candidates: int = 2
    client: object = None            # LLMClient 또는 None
    ai_voice: bool = True
    ai_image: bool = True
    linter_dir: str | None = None
    history: Path | None = None
    jarvis: dict | None = None       # {url, device, token, opener}
    do_lint: bool = True


@dataclass
class Result:
    run_dir: Path
    report_path: Path
    cards: list
    excluded: list
    drafts: list
    candidates: list
    warnings: list
    jarvis_msg: str = ""


def scrub_cards(cards: list[Card], scrubber: Scrubber) -> tuple[list[Card], list[tuple]]:
    """(통과한 가명화 카드, [(카드id, 종류, [사유])]). 제외 사유에는 원문을 담지 않는다."""
    ok, excluded = [], []
    for c in cards:
        blob = c.text_blob()
        scrubber.learn(blob)
        reasons = sensitive_reasons(blob, scrubber.originals)
        if reasons:
            excluded.append((c.id, c.kind, reasons))
            continue
        clean = replace(c, subject=scrubber.scrub(c.subject),
                        facts={k: (scrubber.scrub(v) if isinstance(v, str) else v) for k, v in c.facts.items()})
        post = sensitive_reasons(clean.text_blob(), scrubber.originals)
        if scrubber.leaks(clean.text_blob()):
            post.append("leak")
        if post:
            excluded.append((c.id, c.kind, post))
            continue
        ok.append(clean)
    return ok, excluded


def _draft_ok(d: Draft, scrubber: Scrubber) -> list[str]:
    text = d.all_text()
    reasons = sensitive_reasons(text, scrubber.originals)
    if scrubber.leaks(text):
        reasons.append("leak")
    return reasons


def build_drafts(cards: list[Card], scrubber: Scrubber, opts: Options) -> tuple[list[Draft], list[tuple]]:
    drafts, excluded = [], []
    for c in cards[:opts.max_drafts]:
        d = draft_mod.make_draft(c, opts.client, opts.seed, opts.ai_voice, opts.ai_image)
        bad = _draft_ok(d, scrubber)
        if bad and d.used_llm:
            d = draft_mod.make_draft(c, None, opts.seed, opts.ai_voice, opts.ai_image)   # 템플릿으로 되돌림
            bad = _draft_ok(d, scrubber)
        if bad:
            excluded.append((c.id, c.kind, bad))
            continue
        drafts.append(d)
    return drafts, excluded


def write_draft(run_dir: Path, d: Draft, seed: int) -> None:
    (run_dir / (d.card_id + ".txt")).write_text(d.script, encoding="utf-8")
    (run_dir / (d.card_id + ".meta.json")).write_text(
        json.dumps(d.meta(seed), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prepare_run_dir(out_dir: Path, now: date) -> Path:
    run_dir = Path(out_dir) / now.isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)
    if (run_dir / MARKER).exists():           # 우리가 만든 폴더만 비운다
        for p in list(run_dir.glob("*.txt")) + list(run_dir.glob("*.meta.json")):
            p.unlink()
    (run_dir / MARKER).write_text("story-factory 산출물 폴더\n", encoding="utf-8")
    return run_dir


def run(opts: Options) -> Result:
    cards, warnings = collect(opts.sources, opts.now, opts.max_cards)
    scrubber = Scrubber(opts.names, opts.places, opts.seed)
    clean, excluded = scrub_cards(cards, scrubber)
    drafts, ex2 = build_drafts(clean, scrubber, opts)
    excluded += ex2
    run_dir = _prepare_run_dir(opts.out_dir, opts.now)
    by_id = {c.id: c for c in clean}
    for d in drafts:
        write_draft(run_dir, d, opts.seed)

    linter = lint_mod.Linter(lint_mod.find_linter_dir(opts.linter_dir) if opts.do_lint else None, run_dir)
    dmap = {d.card_id: d for d in drafts}

    def rewrite(cid: str, best: str) -> None:
        d = dmap[cid]
        if best and best != d.title:
            d.title = best
            write_draft(run_dir, d, opts.seed)

    reports = lint_mod.lint_all(linter, run_dir, [d.card_id for d in drafts], opts.history, rewrite)
    ranked = sorted(drafts, key=lambda d: (tuple(-x for x in reports[d.card_id].rank_key()),
                                           -by_id[d.card_id].priority, d.card_id))
    top = ranked[:opts.candidates]
    cands = [(d, reports[d.card_id], by_id[d.card_id]) for d in top]
    if opts.do_lint and not linter.available:
        warnings.append("shorts-linter 를 찾지 못해 promise/titles/disclose/similar 를 생략했습니다 (SHORTSLINT_DIR 또는 --linter-dir)")
    elif any(not r.available for r in reports.values()):
        warnings.append("일부 후보는 shorts-linter 실행에 실패해 린트를 생략했습니다")

    jarvis_msg = ""
    if opts.jarvis is not None:
        summary = {"date": opts.now.isoformat(), "seed": opts.seed, "dir": str(run_dir),
                   "candidates": [{"id": d.card_id, "title": d.title, "file": d.card_id + ".txt"} for d, _, _ in cands]}
        _, jarvis_msg = jarvis.submit(summary, **opts.jarvis)

    run_info = {"date": opts.now.isoformat(), "seed": opts.seed, "cards": len(cards), "drafts": len(drafts),
                "llm": any(d.used_llm for d in drafts), "linter": linter.available}
    md = report_mod.render(run_info, cands, excluded, warnings, jarvis_msg)
    report_path = run_dir / "report.md"
    report_path.write_text(md, encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps({
        **run_info, "excluded": [{"card": c, "kind": k, "reasons": r} for c, k, r in excluded],
        "candidates": [d.card_id for d, _, _ in cands], "warnings": warnings}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return Result(run_dir, report_path, clean, excluded, drafts, cands, warnings, jarvis_msg)
