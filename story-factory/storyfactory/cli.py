"""명령줄 진입점: python -m storyfactory <seeds|scrub|run|lint>."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import TextIO

from . import __version__
from ._jjkit import llm as jllm
from . import lint as lint_mod
from .pipeline import Options, run as run_pipeline, scrub_cards
from .scrub import REASON_LABELS, Scrubber, sensitive_reasons
from .seeds import Sources, collect

DEFAULT_SEED = 17


def _add_source_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("소재 소스 (모두 읽기 전용)")
    g.add_argument("--life-nagger", metavar="PATH", help="life-nagger counters.jsonl 또는 상태 폴더")
    g.add_argument("--decision-pet", metavar="PATH", help="decision-pet state.json 또는 폴더 (DECISION_PET_HOME) (묘지, voice-journal 가져오기 포함)")
    g.add_argument("--cool-off", metavar="PATH", help="cool-off data.json 또는 폴더 (후회 목록)")
    g.add_argument("--kakao", metavar="JSON", help="kakao-wrapped --anonymize --json 결과 (익명 통계만 사용, 원문 대화는 읽지 않음)")
    g.add_argument("--voice-journal", metavar="DB", help="voice-journal journal.db (--journal-entry 로 지정한 일기만 읽음)")
    g.add_argument("--journal-entry", type=int, action="append", default=[], metavar="ID",
                   help="사연에 써도 된다고 직접 고른 일기 id (여러 번 가능)")
    g.add_argument("--date", help="기준 날짜 YYYY-MM-DD (기본: 오늘)")
    g.add_argument("--max-cards", type=int, default=10)
    s = p.add_argument_group("가명화")
    s.add_argument("--name", action="append", default=[], help="가릴 실명 (여러 번 가능)")
    s.add_argument("--names-file", help="가릴 이름 목록 파일 (한 줄에 하나)")
    s.add_argument("--place", action="append", default=[], help="가릴 지명/학교/아파트 이름")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="결정적 선택용 시드 (기본 %d)" % DEFAULT_SEED)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="story-factory", description="사연 공장 야간조: 생활 로그 소재를 익명 사연 쇼츠 대본으로")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seeds", help="소재 카드 10개 수집 + 가명화/민감 주제 필터 결과 보기")
    _add_source_args(s)
    s.add_argument("--json", action="store_true", help="JSON으로 출력")

    s = sub.add_parser("scrub", help="문장 하나를 가명화하고 민감 주제 여부 확인")
    s.add_argument("text", nargs="?", help="생략하면 표준입력")
    s.add_argument("--name", action="append", default=[])
    s.add_argument("--names-file")
    s.add_argument("--place", action="append", default=[])
    s.add_argument("--seed", type=int, default=DEFAULT_SEED)

    s = sub.add_parser("run", help="전체 파이프라인: seeds -> scrub -> draft -> lint -> 아침 보고")
    _add_source_args(s)
    s.add_argument("--out", default="out", help="산출물 폴더 (그 안에 날짜 폴더 생성, 기본 out)")
    s.add_argument("--drafts", type=int, default=6, help="대본을 만들 카드 수 (기본 6)")
    s.add_argument("--candidates", type=int, default=2, help="아침 후보 수 (기본 2)")
    s.add_argument("--no-llm", action="store_true", help="LLM을 쓰지 않고 템플릿만")
    s.add_argument("--llm-url", help="로컬 OpenAI 호환 주소 (기본: 환경변수 STORY_FACTORY_LLM_URL, 없으면 템플릿)")
    s.add_argument("--llm-model", help="모델 이름 (환경변수 STORY_FACTORY_LLM_MODEL)")
    s.add_argument("--no-ai-voice", action="store_true", help="ai_used.voice=false (사람이 직접 녹음할 때)")
    s.add_argument("--no-ai-image", action="store_true", help="ai_used.image=false")
    s.add_argument("--linter-dir", help="shorts-linter 폴더 (기본: SHORTSLINT_DIR, 형제 폴더 shorts-linter)")
    s.add_argument("--no-lint", action="store_true", help="shorts-linter 호출 생략")
    s.add_argument("--history", help="과거 대본 폴더 (similar 비교용)")
    s.add_argument("--submit-jarvis", action="store_true", help="jarvis 작업 큐에 story.batch 작업 등록 (실패하면 건너뜀)")
    s.add_argument("--jarvis-url", default=os.environ.get("JARVIS_URL", "http://127.0.0.1:8800"))
    s.add_argument("--jarvis-device", default=os.environ.get("JARVIS_DEVICE", ""))
    s.add_argument("--jarvis-token", default=os.environ.get("JARVIS_TOKEN", ""))

    s = sub.add_parser("lint", help="이미 만든 폴더(<id>.txt + <id>.meta.json)를 shorts-linter로 다시 검사")
    s.add_argument("dir")
    s.add_argument("--linter-dir")
    s.add_argument("--history")
    return p


def _names(args) -> list[str]:
    names = list(args.name)
    if getattr(args, "names_file", None):
        try:
            names += [ln.strip() for ln in Path(args.names_file).read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            print("경고: 이름 목록 파일을 읽을 수 없습니다: %s" % args.names_file, file=sys.stderr)
    return names


def _sources(args) -> Sources:
    p = lambda v: Path(v) if v else None  # noqa: E731
    return Sources(p(args.life_nagger), p(args.decision_pet), p(args.cool_off), p(args.kakao),
                   p(args.voice_journal), list(args.journal_entry))


def _now(args) -> date:
    return date.fromisoformat(args.date) if args.date else date.today()


def cmd_seeds(args, out: TextIO) -> int:
    now = _now(args)
    cards, warnings = collect(_sources(args), now, args.max_cards)
    scrubber = Scrubber(_names(args), args.place, args.seed)
    clean, excluded = scrub_cards(cards, scrubber)
    if args.json:
        json.dump({"cards": [c.to_dict() for c in clean],
                   "excluded": [{"card": c, "kind": k, "reasons": r} for c, k, r in excluded],
                   "warnings": warnings}, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 0
    print("소재 카드 %d장 (제외 %d장)" % (len(clean), len(excluded)), file=out)
    for c in clean:
        extra = (" · %s" % c.subject) if c.subject else ""
        print("- [%d] %s (%s/%s) %s%s" % (c.priority, c.id, c.source, c.kind, json.dumps(c.facts, ensure_ascii=False), extra), file=out)
    for cid, kind, reasons in excluded:
        print("- 제외 %s (%s): %s" % (cid, kind, ", ".join(REASON_LABELS.get(r, r) for r in reasons)), file=out)
    for w in warnings:
        print("! " + w, file=out)
    return 0


def cmd_scrub(args, out: TextIO) -> int:
    text = args.text if args.text is not None else sys.stdin.read()
    sc = Scrubber(_names(args), args.place, args.seed)
    sc.learn(text)
    reasons = sensitive_reasons(text, sc.originals)
    print(sc.scrub(text), file=out)
    if reasons:
        print("민감 주제: " + ", ".join(REASON_LABELS[r] for r in reasons) + " -> 카드에서 제외됩니다", file=out)
    return 0


def _client(args):
    if args.no_llm:
        return None
    env = dict(os.environ)
    if args.llm_url:
        env["STORY_FACTORY_LLM_URL"] = args.llm_url
    if args.llm_model:
        env["STORY_FACTORY_LLM_MODEL"] = args.llm_model
    try:
        return jllm.client_from_env("STORY_FACTORY", env=env, timeout=60.0)
    except jllm.LLMError as e:
        print("경고: LLM 설정을 쓸 수 없어 템플릿으로 진행합니다 (%s)" % e, file=sys.stderr)
        return None


def cmd_run(args, out: TextIO) -> int:
    jarvis_cfg = None
    if args.submit_jarvis:
        jarvis_cfg = {"url": args.jarvis_url, "device": args.jarvis_device, "token": args.jarvis_token}
    opts = Options(sources=_sources(args), out_dir=Path(args.out), now=_now(args), seed=args.seed,
                   names=_names(args), places=args.place, max_cards=args.max_cards, max_drafts=args.drafts,
                   candidates=args.candidates, client=_client(args), ai_voice=not args.no_ai_voice,
                   ai_image=not args.no_ai_image, linter_dir=args.linter_dir,
                   history=Path(args.history) if args.history else None, jarvis=jarvis_cfg,
                   do_lint=not args.no_lint)
    res = run_pipeline(opts)
    print("소재 %d장 -> 초안 %d편 -> 후보 %d개 (제외 %d장)" % (len(res.cards), len(res.drafts), len(res.candidates), len(res.excluded)), file=out)
    for n, (d, lr, _) in enumerate(res.candidates, 1):
        tag = ("약속 " + lr.promise_status) if lr.available else "린트 생략"
        print("  %d. %s  [%s]" % (n, d.title, tag), file=out)
    for w in res.warnings:
        print("! " + w, file=out)
    if res.jarvis_msg:
        print("jarvis: " + res.jarvis_msg, file=out)
    print("보고서: %s" % res.report_path, file=out)
    return 0


def cmd_lint(args, out: TextIO) -> int:
    d = Path(args.dir)
    if not d.is_dir():
        print("폴더 없음: %s" % d, file=sys.stderr)
        return 2
    ids = sorted(p.name[:-len(".meta.json")] for p in d.glob("*.meta.json"))
    linter = lint_mod.Linter(lint_mod.find_linter_dir(args.linter_dir), d)
    reports = lint_mod.lint_all(linter, d, ids, Path(args.history) if args.history else None)
    if not linter.available:
        print("⏭️ shorts-linter 를 찾지 못해 린트를 생략했습니다", file=out)
        return 0
    for i in ids:
        r = reports[i]
        if not r.available:
            print("⏭️ %s: %s" % (i, r.note), file=out)
            continue
        print("%s: 약속 %s, 제목 최고 %d점%s" % (i, r.promise_status, r.best_title_score,
                                              ", 재활용 위험" if r.similar_risk else ""), file=out)
    return 0


COMMANDS = {"seeds": cmd_seeds, "scrub": cmd_scrub, "run": cmd_run, "lint": cmd_lint}


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글/이모지로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return COMMANDS[args.cmd](args, out or sys.stdout)
