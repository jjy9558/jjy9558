# -*- coding: utf-8 -*-
"""명령행 진입점: python -m til_drafter [옵션]"""
import argparse
import json
import os
import sys
from datetime import timedelta

from . import __version__
from .draft import build_draft, format_check_report
from .redact import Redactor
from .transcripts import (
    default_projects_dir,
    load_entries,
    local_today,
    parse_date,
    parse_tz_offset,
)

DEFAULT_CONFIG = os.path.join("~", ".til-drafter.json")


def _build_parser():
    p = argparse.ArgumentParser(
        prog="python -m til_drafter",
        description="Claude Code 세션 로그에서 하루치 TIL 초안(Markdown)을 만듭니다. "
        "민감정보는 자동으로 마스킹됩니다.",
    )
    p.add_argument("--date", help="대상 날짜 YYYY-MM-DD, 또는 today/yesterday (기본: 오늘, 로컬 시간)")
    p.add_argument("--projects-dir", help="Claude Code projects 폴더 (기본: ~/.claude/projects)")
    p.add_argument("--out", help="초안을 저장할 폴더 (기본: ~/til-drafts)")
    p.add_argument("--tz-offset", help="날짜 경계용 UTC 오프셋, 예: +09:00 (기본: 시스템 로컬 시간)")
    p.add_argument(
        "--redact-word", action="append", default=[], metavar="WORD",
        help="추가로 가릴 단어(여러 번 지정 또는 쉼표로 구분)",
    )
    p.add_argument(
        "--config", metavar="FILE",
        help="설정 파일(JSON: redact_words, out, projects_dir, tz_offset). "
        "기본: ~/.til-drafter.json (있을 때만)",
    )
    p.add_argument(
        "--check", action="store_true",
        help="파일을 쓰지 않고 어떤 마스킹 규칙이 몇 번 발동했는지만 출력(비밀값은 출력 안 함)",
    )
    p.add_argument("--version", action="version", version="til-drafter " + __version__)
    return p


def _load_config(path, explicit):
    full = os.path.expanduser(path)
    if not os.path.isfile(full):
        if explicit:
            raise ValueError("설정 파일을 찾을 수 없습니다: %s" % path)
        return {}
    try:
        with open(full, "r", encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        # 마스킹 단어가 조용히 빠지면 안 되므로 중단한다.
        raise ValueError("설정 파일을 읽을 수 없습니다 (%s): %s" % (path, exc))
    if not isinstance(cfg, dict):
        raise ValueError("설정 파일 최상위는 JSON 객체여야 합니다: %s" % path)
    return cfg


def _resolve_date(value, tz):
    today = local_today(tz)
    if not value or value.strip().lower() == "today":
        return today
    if value.strip().lower() == "yesterday":
        return today - timedelta(days=1)
    return parse_date(value)


def _split_words(values):
    out = []
    for v in values:
        for w in str(v).split(","):
            w = w.strip()
            if w:
                out.append(w)
    return out


def _safe_print(msg, stream=None):
    stream = stream or sys.stdout
    try:
        print(msg, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "ascii"
        print(msg.encode(enc, "replace").decode(enc, "replace"), file=stream)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = _load_config(args.config or DEFAULT_CONFIG, explicit=bool(args.config))
        tz = parse_tz_offset(args.tz_offset if args.tz_offset is not None else cfg.get("tz_offset"))
        day = _resolve_date(args.date, tz)
    except ValueError as exc:
        parser.error(str(exc))

    projects_dir = os.path.expanduser(args.projects_dir or cfg.get("projects_dir") or default_projects_dir())
    out_dir = os.path.expanduser(args.out or cfg.get("out") or os.path.join("~", "til-drafts"))
    cfg_words = cfg.get("redact_words") or []
    if not isinstance(cfg_words, list):
        parser.error("설정 파일의 redact_words 는 문자열 목록이어야 합니다")
    words = _split_words(list(cfg_words) + list(args.redact_word))

    if not os.path.isdir(projects_dir):
        _safe_print("projects 폴더를 찾을 수 없습니다: %s" % projects_dir, sys.stderr)
        return 1

    stats = {}
    entries = load_entries(projects_dir, day, tz, stats)
    redactor = Redactor(words=words)
    md = build_draft(entries, day, redactor)

    if args.check:
        _safe_print(format_check_report(redactor, day, stats))
        return 0

    if not entries:
        _safe_print("%s: 해당 날짜의 Claude Code 기록이 없어 초안을 만들지 않았습니다." % day.isoformat())
        return 0

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, day.isoformat() + ".md")
    if os.path.exists(path):
        # 이미 손본 초안을 야간 작업이 덮어쓰지 않도록 이전 파일은 .bak.md 로 보관
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                old = fh.read()
            if old != md:
                os.replace(path, os.path.join(out_dir, day.isoformat() + ".bak.md"))
        except OSError:
            pass
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    _safe_print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
