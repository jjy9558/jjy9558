"""devguard 명령줄. `python -m devguard <명령>` 또는 `python devguard_run.py <명령>`.

훅으로 호출되는 명령(precommit/prepush/strike/wallet stop-hook)은 내부 예외가 나도
Claude Code/git 을 막지 않도록 방어적으로 작성되어 있다.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import List, Optional, Sequence

from devguard import __version__
from devguard.common import eprint, read_stdin_text, setup_stdio, split_csv

HELP_EPILOG = """\
명령:
  install-git-hooks [repo]   pre-commit(비밀 검사) + pre-push(금요일 차단) 훅 설치
  uninstall-git-hooks [repo] 훅 제거 (기존 훅 복원)
  precommit                  (훅) 스테이징 비밀 검사
  prepush                    (훅) 금요일 배포 차단기
  push [--i-know] ...        git push 래퍼 (--i-know 로 금요일 차단 우회)
  strike                     (훅) 에이전트 파업 - Pre/PostToolUse 공용
  strike-status / strike-reset
  wallet report|alert|stop-hook|init
  scan <파일...>             파일에서 비밀 후보 찾기 (시험용)
  settings-snippet           .claude/settings.json hooks 블록 출력
                             (--with-sidekick PATH / --with-nagger / --merge FILE [--dry-run])
  permissions-report         권한 소음 측정: 자주 쓴 도구/명령 -> allow 후보 제안 (수정 안 함)
  wallet payslip --month M   야근 수당 명세서 (HTML)
"""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="devguard", description="개발자 안전장치 팩 v%s" % __version__,
                                epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("install-git-hooks", help="git 훅 설치")
    s.add_argument("repo", nargs="?", default=None)
    s.add_argument("--hooks", default="pre-commit,pre-push", help="쉼표 구분 (기본: pre-commit,pre-push)")
    s.add_argument("--python", default=None, help="훅이 쓸 python 실행 파일 (기본: 현재 인터프리터)")

    s = sub.add_parser("uninstall-git-hooks", help="git 훅 제거")
    s.add_argument("repo", nargs="?", default=None)
    s.add_argument("--hooks", default="pre-commit,pre-push")

    sub.add_parser("precommit", help="(훅) 스테이징 검사")

    s = sub.add_parser("prepush", help="(훅) 금요일 차단기")
    s.add_argument("--i-know", action="store_true", dest="i_know")
    s.add_argument("git_args", nargs="*")

    s = sub.add_parser("strike", help="(훅) 에이전트 파업")
    s.add_argument("--exit2", action="store_true", help="JSON 대신 종료코드 2 + stderr 로 신호")

    s = sub.add_parser("strike-status", help="파업 상태 보기")
    s = sub.add_parser("strike-reset", help="파업 상태 초기화")
    s.add_argument("--session", default=None)

    w = sub.add_parser("wallet", help="지갑 고발장")
    ws = w.add_subparsers(dest="wcmd")
    r = ws.add_parser("report", help="일별 보고서")
    r.add_argument("--days", type=int, default=1, help="최근 N일 (기본 1 = 오늘)")
    r.add_argument("--json", action="store_true")
    r.add_argument("--projects-dir", default=None)
    a = ws.add_parser("alert", help="오늘 지출 vs 한도")
    a.add_argument("--ntfy", default="", help="ntfy 토픽 URL (선택)")
    a.add_argument("--strict", action="store_true", help="초과 시 종료코드 1")
    a.add_argument("--projects-dir", default=None)
    sh = ws.add_parser("stop-hook", help="(Stop 훅) 오늘 지출 한 줄")
    sh.add_argument("--projects-dir", default=None)
    p2 = ws.add_parser("payslip", help="야근 수당 명세서 HTML")
    p2.add_argument("--month", default=None, help="YYYY-MM (기본: 이번 달)")
    p2.add_argument("--out", default=None, help="저장 경로 (기본 devguard-payslip-YYYY-MM.html, '-' 는 stdout)")
    p2.add_argument("--projects-dir", default=None)
    ws.add_parser("init", help="~/.devguard/config.json 템플릿 생성 (없을 때만)")

    s = sub.add_parser("scan", help="파일 비밀 검사 (시험용)")
    s.add_argument("files", nargs="+")

    s = sub.add_parser("settings-snippet", help="settings.json hooks 블록 출력")
    s.add_argument("--only", default="strike,wallet", help="strike,wallet 중 선택")
    s.add_argument("--python", default=None)
    s.add_argument("--no-stop", action="store_true", help="Stop 훅(지갑) 제외")
    s.add_argument("--with-sidekick", default=None, metavar="PATH",
                   help="s21-sidekick/hooks/claude_pretooluse.py 경로 (위험 명령 결재 훅, 가장 앞에 배치)")
    s.add_argument("--with-nagger", nargs="?", const="", default=None, metavar="PATH",
                   help="life-nagger 훅 스크립트 경로 (생략하면 형제 폴더에서 찾음, 없으면 건너뜀)")
    s.add_argument("--merge", default=None, metavar="SETTINGS_JSON",
                   help="출력 대신 이 파일에 중복 없이 병합 (.bak 백업 먼저)")
    s.add_argument("--dry-run", action="store_true", help="--merge 와 함께: diff 만 보여 주고 쓰지 않음")

    s = sub.add_parser("permissions-report", help="권한 소음 측정기 (allow 후보 제안만)")
    s.add_argument("--projects-dir", default=None)
    s.add_argument("--days", type=int, default=0, help="최근 N일 파일만 (기본 0 = 전체)")
    s.add_argument("--min-count", type=int, default=3)
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--json", action="store_true")
    return p


def _cmd_install(args) -> int:
    from devguard import githooks
    try:
        for m in githooks.install(args.repo, split_csv(args.hooks), args.python):
            print(m)
    except Exception as exc:  # noqa: BLE001
        eprint("[devguard] 설치 실패: %s" % exc)
        return 1
    print("완료. (훅이 안 먹으면 `git config core.hooksPath` 를 확인하세요.)")
    return 0


def _cmd_uninstall(args) -> int:
    from devguard import githooks
    try:
        for m in githooks.uninstall(args.repo, split_csv(args.hooks)):
            print(m)
    except Exception as exc:  # noqa: BLE001
        eprint("[devguard] 제거 실패: %s" % exc)
        return 1
    return 0


def _cmd_scan(args) -> int:
    from devguard import scanner
    from devguard.config import load_config
    cfg = load_config(os.getcwd())
    added = {}
    for fp in args.files:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                added[fp] = [(i, ln.rstrip("\r\n")) for i, ln in enumerate(f, 1)]
        except OSError as exc:
            eprint("읽기 실패 %s: %s" % (fp, exc))
    findings = scanner.scan_added_lines(added, list(args.files), scanner.Allowlist.load(os.getcwd()), cfg)
    if findings:
        eprint(scanner.format_report(findings))
        return 1
    print("비밀 후보 없음")
    return 0


def _cmd_strike(args) -> int:
    from devguard import strike
    code, out, err = strike.run_hook(read_stdin_text(), exit2=args.exit2)
    if out:
        sys.stdout.write(out + "\n")
        sys.stdout.flush()
    if err:
        sys.stderr.write(err + "\n")
        sys.stderr.flush()
    return code


def _cmd_wallet(args) -> int:
    from devguard import wallet
    from devguard.config import devguard_home, load_config
    sub = args.wcmd
    if sub == "stop-hook":
        out = wallet.run_stop_hook(read_stdin_text(), args.projects_dir or wallet.projects_dir())
        if out:
            sys.stdout.write(out + "\n")
        return 0
    cfg = load_config(os.getcwd())
    if sub == "init":
        path = os.path.join(devguard_home(), "config.json")
        if os.path.exists(path):
            print("이미 있습니다: %s (덮어쓰지 않음)" % path)
            return 0
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(wallet.CONFIG_TEMPLATE, f, ensure_ascii=False, indent=2)
        print("만들었습니다: %s\n단가(prices)를 공식 가격 페이지 기준으로 고친 뒤 prices_confirmed 를 true 로 바꾸세요." % path)
        return 0
    root = getattr(args, "projects_dir", None) or wallet.projects_dir()
    if sub == "payslip":
        return _cmd_payslip(args, cfg, root)
    if sub == "report":
        now = _dt.datetime.now().astimezone()
        since = wallet.start_of_day(now) - _dt.timedelta(days=max(args.days, 1) - 1)
        usages = wallet.load_usage(root, since)
        days = wallet.aggregate(usages, wallet.merged_prices(cfg["wallet"]))
        days = {d: r for d, r in days.items() if d >= since.date()}
        if args.json:
            print(wallet.report_json(days))
        else:
            print(wallet.format_report(days, bool(cfg["wallet"].get("prices_confirmed"))))
        return 0
    if sub == "alert":
        level = wallet.run_alert(root, cfg, ntfy_url=args.ntfy)
        return 1 if (args.strict and level == "over") else 0
    eprint("wallet report|alert|stop-hook|init 중 하나를 고르세요.")
    return 2


def _cmd_payslip(args, cfg, root) -> int:
    from devguard import payslip
    month = args.month or _dt.datetime.now().strftime("%Y-%m")
    tz = _dt.datetime.now().astimezone().tzinfo
    try:
        start, _end = payslip.month_bounds(month, tz)
    except ValueError as exc:
        eprint("[devguard] %s" % exc)
        return 2
    usages = wallet_load(root, start)
    rows = payslip.compute(usages, month, wallet_prices(cfg), tz)
    text = payslip.render_html(rows, month, bool(cfg["wallet"].get("prices_confirmed")))
    if args.out == "-":
        sys.stdout.write(text)
        return 0
    out = args.out or "devguard-payslip-%s.html" % month
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("명세서를 만들었습니다: %s" % out)
    return 0


def wallet_load(root, start):
    from devguard import wallet
    return wallet.load_usage(root, start)


def wallet_prices(cfg):
    from devguard import wallet
    return wallet.merged_prices(cfg["wallet"])


def _cmd_permissions(args) -> int:
    import time
    from devguard import permissions, wallet
    root = args.projects_dir or wallet.projects_dir()
    min_mtime = time.time() - args.days * 86400 if args.days > 0 else None
    tools, bash, total = permissions.count_tool_calls(root, min_mtime)
    sug = permissions.suggest(tools, bash, max(args.min_count, 1), max(args.top, 1))
    if args.json:
        print(permissions.report_json(tools, bash, total, sug))
    else:
        print(permissions.format_report(tools, bash, total, sug))
    return 0


def _cmd_snippet(args) -> int:
    from devguard import snippet
    only = split_csv(args.only)
    sidekick = args.with_sidekick
    nagger = args.with_nagger
    if sidekick and not os.path.isfile(sidekick):
        eprint("[devguard] 경고: sidekick 훅 파일이 없습니다 (경로만 그대로 사용): %s" % sidekick)
    if nagger is not None:
        if nagger == "":
            nagger = snippet.find_nagger_hook()
            if nagger is None:
                eprint("[devguard] life-nagger 에 Claude Code 훅이 없어 건너뜁니다 (경로를 직접 주려면 --with-nagger PATH).")
        elif not os.path.isfile(nagger):
            eprint("[devguard] 경고: nagger 훅 파일이 없습니다 (경로만 그대로 사용): %s" % nagger)
    data = snippet.build(args.python, None, only, stop=not args.no_stop,
                         sidekick=sidekick, nagger=nagger or None)
    if args.dry_run and not args.merge:
        eprint("[devguard] --dry-run 은 --merge 와 함께 쓰세요.")
        return 2
    if args.merge:
        try:
            info = snippet.merge_file(args.merge, data, dry_run=args.dry_run)
        except snippet.SettingsError as exc:
            eprint("[devguard] 병합 실패 (%s): %s" % (args.merge, exc))
            return 1
        except OSError as exc:
            eprint("[devguard] 병합 실패 (%s): %s" % (args.merge, exc))
            return 1
        if not info["changed"]:
            print("변경 없음: 이미 모두 들어 있습니다 (%s)" % args.merge)
            return 0
        sys.stdout.write(info["diff"])
        if args.dry_run:
            print("\n(dry-run: 파일을 수정하지 않았습니다)")
        else:
            print("\n병합 완료: %s%s" % (args.merge, ("  (백업: %s)" % info["backup"]) if info["backup"] else ""))
        return 0
    print(json.dumps(data, ensure_ascii=False, indent=2))
    eprint(snippet.NOTES)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)

    # git push 래퍼는 git 인자를 그대로 넘겨야 하므로 argparse 를 거치지 않는다
    if argv and argv[0] == "push":
        from devguard import friday
        return friday.push_wrapper(argv[1:])

    parser = _build_parser()
    args = parser.parse_args(argv)
    cmd = args.cmd

    # ---- 훅 진입점: 내부 오류는 절대 밖으로 새지 않는다 ----
    if cmd == "precommit":
        from devguard import precommit
        try:
            return precommit.run()
        except Exception:  # noqa: BLE001
            return 0
    if cmd == "prepush":
        from devguard import friday
        try:
            return friday.run(read_stdin_text(), i_know=args.i_know)
        except Exception:  # noqa: BLE001
            return 0
    if cmd == "strike":
        try:
            return _cmd_strike(args)
        except Exception:  # noqa: BLE001
            return 0

    if cmd == "install-git-hooks":
        return _cmd_install(args)
    if cmd == "uninstall-git-hooks":
        return _cmd_uninstall(args)
    if cmd == "strike-status":
        from devguard import strike
        print("\n".join(strike.status(strike.state_dir())))
        return 0
    if cmd == "strike-reset":
        from devguard import strike
        print("%d개 상태 파일 삭제" % strike.reset(strike.state_dir(), args.session))
        return 0
    if cmd == "wallet":
        if args.wcmd == "stop-hook":
            try:
                return _cmd_wallet(args)
            except Exception:  # noqa: BLE001
                return 0
        return _cmd_wallet(args)
    if cmd == "scan":
        return _cmd_scan(args)
    if cmd == "permissions-report":
        return _cmd_permissions(args)
    if cmd == "settings-snippet":
        return _cmd_snippet(args)
    parser.print_help()
    return 0
