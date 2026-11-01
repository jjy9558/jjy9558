"""명령줄 인터페이스: python -m hpatch <scan|extract|translate|apply|restore|export-csv|import-csv>"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from .backup import BackupStore
from .csvio import export_csv, import_csv
from .engines import ApplyOptions
from .glossary import Glossary
from .llm import OpenAIChatClient
from .project import Workspace, extract_all, load_engine, save_units, unique_kinds
from .translate import Translator

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_LLM_DOWN = 0, 1, 2, 3


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("game_dir", help="게임 폴더 (RPG Maker: index.html/Game.exe 가 있는 폴더, Ren'Py: game/ 의 상위 폴더)")
    p.add_argument("--engine", choices=["auto", "rpgmaker", "renpy"], default="auto")
    p.add_argument("--work-dir", help="작업 폴더 (기본: <game_dir>/.hpatch)")
    p.add_argument("--tm", help="번역 메모리 파일 경로 (기본: <작업 폴더>/tm.sqlite)")
    p.add_argument("--glossary", help="용어집 파일(.json/.txt/.tsv) (기본: <작업 폴더>/glossary.json)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hpatch", description="인디게임 AI 한글패치 도구 (개인용, 로컬 LLM)")
    ap.add_argument("--version", action="version", version=f"hpatch {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="엔진 감지 및 문자열 개수 확인 (파일을 쓰지 않음)")
    _common(p)

    p = sub.add_parser("extract", help="번역 대상 문자열을 추출해 작업 폴더에 units.json 으로 저장")
    _common(p)

    p = sub.add_parser("translate", help="번역 메모리에 없는 문자열을 로컬 LLM 으로 번역")
    _common(p)
    p.add_argument("--limit", type=int, help="이번 실행에서 번역할 최대 문자열 수")
    p.add_argument("--dry-run", action="store_true", help="LLM 호출 없이 대상 개수만 계산")
    p.add_argument("--show-prompt", action="store_true", help="(dry-run) 첫 배치 프롬프트 출력")
    p.add_argument("--endpoint", default=os.environ.get("HPATCH_ENDPOINT", "http://127.0.0.1:8080/v1"))
    p.add_argument("--model", default=os.environ.get("HPATCH_MODEL", "local"))
    p.add_argument("--api-key", default=os.environ.get("HPATCH_API_KEY"))
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--max-chars", type=int, default=3000, help="배치당 대략적인 최대 글자 수")
    p.add_argument("--retries", type=int, default=1, help="실패한 줄을 개별 재시도하는 횟수")
    p.add_argument("--source-lang", default="auto", help="원문 언어 (기본: 자동)")
    p.add_argument("--style", default="", help="번역 말투 지시문 (기본: 자연스러운 게임 대사체)")

    p = sub.add_parser("apply", help="번역을 게임에 적용 (원본은 .hpatch_backup/ 에 백업)")
    _common(p)
    p.add_argument("--font", help="한글 폰트 파일(.ttf/.otf/.woff) 경로 - 주입")
    p.add_argument("--locale-ko", action="store_true", help="(RPG Maker) System.json locale 을 ko_KR 로")
    p.add_argument("--language", default="korean", help="(Ren'Py) tl 폴더/언어 이름")
    p.add_argument("--no-default-lang", action="store_true", help="(Ren'Py) 기본 언어 설정을 쓰지 않음")

    p = sub.add_parser("restore", help="백업에서 원본 복원")
    _common(p)
    p.add_argument("--force", action="store_true", help="패치 후 바뀐 파일도 덮어씀")

    p = sub.add_parser("export-csv", help="검수용 CSV 내보내기")
    _common(p)
    p.add_argument("--out", help="출력 경로 (기본: <작업 폴더>/review.csv)")
    p.add_argument("--only-untranslated", action="store_true")

    p = sub.add_parser("import-csv", help="수정한 CSV 가져오기 (수동 번역으로 저장, LLM 이 덮어쓰지 않음)")
    _common(p)
    p.add_argument("csv", help="가져올 CSV 파일")
    return ap


def _say(msg: str = "") -> None:
    print(msg)


def cmd_scan(args, ws: Workspace) -> int:
    eng = load_engine(args.game_dir, args.engine)
    res = extract_all(eng)
    _say(f"엔진: {eng.describe()}")
    for n in res.notes:
        _say(f"[안내] {n}")
    if not res.ok:
        return EXIT_ERROR
    masker = eng.masker()
    uniq = {}
    for u in res.units:
        uniq.setdefault(u.source, u)
    translatable = [s for s in uniq if masker.needs_translation(masker.mask(s))]
    _say(f"읽은 파일: {res.files}개")
    _say(f"문자열(유닛): {len(res.units)}개, 중복 제거 {len(uniq)}개, 번역 대상 {len(translatable)}개")
    _say("종류별: " + ", ".join(f"{k} {v}" for k, v in sorted(unique_kinds(res).items())))
    if ws.tm_exists():
        tm = ws.open_tm()
        done = sum(1 for s in translatable if tm.has(s))
        tm.close()
        _say(f"번역 메모리: {done}/{len(translatable)}개 번역됨 ({ws.tm_path})")
    else:
        _say("번역 메모리: 아직 없음")
    return EXIT_OK


def cmd_extract(args, ws: Workspace) -> int:
    eng = load_engine(args.game_dir, args.engine)
    res = extract_all(eng)
    for n in res.notes:
        _say(f"[안내] {n}")
    if not res.ok:
        return EXIT_ERROR
    save_units(ws, res)
    _say(f"{len(res.units)}개 유닛을 저장했습니다: {ws.units_path}")
    return EXIT_OK


def cmd_translate(args, ws: Workspace, client=None) -> int:
    eng = load_engine(args.game_dir, args.engine)
    res = extract_all(eng)
    for n in res.notes:
        _say(f"[안내] {n}")
    if not res.ok:
        return EXIT_ERROR
    glossary = ws.load_glossary()
    masker = eng.masker().with_extra(glossary.mask_patterns)
    tm = ws.open_tm()
    try:
        if client is None and not args.dry_run:
            client = OpenAIChatClient(args.endpoint, args.model, args.api_key, args.temperature, args.timeout)
        tr = Translator(client, tm, masker, glossary, finalize=eng.finalize, batch_size=args.batch_size,
                        max_chars=args.max_chars, src_lang=args.source_lang, style=args.style,
                        retries=args.retries, model=args.model)
        rep = tr.run(res.units, limit=args.limit, dry_run=args.dry_run)
    finally:
        tm.close()
    _say(f"유닛 {rep.total_units}개 / 고유 문자열 {rep.unique}개: 번역 불필요 {rep.skipped_nontext}, "
         f"메모리 재사용 {rep.cached}, 용어집 확정 {rep.glossary}, 번역 필요 {rep.pending}")
    if args.dry_run:
        _say(f"[dry-run] 이번에 번역할 문자열 {rep.to_translate}개, 배치 {rep.batches}개 (LLM 호출 없음)")
        if args.show_prompt and rep.sample_messages:
            for m in rep.sample_messages:
                _say(f"--- {m['role']} ---\n{m['content']}")
        return EXIT_OK
    _say(f"번역 완료 {rep.translated}개, 실패 {rep.failed}개 (LLM 호출 {rep.calls}회)")
    if rep.failed:
        _say("실패한 문자열은 원문 그대로 남으며, 다음 실행 때 다시 시도합니다.")
    if rep.aborted:
        _say(f"[중단] {rep.abort_reason}")
        _say("진행 상황은 번역 메모리에 저장되었습니다. 서버를 확인한 뒤 같은 명령으로 이어서 실행하세요.")
        return EXIT_LLM_DOWN
    return EXIT_OK


def cmd_apply(args, ws: Workspace) -> int:
    eng = load_engine(args.game_dir, args.engine)
    store = BackupStore(args.game_dir)
    if not ws.tm_exists():
        _say("번역 메모리가 없습니다. 먼저 translate 를 실행하세요.")
        return EXIT_ERROR
    tm = ws.open_tm()
    try:
        translations = tm.as_dict()
    finally:
        tm.close()
    opts = ApplyOptions(font=args.font, locale_ko=args.locale_ko, language=args.language,
                        default_language=not args.no_default_lang)
    rep = eng.apply(store, translations, opts)
    for n in rep.notes:
        _say(f"[안내] {n}")
    _say(f"적용: {rep.replaced}곳, 쓴 파일 {len(rep.written)}개, 미번역 {rep.untranslated}개")
    for w in rep.written:
        _say(f"  - {w}")
    if rep.written:
        _say(f"원본은 {store.backup_dir} 에 백업되었습니다. 되돌리려면: python -m hpatch restore \"{args.game_dir}\"")
    return EXIT_OK


def cmd_restore(args, ws: Workspace) -> int:
    store = BackupStore(args.game_dir)
    if not store.files:
        _say("복원할 백업이 없습니다.")
        return EXIT_OK
    r = store.restore(force=args.force)
    _say(f"복원 {len(r['restored'])}개, 삭제(새로 만든 파일) {len(r['removed'])}개, 이미 원본 {len(r['already'])}개")
    if r["skipped"]:
        _say("[경고] 패치 후 바뀐 파일은 건너뛰었습니다 (--force 로 강제 복원):")
        for rel in r["skipped"]:
            _say(f"  - {rel}")
        return EXIT_ERROR
    return EXIT_OK


def cmd_export_csv(args, ws: Workspace) -> int:
    eng = load_engine(args.game_dir, args.engine)
    res = extract_all(eng)
    if not res.ok:
        for n in res.notes:
            _say(f"[안내] {n}")
        return EXIT_ERROR
    out = args.out or str(ws.dir / "review.csv")
    ws.dir.mkdir(parents=True, exist_ok=True)
    tm = ws.open_tm()
    try:
        n = export_csv(out, res.units, tm, args.only_untranslated)
    finally:
        tm.close()
    _say(f"{n}행을 내보냈습니다: {out}")
    return EXIT_OK


def cmd_import_csv(args, ws: Workspace) -> int:
    eng = load_engine(args.game_dir, args.engine)
    glossary = ws.load_glossary()
    masker = eng.masker().with_extra(glossary.mask_patterns)
    tm = ws.open_tm()
    try:
        n, warns = import_csv(args.csv, tm, masker, eng.finalize)
    finally:
        tm.close()
    for w in warns:
        _say(f"[경고] {w}")
    _say(f"{n}행을 수동 번역으로 가져왔습니다.")
    return EXIT_OK


def main(argv: Optional[List[str]] = None, client=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 인코딩 오류 방지
        except (AttributeError, ValueError):
            pass
    ap = build_parser()
    args = ap.parse_args(argv)
    ws = Workspace(args.game_dir, args.work_dir, args.tm, args.glossary)
    try:
        if args.cmd == "translate":
            return cmd_translate(args, ws, client)
        return {
            "scan": cmd_scan, "extract": cmd_extract, "apply": cmd_apply, "restore": cmd_restore,
            "export-csv": cmd_export_csv, "import-csv": cmd_import_csv,
        }[args.cmd](args, ws)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
