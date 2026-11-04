import argparse
import os
import sys

from . import disclose, hookcheck, metadata, phrasebook, report, runner, similar, sources, titles
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
    s = sub.add_parser("similar", help="과거 대본과 유사도 비교 (재활용 콘텐츠 위험)")
    s.add_argument("target", help="clip.txt/.srt/.ass가 있는 폴더")
    s.add_argument("--history", help="과거 대본 폴더 (추가 비교 대상)")
    s.add_argument("--threshold", type=float, help="경고 임계 (0~1, 기본 0.5)")
    s.add_argument("--words", action="store_true", help="어절 n-gram도 함께 사용")
    s.add_argument("--config")
    s.add_argument("--strict", action="store_true")
    s = sub.add_parser("disclose", help="meta.json의 ai_used로 AI 공개 문구 초안 생성")
    s.add_argument("meta")
    s = sub.add_parser("sources", help="BGM/이미지 출처 장부")
    s.add_argument("action", choices=["add", "list", "check"])
    s.add_argument("--file", default="sources.csv", help="장부 csv (기본 sources.csv)")
    s.add_argument("--name")
    s.add_argument("--license", dest="license_")
    s.add_argument("--url")
    s.add_argument("--ai", help="AI 생성 여부 yes/no")
    s.add_argument("--strict", action="store_true")
    s = sub.add_parser("titles", help="제목 후보 규칙 점수")
    s.add_argument("meta")
    s.add_argument("--transcript", help="대본 파일 (생략 시 meta/옆 파일 자동 탐색)")
    s.add_argument("--config")
    s = sub.add_parser("hook", help="첫 3초 훅 점검 (meta.json/.srt/.ass/.txt)")
    s.add_argument("target")
    s.add_argument("--video", help="영상 파일 (생략 시 옆 파일 자동 탐색, 오디오 시작 점검용)")
    s.add_argument("--no-audio", action="store_true", help="오디오 시작 점검 생략")
    s.add_argument("--book", help="훅 사전 JSON (기본: 사용자 데이터 폴더)")
    s.add_argument("--config")
    s.add_argument("--strict", action="store_true")
    s = sub.add_parser("hooks", help="훅 문장 사전")
    s.add_argument("action", choices=["list", "add", "edit", "remove", "suggest", "use", "check"])
    s.add_argument("arg", nargs="?", help="suggest: meta.json / use,edit,remove: 템플릿 id")
    s.add_argument("--tag")
    s.add_argument("--text")
    s.add_argument("--tags")
    s.add_argument("--id", dest="tid")
    s.add_argument("--video", help="use: 사용한 영상 이름")
    s.add_argument("--book", help="훅 사전 JSON (기본: 사용자 데이터 폴더)")
    s.add_argument("--config")
    s.add_argument("--strict", action="store_true")
    return p


def _hook_cmd(args):
    try:
        cfg = load_config(args.config)
        book_path = args.book or phrasebook.default_path()
        book = phrasebook.load(book_path)
        if args.cmd == "hook":
            r = hookcheck.run(args.target, cfg, book, args.video, not args.no_audio)
            print(hookcheck.format_result(r))
            return 1 if args.strict and not r["ok"] else 0
        hk = cfg["hooks"]
        a = args.action
        if a == "list":
            print(phrasebook.format_list(phrasebook.list_templates(book, args.tag)))
            return 0
        if a == "add":
            tid = phrasebook.add(book, args.text, args.tags, args.tid)
            phrasebook.save(book, book_path)
            print("추가: %s" % tid)
            return 0
        if a == "edit":
            phrasebook.edit(book, args.arg or args.tid, args.text, args.tags)
            phrasebook.save(book, book_path)
            print("수정: %s" % (args.arg or args.tid))
            return 0
        if a == "remove":
            phrasebook.remove(book, args.arg or args.tid)
            phrasebook.save(book, book_path)
            print("삭제: %s" % (args.arg or args.tid))
            return 0
        if a == "use":
            tid = args.arg or args.tid
            added = phrasebook.use(book, tid, args.video)
            phrasebook.save(book, book_path)
            print("사용 기록: %s%s" % (tid, "" if added else " (이미 기록됨)"))
        elif a == "suggest":
            if not args.arg:
                raise ValueError("meta.json 경로가 필요합니다")
            meta = metadata.load_meta(args.arg)
            rows = phrasebook.suggest(book, meta, hk["suggest_count"], hk["overuse_window"], hk["overuse_max"])
            print(phrasebook.format_suggestions(rows))
            return 0
        over = phrasebook.overused(book, hk["overuse_window"], hk["overuse_max"])
        lines = phrasebook.format_overuse(over, book, hk["overuse_window"])
        for ln in lines:
            print(ln)
        if a == "check" and not lines:
            print("✅ 최근 %d편 안에 과다 사용 템플릿 없음" % hk["overuse_window"])
        return 1 if lines and args.strict else 0
    except (OSError, ValueError) as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2


def _extra(args):
    """check/check-dir 외 신규 명령. 종료 코드 반환."""
    if args.cmd == "disclose":
        try:
            print(disclose.format_draft(disclose.draft(metadata.load_meta(args.meta))))
        except (OSError, ValueError) as e:
            print("오류: %s" % e, file=sys.stderr)
            return 2
        return 0
    if args.cmd == "sources":
        if args.action == "add":
            try:
                r = sources.add(args.file, args.name, args.license_, args.url, args.ai)
            except (OSError, ValueError) as e:
                print("오류: %s" % e, file=sys.stderr)
                return 2
            print("%s: %s -> %s" % (r, args.name, args.file))
            return 0
        if not os.path.isfile(args.file):
            print("장부 없음: %s" % args.file, file=sys.stderr)
            return 2
        rows = sources.load(args.file)
        if args.action == "list":
            print(sources.format_list(rows))
            return 0
        issues = sources.check(rows)
        for _, msg in issues:
            print("⚠️ " + msg)
        if not issues:
            print("✅ 장부 %d건 모두 기재됨" % len(rows))
        return 1 if issues and args.strict else 0
    try:
        cfg = load_config(getattr(args, "config", None))
    except (OSError, ValueError) as e:
        print("설정 오류: %s" % e, file=sys.stderr)
        return 2
    if args.cmd == "similar":
        if not os.path.isdir(args.target):
            print("폴더 없음: %s" % args.target, file=sys.stderr)
            return 2
        if args.history and not os.path.isdir(args.history):
            print("폴더 없음: %s" % args.history, file=sys.stderr)
            return 2
        rows, th = similar.analyze(args.target, cfg, args.history, args.words, args.threshold)
        if not rows:
            print("대본(.txt/.srt/.ass)이 없습니다: %s" % args.target, file=sys.stderr)
            return 2
        print(similar.format_rows(rows, th))
        return 1 if args.strict and any(r["risk"] for r in rows) else 0
    # titles
    try:
        meta = metadata.load_meta(args.meta)
    except (OSError, ValueError) as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2
    tr = titles.load_transcript(meta, args.meta, args.transcript)
    print(titles.format_rank(titles.rank(meta, tr, cfg)))
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    args = build_parser().parse_args(argv)
    if args.cmd in ("hook", "hooks"):
        return _hook_cmd(args)
    if args.cmd in ("similar", "disclose", "sources", "titles"):
        return _extra(args)
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
