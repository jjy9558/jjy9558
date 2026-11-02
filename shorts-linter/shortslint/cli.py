import argparse
import os
import sys

from . import report, runner
from .config import load_config


def build_parser():
    p = argparse.ArgumentParser(prog="shortslint", description="유튜브 쇼츠 업로드 전 린터")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("check", "check-dir"):
        s = sub.add_parser(name)
        s.add_argument("target", help="영상 파일" if name == "check" else "영상 폴더")
        if name == "check":
            s.add_argument("--meta", help="meta.json 경로")
        s.add_argument("--profile", default="shorts")
        s.add_argument("--config", help="임계값/단어 목록 JSON")
        s.add_argument("--json", dest="json_out", help="JSON 리포트 저장 경로")
        s.add_argument("--md", dest="md_out", help="Markdown 리포트 저장 경로")
        s.add_argument("--strict", action="store_true", help="⚠️도 실패로 취급")
        s.add_argument("--llm", action="store_true", help="로컬 LLM 판정 사용 (설정의 llm 섹션)")
        s.add_argument("--quiet", action="store_true", help="콘솔 출력 생략")
    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config, args.profile)
    except (OSError, ValueError) as e:
        print("설정 오류: %s" % e, file=sys.stderr)
        return 2
    if args.llm:
        cfg["llm"]["enabled"] = True
    if args.cmd == "check":
        if not os.path.isfile(args.target):
            print("파일 없음: %s" % args.target, file=sys.stderr)
            return 2
        rep, _ = runner.check_video(args.target, cfg, args.meta)
        reports = [rep]
    else:
        if not os.path.isdir(args.target):
            print("폴더 없음: %s" % args.target, file=sys.stderr)
            return 2
        reports = runner.check_dir(args.target, cfg)
        if not reports:
            print("영상 파일이 없습니다: %s" % args.target, file=sys.stderr)
            return 2
    if not args.quiet:
        print(report.format_console(reports))
    for path, fn in ((args.json_out, report.format_json), (args.md_out, report.format_markdown)):
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(fn(reports))
    bad = sum(r.count("fail") + (r.count("warn") if args.strict else 0) for r in reports)
    return 1 if bad else 0
