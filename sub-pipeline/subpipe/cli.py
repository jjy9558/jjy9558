"""Command line interface: python3 -m subpipe <command>."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import price as pricemod
from . import qa as qamod
from . import review as reviewmod
from . import srt
from .llm import LLMError, LLMUnavailable, OpenAIClient
from .segment import SegConfig
from .transcribe import TranscribeError, transcribe_media
from .translate import Glossary, TranslationMemory, Translator, format_draft


def _limits(a) -> qamod.Limits:
    return qamod.Limits(cps=a.cps, max_chars=a.max_chars, max_lines=a.max_lines,
                        min_dur=getattr(a, "min_dur", 0.7), max_dur=getattr(a, "max_dur", 7.0),
                        min_gap=getattr(a, "min_gap", 0.0))


def _add_limits(p, max_chars=22):
    p.add_argument("--cps", type=float, default=14.0, help="최대 초당 글자수 (기본 14)")
    p.add_argument("--max-chars", type=int, default=max_chars, help="한 줄 최대 글자수")
    p.add_argument("--max-lines", type=int, default=2)


def _outpath(src: str, outdir: Optional[str], suffix: str) -> str:
    stem = os.path.splitext(os.path.basename(src))[0]
    d = outdir or os.path.dirname(os.path.abspath(src))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, stem + suffix)


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="subpipe", description="자막 파이프라인: 전사 -> 번역 초안 -> 검수 -> SRT")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("transcribe", help="미디어 -> 원문 SRT (faster-whisper)")
    p.add_argument("media")
    p.add_argument("--model", default="large-v3")
    p.add_argument("--lang", default="auto", choices=["auto", "en", "ja", "ko"])
    p.add_argument("--device", default="cuda")
    p.add_argument("--compute-type", default=None)
    p.add_argument("-o", "--outdir")
    p.add_argument("--max-chars", type=int, default=42)
    p.add_argument("--max-lines", type=int, default=2)
    p.add_argument("--min-dur", type=float, default=1.0)
    p.add_argument("--max-dur", type=float, default=7.0)
    p.add_argument("--min-gap", type=float, default=0.08)

    p = sub.add_parser("translate", help="원문 SRT -> 번역 초안 + 검수 파일")
    p.add_argument("src")
    p.add_argument("--to", default="ko")
    p.add_argument("--from", dest="from_", default="auto")
    p.add_argument("--tone", default="formal", help="formal|casual (존댓말|반말)")
    p.add_argument("--domain", default="general", help="general|variety|drama|it|medical (예능|드라마|IT|의학)")
    p.add_argument("--glossary")
    p.add_argument("--context", type=int, default=2, help="앞뒤 문맥 줄 수")
    p.add_argument("--batch-size", type=int, default=15)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--condense", action="store_true", help="CPS 초과 줄을 LLM으로 한 번 더 줄여보기")
    p.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    p.add_argument("--llm-model", default="local")
    p.add_argument("--api-key", default="none")
    p.add_argument("--tm", default="translation_memory.json")
    p.add_argument("--no-tm", action="store_true")
    p.add_argument("-o", "--outdir")
    _add_limits(p)

    p = sub.add_parser("review", help="검수용 HTML 생성 (review.json 또는 --src/--draft)")
    p.add_argument("project", nargs="?", help="*.review.json")
    p.add_argument("--src")
    p.add_argument("--draft")
    p.add_argument("--glossary")
    p.add_argument("-o", "--output")
    _add_limits(p)

    p = sub.add_parser("apply-review", help="검수 완료 JSON -> 최종 SRT")
    p.add_argument("edited")
    p.add_argument("-o", "--output")
    p.add_argument("--src", help="원문 SRT (줄 수/타임코드 1:1 검증)")
    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--vtt", action="store_true")
    _add_limits(p)

    p = sub.add_parser("qa", help="최종 SRT 점검")
    p.add_argument("srt")
    p.add_argument("--min-dur", type=float, default=0.7)
    p.add_argument("--max-dur", type=float, default=7.0)
    p.add_argument("--min-gap", type=float, default=0.0)
    p.add_argument("--strict", action="store_true", help="경고도 실패로 처리")
    _add_limits(p)

    p = sub.add_parser("price", help="분당 단가 견적")
    p.add_argument("path", help="영상/음성 또는 SRT/VTT")
    p.add_argument("--rate", type=int, help="분당 단가(원) 하나만 계산")
    p.add_argument("--tier", choices=list(pricemod.DEFAULT_TIERS))
    p.add_argument("--tiers", help="기본 단가 변경 예: basic=5000,entertainment=7000,specialist=10000")
    p.add_argument("--minutes", type=float, help="길이를 직접 지정(분)")
    p.add_argument("--round", dest="rounding", default="ceil", choices=["ceil", "half", "none"])
    return ap


# ------------------------------------------------------------------ commands
def cmd_transcribe(a, whisper_factory=None) -> int:
    cfg = SegConfig(max_chars=a.max_chars, max_lines=a.max_lines, min_dur=a.min_dur,
                    max_dur=a.max_dur, min_gap=a.min_gap)
    cues, lang = transcribe_media(a.media, a.model, a.lang, a.device, a.compute_type, cfg, whisper_factory)
    out = _outpath(a.media, a.outdir, ".%s.srt" % lang)
    srt.save_srt(out, cues)
    print("감지 언어: %s / %d개 자막 -> %s" % (lang, len(cues), out))
    return 0


def cmd_translate(a, client=None) -> int:
    cues = srt.load(a.src)
    if not cues:
        print("자막이 비어 있습니다: " + a.src, file=sys.stderr)
        return 1
    limits = _limits(a)
    gl = Glossary.load(a.glossary) if a.glossary else Glossary()
    tm = TranslationMemory(None if a.no_tm else a.tm)
    client = client or OpenAIClient(a.base_url, a.llm_model, a.api_key)
    stem = "%s.%s" % (os.path.splitext(os.path.basename(a.src))[0], a.to)
    json_path = _outpath(a.src, a.outdir, ".%s.review.json" % a.to)
    meta = {"title": stem, "base": stem, "src_lang": a.from_, "tgt_lang": a.to, "tone": a.tone, "domain": a.domain}

    def save_partial(drafts):
        fmt = [format_draft(d, limits) for d in drafts]
        _write(json_path, json.dumps(dict(reviewmod.build_doc(cues, fmt, limits, gl, meta), partial=True),
                                     ensure_ascii=False, indent=1))

    try:
        tr = Translator(client, a.from_, a.to, a.tone, a.domain, gl, limits, tm, a.context,
                        a.batch_size, a.retries, on_progress=save_partial)
        drafts = tr.translate(cues)
        if a.condense:
            drafts = tr.condense(cues, drafts)
    except LLMUnavailable as e:
        print("오류: %s\n진행 상황 저장됨: %s%s\n서버를 켠 뒤 같은 명령을 다시 실행하면 이어서 진행합니다."
              % (e, json_path, "" if a.no_tm else " + " + a.tm), file=sys.stderr)
        return 2
    except LLMError as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2
    fmt = [format_draft(d, limits) for d in drafts]
    doc = reviewmod.build_doc(cues, fmt, limits, gl, meta)
    _write(json_path, json.dumps(doc, ensure_ascii=False, indent=1))
    html_path = _outpath(a.src, a.outdir, ".%s.review.html" % a.to)
    _write(html_path, reviewmod.render_html(doc))
    draft_srt = _outpath(a.src, a.outdir, ".%s.draft.srt" % a.to)
    srt.save_srt(draft_srt, [srt.Cue(c.index, c.start, c.end, d) for c, d in zip(cues, fmt)])
    nflag = sum(1 for e in doc["entries"] if e["flags"])
    print("초안 %d줄 (플래그 %d줄)\n  검수 HTML: %s\n  JSON: %s\n  초안 SRT: %s" % (
        len(cues), nflag, html_path, json_path, draft_srt))
    return 0


def cmd_review(a) -> int:
    limits = _limits(a)
    if a.project:
        with open(a.project, "r", encoding="utf-8") as f:
            doc = json.load(f)
        out = a.output or os.path.splitext(a.project)[0] + ".html"
    elif a.src and a.draft:
        cues, drafts = srt.load(a.src), srt.load(a.draft)
        if len(cues) != len(drafts):
            print("오류: 줄 수 불일치 (원문 %d / 초안 %d)" % (len(cues), len(drafts)), file=sys.stderr)
            return 1
        gl = Glossary.load(a.glossary) if a.glossary else Glossary()
        stem = os.path.splitext(os.path.basename(a.draft))[0]
        doc = reviewmod.build_doc(cues, [d.text for d in drafts], limits, gl, {"title": stem, "base": stem})
        out = a.output or os.path.splitext(a.draft)[0] + ".review.html"
        _write(os.path.splitext(out)[0] + ".json", json.dumps(doc, ensure_ascii=False, indent=1))
    else:
        print("review: review.json 또는 --src 와 --draft 가 필요합니다", file=sys.stderr)
        return 1
    _write(out, reviewmod.render_html(doc))
    print("검수 HTML: " + out)
    return 0


def cmd_apply(a) -> int:
    try:
        with open(a.edited, "r", encoding="utf-8") as f:
            doc = json.load(f)
        src = srt.load(a.src) if a.src else None
        cues = reviewmod.apply_review(doc, src, a.allow_empty)
    except (ValueError, KeyError, OSError) as e:
        print("오류: %s" % e, file=sys.stderr)
        return 1
    ext = ".vtt" if a.vtt else ".srt"
    out = a.output or (a.edited[:-5] if a.edited.endswith(".json") else a.edited) + ".final" + ext
    _write(out, srt.write_vtt(cues) if a.vtt else srt.write_srt(cues))
    issues = qamod.lint(cues, _limits(a))
    print("최종 자막 %d줄 -> %s (QA 이슈 %d건)" % (len(cues), out, len(issues)))
    for i in issues[:20]:
        print("  " + str(i))
    return 0


def cmd_qa(a) -> int:
    cues = srt.load(a.srt)
    issues = qamod.lint(cues, _limits(a))
    for i in issues:
        print(i)
    errs = sum(1 for i in issues if i.severity == "error")
    print("%d줄 점검: 오류 %d, 경고 %d" % (len(cues), errs, len(issues) - errs))
    return 1 if errs or (a.strict and issues) else 0


def cmd_price(a) -> int:
    try:
        secs = a.minutes * 60 if a.minutes is not None else pricemod.media_duration_seconds(a.path)
        tiers = pricemod.parse_tiers(a.tiers)
    except pricemod.PriceError as e:
        print("오류: %s" % e, file=sys.stderr)
        return 1
    print("길이: %d분 %02d초 (%.2f분), 과금 단위: %s" % (secs // 60, secs % 60, secs / 60, a.rounding))
    rows = []
    if a.rate is not None:
        rows.append(("직접 지정", a.rate))
    elif a.tier:
        rows.append(tiers[a.tier])
    else:
        rows.extend(tiers.values())
    for name, rate in rows:
        q = pricemod.quote(secs, rate, a.rounding)
        print("  %-12s %s/분 x %g분 = %s" % (name, pricemod.won(rate), q["billable_minutes"], pricemod.won(q["amount"])))
    return 0


def main(argv: Optional[List[str]] = None, client=None, whisper_factory=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if a.cmd == "transcribe":
            return cmd_transcribe(a, whisper_factory)
        if a.cmd == "translate":
            return cmd_translate(a, client)
        return {"review": cmd_review, "apply-review": cmd_apply, "qa": cmd_qa, "price": cmd_price}[a.cmd](a)
    except TranscribeError as e:
        print(str(e), file=sys.stderr)
        return 3
    except FileNotFoundError as e:
        print("파일을 찾을 수 없습니다: %s" % e.filename, file=sys.stderr)
        return 1
