"""Command-line entry point: python -m subvocab <files...> [options]."""
from __future__ import annotations

import argparse
import datetime
import os
import sys
from typing import Optional, Sequence

from .known import add_known_words, load_known
from .llm import LLMClient, fill_meanings, load_llm_config
from .output import write_outputs
from .subtitles import SubtitleError, load_captions
from .vocab import analyze

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_KNOWN = os.path.join(PROJECT_ROOT, "known_words.txt")
DEFAULT_CONFIG = os.path.join(PROJECT_ROOT, "config", "llm.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m subvocab",
        description="영어 자막(.srt/.vtt)에서 단어장(Markdown, Anki용 TSV)을 만듭니다.",
        epilog="--add-known은 옵션 중 맨 뒤에 두거나 자막 파일 앞에 두세요 (단어 목록을 먹기 때문).",
    )
    parser.add_argument("files", nargs="*", help="자막 파일 (.srt 또는 .vtt)")
    parser.add_argument("--top", type=int, default=50, help="빈도순 상위 N개만 출력 (0이면 전체, 기본 50)")
    parser.add_argument("--known", default=DEFAULT_KNOWN, help="아는 단어 목록 파일 (기본: 프로젝트 known_words.txt)")
    parser.add_argument("--no-llm", action="store_true", help="LLM으로 뜻을 만들지 않음")
    parser.add_argument("--out", default="output", help="출력 폴더 (기본: ./output)")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="LLM 설정 JSON (기본: config/llm.json)")
    parser.add_argument(
        "--add-known", nargs="+", metavar="WORD",
        help="아는 단어 목록에 단어를 추가하고 종료 (자막 파일과 함께 쓰면 처리도 함)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.top < 0:
        parser.error("--top 은 0 이상이어야 합니다")

    if args.add_known:
        added = add_known_words(args.known, args.add_known)
        print(f"아는 단어 {len(added)}개 추가: {', '.join(added) or '-'} ({args.known})")
        if not args.files:
            return 0

    if not args.files:
        parser.error("자막 파일을 하나 이상 지정하세요")
    for path in args.files:
        if not os.path.isfile(path):
            print(f"오류: 파일이 없습니다: {path}", file=sys.stderr)
            return 1

    captions = []
    try:
        for path in args.files:
            captions.extend(load_captions(path))
    except SubtitleError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    known = load_known(args.known)
    analysis = analyze(captions, known)
    entries = analysis.words if args.top == 0 else analysis.words[:args.top]

    llm_note = "뜻: 생성 안 함 (--no-llm)"
    if not args.no_llm:
        try:
            config = load_llm_config(args.config)
        except ValueError as exc:
            print(f"경고: {exc}. 뜻 없이 진행합니다.", file=sys.stderr)
        else:
            log = lambda msg: print(msg, file=sys.stderr)  # noqa: E731
            filled = fill_meanings(entries, LLMClient(config), config.batch_size, log=log)
            llm_note = f"뜻: {filled}/{len(entries)}개 ({config.base_url})"

    sources = [os.path.basename(p) for p in args.files]
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    md_path, tsv_path = write_outputs(args.out, analysis, entries, sources, generated_at)

    print(f"자막 줄 {analysis.caption_count}개, 후보 단어 {len(analysis.words)}개, "
          f"고유명사 후보 {len(analysis.names)}개")
    print(llm_note)
    print(f"단어장: {md_path}")
    print(f"Anki: {tsv_path}")
    return 0
