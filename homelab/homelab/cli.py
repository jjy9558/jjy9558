"""명령줄 진입점: python -m homelab <명령>."""
import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import TextIO

from . import __version__
from . import doctor as doctor_mod
from . import initcmd, jobs, paths, procs, schedule, secretstore, status, tailscale
from .registry import JOBS, SERVICES, Ctx

EXIT_USAGE = 2


def _csv(text):
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="homelab", description="한 번에 켜고 끄는 홈랩 런처")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    p.add_argument("--dir", help="홈랩 폴더 (기본 ~/.homelab, 환경변수 HOMELAB_DIR)")
    p.add_argument("--home", help="프로젝트 홈 설정(~/.phone-home 등)의 기준 폴더 (기본: 사용자 홈)")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="토큰 생성 + 프로젝트별 설정 파일 쓰기 (대화식 또는 플래그)")
    i.add_argument("--yes", "-y", action="store_true", help="묻지 않고 플래그/기본값으로 진행")
    i.add_argument("--force", action="store_true", help="이미 있는 설정도 덮어씀 (먼저 .bak-시각 으로 백업)")
    i.add_argument("--dry-run", action="store_true", help="아무것도 쓰지 않고 diff 만 보여 줌")
    i.add_argument("--rotate-tokens", action="store_true", help="모든 토큰을 새로 만듦 (--force 필요)")
    i.add_argument("--python", help="서버를 켤 python 경로 (기본: 지금 실행 중인 python)")
    i.add_argument("--repo-root", help="프로젝트 폴더들이 들어 있는 저장소 폴더")
    i.add_argument("--llm-url", help="로컬 LLM 주소 (예: http://127.0.0.1:8080/v1)")
    i.add_argument("--enable", help="켤 서비스 추가 (쉼표): " + ", ".join(SERVICES))
    i.add_argument("--disable", help="끌 서비스 (쉼표)")
    i.add_argument("--hub-host", help="자비스 허브가 열릴 주소 (기본 127.0.0.1)")
    i.add_argument("--hub-url", help="워커가 붙을 허브 주소")
    i.add_argument("--wol-mac", help="5080 PC MAC (Wake-on-LAN)")
    i.add_argument("--wol-broadcast", help="WOL 브로드캐스트 주소")
    i.add_argument("--minipc", action=argparse.BooleanOptionalAction, default=None, help="미니PC 워커 설정 파일도 생성")
    i.add_argument("--ntfy", action=argparse.BooleanOptionalAction, default=None, help="ntfy 푸시 사용 (무작위 토픽)")
    i.add_argument("--wallet-limit", help="Claude 사용량 일일 한도 USD (0=알림 없음)")
    i.add_argument("--redact-words", help="TIL 초안에서 추가로 가릴 단어 (쉼표)")
    i.add_argument("--gm-world", help="gm-trpg 세계 이름 (주면 gm-trpg 도 켬)")
    i.add_argument("--appids", help="game-calendar 가 추적할 Steam appid (쉼표)")
    i.add_argument("--morning-gate", action=argparse.BooleanOptionalAction, default=None,
                   help="알람 승인 뒤 아침 브리핑 낭독")
    i.add_argument("--tailnet-host", help="tailscale 안내문에 넣을 호스트 이름")

    u = sub.add_parser("up", help="서버 켜기 (백그라운드)")
    u.add_argument("--only", help="이 서비스만 (쉼표)")
    u.add_argument("--announce", action="store_true", help="다 켜진 뒤 사이드킥 폰이 한마디 해 줌")

    d = sub.add_parser("down", help="서버 끄기")
    d.add_argument("--only", help="이 서비스만 (쉼표)")

    s = sub.add_parser("status", help="상태 표")
    s.add_argument("--only", help="이 서비스만 (쉼표)")
    s.add_argument("--all", action="store_true", help="비활성 서비스도 표시")

    lg = sub.add_parser("logs", help="로그 보기")
    lg.add_argument("name", help="서비스 또는 작업 이름")
    lg.add_argument("-n", "--lines", type=int, default=40)
    lg.add_argument("-f", "--follow", action="store_true")

    sc = sub.add_parser("schedule", help="매일 도는 작업 등록 (schtasks / cron)")
    g = sc.add_mutually_exclusive_group()
    g.add_argument("--print", dest="do_print", action="store_true", help="명령만 출력 (기본)")
    g.add_argument("--install", action="store_true", help="schtasks 로 실제 등록 (윈도우, 확인 후)")
    sc.add_argument("--only", help="이 작업만 (쉼표): " + ", ".join(JOBS))
    sc.add_argument("--yes", "-y", action="store_true", help="--install 확인 생략")
    sc.add_argument("--hidden", action="store_true", help="pythonw 가 있으면 콘솔 창 없이 실행")

    t = sub.add_parser("tailscale", help="tailscale serve 명령 안내")
    t.add_argument("--print", dest="do_print", action="store_true", help="명령만 출력 (기본)")
    t.add_argument("--show-token", action="store_true", help="폰 주소에 실제 토큰을 넣어 출력")

    dr = sub.add_parser("doctor", help="설치 점검")
    dr.add_argument("--llm-url", help="이 주소로 LLM 응답 확인")

    r = sub.add_parser("run", help="주기 작업 한 번 실행 (스케줄러가 부름): " + ", ".join(JOBS))
    r.add_argument("job", choices=list(JOBS))
    r.add_argument("extra", nargs=argparse.REMAINDER, help="작업에 그대로 넘길 추가 인자")

    tk = sub.add_parser("token", help="토큰 보기")
    tk.add_argument("action", choices=["list", "show"])
    tk.add_argument("name", nargs="?")

    h = sub.add_parser("hooks", help="Claude Code settings.json 에 넣을 훅 블록 출력 (dev-guard + 사이드킥 결재)")
    h.add_argument("--out", help="이 파일에 저장 (기본: 화면 출력)")
    return p


def _layout(args) -> paths.Layout:
    home = Path(args.home).expanduser() if args.home else Path.home()
    root = Path(args.dir).expanduser() if args.dir else paths.default_root(home=home)
    return paths.Layout(root, home)


def load_ctx(args, out: TextIO) -> Ctx | None:
    layout = _layout(args)
    if not layout.settings_path.exists():
        print("아직 설정이 없어요: %s\n먼저 `homelab init` 을 실행하세요." % layout.settings_path, file=out)
        return None
    try:
        settings = paths.validate_settings(paths.load_settings(layout), list(SERVICES))
        secrets = secretstore.load(layout.secrets_path)
    except (paths.SettingsError, secretstore.SecretsError) as e:
        print("❌ %s" % e, file=out)
        return None
    return Ctx(layout, settings, secrets)


def _pick(names: list[str], only: str | None, universe, out: TextIO):
    if not only:
        return names
    want = _csv(only)
    bad = [n for n in want if n not in universe]
    if bad:
        print("알 수 없는 이름: %s (가능: %s)" % (", ".join(bad), ", ".join(universe)), file=out)
        return None
    return [n for n in universe if n in want]


# ---------------------------------------------------------------------------------------
def cmd_init(args, out, **kw) -> int:
    return initcmd.run_init(_layout(args), args, out, ask=kw.get("ask", input), is_tty=kw.get("is_tty"))


def cmd_up(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    names = _pick(ctx.enabled_services(), args.only, list(SERVICES), out)
    if names is None:
        return EXIT_USAGE
    mgr = kw.get("manager") or procs.Manager(ctx)
    icons = {"started": "✅", "already": "✅", "slow": "⚠️ ", "conflict": "❌", "failed": "❌", "skipped": "⬜"}
    bad = 0
    results = []
    failed_names: set[str] = set()
    for n in names:
        blocked = [d for d in SERVICES[n].needs if d in failed_names]
        if blocked:
            r = procs.Outcome(n, "skipped", "%s 가 안 켜져서 건너뛰었어요" % ", ".join(blocked))
            failed_names.add(n)
        else:
            r = mgr.start(n)
            if r.status in ("conflict", "failed"):
                failed_names.add(n)
        results.append(r)
        bad += r.status in ("conflict", "failed")
        print("%s %s: %s" % (icons.get(r.status, "·"), n, r.message), file=out)
    print("", file=out)
    rows = [mgr.status_of(n) for n in names]
    for line in status.render_status(rows):
        print(line, file=out)
    if ctx.is_enabled("phone-home") and any(r.name == "phone-home" and r.ok for r in results):
        print("\n폰 연결: homelab tailscale --print", file=out)
    if args.announce and not bad:
        _announce(ctx, out)
    return 1 if bad else 0


def _announce(ctx: Ctx, out: TextIO) -> None:
    from ._jjkit import notify
    if not ctx.is_enabled("sidekick"):
        return
    try:
        notify.sidekick_say("홈랩이 켜졌어요.", token=ctx.token("sidekick"), url=ctx.url("sidekick"),
                            module="homelab")
        print("📣 사이드킥이 말했어요.", file=out)
    except notify.NotifyError as e:
        print("📣 말하기는 건너뛰었어요 (%s)" % e, file=out)


def cmd_down(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    names = _pick(list(SERVICES), args.only, list(SERVICES), out)
    if names is None:
        return EXIT_USAGE
    mgr = kw.get("manager") or procs.Manager(ctx)
    bad = 0
    for r in mgr.down(names):
        if r.status == "not-running" and not args.only:
            continue  # 전체 끄기에서는 원래 꺼져 있던 건 조용히
        bad += r.status == "failed"
        print("%s %s: %s" % ("✅" if r.ok else "❌", r.name, r.message), file=out)
    return 1 if bad else 0


def cmd_status(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    names = _pick(list(SERVICES), args.only, list(SERVICES), out)
    if names is None:
        return EXIT_USAGE
    mgr = kw.get("manager") or procs.Manager(ctx)
    rows = [mgr.status_of(n) for n in names]
    if not args.all and not args.only:
        rows = [r for r in rows if r.enabled or r.running or r.port_open]
    for line in status.render_status(rows):
        print(line, file=out)
    return 0


def cmd_logs(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    if args.name in SERVICES:
        path = ctx.layout.log_file(args.name)
    elif args.name in JOBS:
        path = ctx.layout.log_file("job-" + args.name)
    else:
        print("알 수 없는 이름: %s (서비스: %s / 작업: %s)" % (args.name, ", ".join(SERVICES), ", ".join(JOBS)), file=out)
        return EXIT_USAGE
    lines = procs.tail_lines(path, args.lines)
    if len(lines) < args.lines:
        older = procs.tail_lines(path.with_name(path.name + ".1"), args.lines - len(lines))
        lines = older + lines
    if not lines and not path.exists():
        print("아직 로그가 없어요: %s" % path, file=out)
        return 0
    for ln in lines:
        print(ln, file=out)
    if args.follow:  # pragma: no cover - 대화형
        try:
            pos = path.stat().st_size if path.exists() else 0
            while True:
                time.sleep(0.5)
                if not path.exists():
                    continue
                size = path.stat().st_size
                if size < pos:
                    pos = 0
                if size > pos:
                    with open(path, "rb") as f:
                        f.seek(pos)
                        out.write(f.read().decode("utf-8", "replace"))
                        out.flush()
                    pos = size
        except KeyboardInterrupt:
            pass
    return 0


def cmd_schedule(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    only = _pick(list(JOBS), args.only, list(JOBS), out)
    if only is None:
        return EXIT_USAGE
    try:
        tasks = schedule.build_tasks(ctx, only, hidden=args.hidden)
    except schedule.ScheduleError as e:
        print("❌ %s" % e, file=out)
        return 2
    try:
        if not args.install:
            for line in schedule.print_lines(ctx, tasks):
                print(line, file=out)
            return 0
        platform = kw.get("platform", sys.platform)
        if platform != "win32":
            print("`--install` 은 윈도우에서만 돼요. --print 로 cron 줄을 확인하세요.", file=out)
            return 2
        print("다음 작업 %d개를 작업 스케줄러에 등록합니다 (같은 이름이 있으면 덮어씀):" % len(tasks), file=out)
        for t in tasks:
            print("  - %s: %s" % (t.name, schedule.when_text(t)), file=out)
        if not args.yes:
            ans = kw.get("ask", input)("계속할까요? [y/N] ").strip().lower()
            if ans not in ("y", "yes", "예", "ㅇ"):
                print("취소했어요.", file=out)
                return 1
        return schedule.install(tasks, out, run=kw.get("run", schedule.subprocess.run), platform=platform)
    except schedule.ScheduleError as e:
        print("❌ %s" % e, file=out)
        return 2


def cmd_tailscale(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    for line in tailscale.tailscale_lines(ctx, show_token=args.show_token):
        print(line, file=out)
    return 0


def cmd_doctor(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    checks = doctor_mod.run_doctor(ctx, kw.get("deps"), llm_url=args.llm_url)
    for line in doctor_mod.render(checks):
        print(line, file=out)
    return doctor_mod.exit_code(checks)


def cmd_run(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    extra = list(args.extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    return jobs.run_job(ctx, args.job, extra, out)


def cmd_token(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    if args.action == "list":
        for k, desc in {**secretstore.TOKEN_NAMES, **secretstore.EXTRA_NAMES}.items():
            has = k in ctx.secrets["tokens"] or k in ctx.secrets["extras"]
            print("%-24s %s%s" % (k, desc, "" if has else "  (없음)"), file=out)
        return 0
    if not args.name:
        print("이름을 주세요. 목록: homelab token list", file=out)
        return EXIT_USAGE
    val = ctx.secrets["tokens"].get(args.name) or ctx.secrets["extras"].get(args.name)
    if not val:
        print("그런 토큰이 없어요: %s (homelab token list)" % args.name, file=out)
        return 1
    print(val, file=out)
    return 0


def cmd_hooks(args, out, **kw) -> int:
    ctx = load_ctx(args, out)
    if ctx is None:
        return EXIT_USAGE
    run = kw.get("hooks_run") or doctor_mod._real_run
    hook = ctx.project("s21-sidekick") / "hooks" / "claude_pretooluse.py"
    guard = ctx.project("dev-guard")
    argv = [ctx.python, "-m", "devguard", "settings-snippet", "--python", ctx.python]
    if ctx.is_enabled("sidekick") and hook.exists():
        argv += ["--with-sidekick", str(hook)]
    rc, text = run(argv, guard)
    if rc != 0:
        print("❌ dev-guard settings-snippet 실행에 실패했어요:\n%s" % text.strip()[:400], file=out)
        return 1
    try:
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        print("❌ 출력을 해석하지 못했어요:\n%s" % text.strip()[:400], file=out)
        return 1
    cfg_arg = str(ctx.cfg("sidekick"))
    quoted = '"%s"' % cfg_arg if os.name == "nt" else shlex.quote(cfg_arg)
    changed = 0
    for groups in (data.get("hooks") or {}).values():
        for g in groups if isinstance(groups, list) else []:
            for h in g.get("hooks", []):
                cmd = h.get("command")
                if isinstance(cmd, str) and "claude_pretooluse.py" in cmd and "--config" not in cmd:
                    h["command"] = cmd + " --config " + quoted
                    changed += 1
    text_out = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        print("저장했어요: %s" % args.out, file=out)
    else:
        out.write(text_out)
    print("", file=out)
    print("→ 이 JSON 의 hooks 블록을 ~/.claude/settings.json 에 직접 붙여 넣으세요 (홈랩은 settings.json 을 고치지 않아요).", file=out)
    if changed:
        print("→ 사이드킥 결재 훅에 --config %s 를 붙였어요. 사이드킥 서버가 켜져 있어야 해요." % cfg_arg, file=out)
    return 0


COMMANDS = {"init": cmd_init, "up": cmd_up, "down": cmd_down, "status": cmd_status, "logs": cmd_logs,
            "schedule": cmd_schedule, "tailscale": cmd_tailscale, "doctor": cmd_doctor, "run": cmd_run,
            "token": cmd_token, "hooks": cmd_hooks}


def main(argv: list[str] | None = None, out: TextIO | None = None, **kw) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글/이모지로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return COMMANDS[args.cmd](args, out or sys.stdout, **kw)
