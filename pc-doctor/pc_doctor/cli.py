"""pc-doctor 명령줄 진입점."""

import argparse
import json
import os
import shutil
import sys

from . import checks, gamemode, snapshot, tweaks, updates, upgrade
from . import config as cfgmod

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def _system():
    from .system import default_system

    return default_system()


def _load_snap(path, system, cfg):
    if path:
        return snapshot.load(path)
    return snapshot.collect(system, cfg)


def cmd_snapshot(a, system, cfg):
    snap = snapshot.collect(system, cfg)
    if a.print:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    if not a.no_save:
        print("💾 저장: %s" % snapshot.save(snap))
    mem = snap.get("memory") or {}
    print("CPU: %s | RAM: %s GB @ %s MT/s (%s개) | GPU: %s" % (
        (snap.get("cpu") or {}).get("name"), mem.get("total_gb"), mem.get("configured_speed"), mem.get("module_count"),
        ", ".join("%s %sGB" % (g.get("name"), g.get("vram_gb")) for g in snap.get("gpus") or []) or "?"))
    return 0


def cmd_diff(a, system, cfg):
    files = snapshot.list_snapshots()
    if a.old and a.new:
        old_p, new_p = a.old, a.new
    elif a.old:
        if not files:
            print("저장된 스냅샷이 없습니다.")
            return 1
        old_p, new_p = a.old, files[-1]
    else:
        if len(files) < 2:
            print("비교할 스냅샷이 2개 이상 필요합니다. (`pc-doctor snapshot`)")
            return 1
        old_p, new_p = files[-2], files[-1]
    old, new = snapshot.load(old_p), snapshot.load(new_p)
    d = snapshot.diff(old, new)
    print("비교: %s (%s) → %s (%s)" % (os.path.basename(old_p), old.get("taken_at"), os.path.basename(new_p), new.get("taken_at")))
    if d["highlights"]:
        print("\n중요 변경:")
        for sev, msg in d["highlights"]:
            print("  %s %s" % (checks.SEVERITY_LABEL.get(sev, sev), msg))
    if d["changes"]:
        print("\n전체 변경 (%d):" % len(d["changes"]))
        for k, o, n in d["changes"]:
            print("  %s: %s → %s" % (k, o, n))
    if not d["highlights"] and not d["changes"]:
        print("변경 없음")
    return 0


def cmd_check(a, system, cfg):
    snap = _load_snap(a.snapshot, system, cfg)
    if a.save and not a.snapshot:
        snapshot.save(snap)
    found = checks.run_checks(snap, cfg)
    if a.json:
        print(json.dumps([f.to_json() for f in found], ensure_ascii=False, indent=2))
    else:
        print("\n".join(checks.format_findings(found)))
    return 1 if any(f.severity == "critical" for f in found) else 0


def cmd_upgrade(a, system, cfg):
    snap = _load_snap(a.snapshot, system, cfg)
    print("\n".join(upgrade.format_advice(upgrade.advise(snap, cfg))))
    return 0


def cmd_tweaks(a, system, cfg):
    if a.tweaks_cmd == "list":
        for t in tweaks.all_tweaks():
            st = "" if a.no_status else " [%s]" % {"applied": "적용됨", "partial": "일부", "not_applied": "미적용",
                                                    "unknown": "?"}[tweaks.status(system, t)]
            flags = []
            if t.get("default"):
                flags.append("기본")
            if tweaks.is_admin_tweak(t):
                flags.append("관리자")
            if t.get("restart"):
                flags.append({"explorer": "탐색기 재시작", "logoff": "재로그인", "reboot": "재부팅"}[t["restart"]])
            print("%-26s %s%s  (%s)" % (t["id"], t["title"], st, ", ".join(flags)))
            print("%-26s   %s" % ("", t["description"]))
        print("\n※ Defender·UAC·방화벽·SmartScreen·VBS 등 보안 기능은 다루지 않습니다.")
        return 0
    if a.tweaks_cmd == "apply":
        r = tweaks.apply(system, ids=a.ids or None, admin=a.admin, dry_run=a.dry_run)
        if r["restart"] and not a.dry_run:
            if "explorer" in r["restart"] and a.restart_explorer:
                system.restart_explorer()
                print("🔄 탐색기를 다시 시작했습니다.")
                r["restart"].discard("explorer")
            for k in sorted(r["restart"]):
                print("ℹ️  " + tweaks.RESTART_MSG[k])
        if r["backup"]:
            print("되돌리기: pc-doctor tweaks restore")
        return 1 if r["errors"] else 0
    if a.tweaks_cmd == "restore":
        r = tweaks.restore(system, backup_path=a.backup, admin=a.admin, dry_run=a.dry_run)
        if r["restored"] and not a.dry_run:
            print("✅ 복원 완료. 탐색기 재시작/재로그인 후 반영됩니다.")
        return 1 if r["errors"] else 0
    if a.tweaks_cmd == "backups":
        for p, b in tweaks.list_backups(include_restored=True):
            print("%s  %s  %s  %s" % (b.get("created_at"), "복원됨" if b.get("restored_at") else "미복원",
                                      ",".join(b.get("tweaks", [])), p))
        return 0
    return 2


def cmd_updates(a, system, cfg):
    issues = updates.load_known_issues(a.issues)
    data = updates.collect(system, include_pending=not a.no_pending)
    ev = updates.evaluate(data, issues, recent_days=a.days or cfg.get("update_recent_days", 45),
                          stale_days=cfg.get("update_stale_days", 60))
    print("\n".join(updates.format_report(data, ev, issues)))
    return {"green": 0, "yellow": 0, "red": 1}[ev["level"]]


def cmd_gamemode(a, system, cfg):
    if a.gm_cmd == "on":
        return 0 if gamemode.turn_on(system, cfg, yes=a.yes, dry_run=a.dry_run) or a.dry_run else 1
    if a.gm_cmd == "off":
        return 0 if gamemode.turn_off(system, cfg) else 1
    if a.gm_cmd == "status":
        st = gamemode.load_state()
        print("게임 모드: %s" % ("ON" if st else "OFF"))
        if st:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    if a.gm_cmd == "watch":
        if not a.yes and not gamemode.default_confirm("게임이 감지되면 확인 없이 게임 모드를 켜고 끕니다. 시작할까요?"):
            return 1
        gamemode.watch(system, cfg)
        return 0
    return 2


def cmd_init(a, system, cfg):
    d = cfgmod.data_dir()
    for src, dst in (("config.example.json", "config.json"), ("known_issues.example.json", "known_issues.json")):
        target = os.path.join(d, dst)
        if os.path.exists(target):
            print("이미 있음: %s" % target)
        else:
            shutil.copyfile(os.path.join(EXAMPLES_DIR, src), target)
            print("생성: %s" % target)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="pc-doctor", description="C5 PC 닥터 — 내 PC 병목 분석·윈11 짜증 해결·업데이트 신호등·게임 모드")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="하드웨어/설정 스냅샷 저장")
    s.add_argument("--no-save", action="store_true")
    s.add_argument("--print", action="store_true", help="JSON 출력")
    s.set_defaults(fn=cmd_snapshot)

    s = sub.add_parser("diff", help="두 스냅샷 비교 (기본: 최근 2개)")
    s.add_argument("old", nargs="?")
    s.add_argument("new", nargs="?")
    s.set_defaults(fn=cmd_diff)

    s = sub.add_parser("check", help="규칙 기반 진단")
    s.add_argument("--snapshot", help="저장된 스냅샷 파일로 진단")
    s.add_argument("--save", action="store_true", help="수집한 스냅샷도 저장")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("upgrade", help="병목 분석 + 업그레이드 조언")
    s.add_argument("--snapshot")
    s.set_defaults(fn=cmd_upgrade)

    s = sub.add_parser("tweaks", help="윈11 짜증 해결 프로필")
    ts = s.add_subparsers(dest="tweaks_cmd", required=True)
    t = ts.add_parser("list")
    t.add_argument("--no-status", action="store_true", help="현재 적용 상태를 읽지 않음")
    t = ts.add_parser("apply")
    t.add_argument("ids", nargs="*", help="트윅 id (생략 시 기본 프로필)")
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--admin", action="store_true", help="HKLM(관리자) 항목도 적용 — 관리자 터미널 필요")
    t.add_argument("--restart-explorer", action="store_true")
    t = ts.add_parser("restore")
    t.add_argument("--backup", help="특정 백업 파일만 복원 (기본: 미복원 백업 전부, 최신부터)")
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--admin", action="store_true")
    ts.add_parser("backups")
    s.set_defaults(fn=cmd_tweaks)

    s = sub.add_parser("updates", help="윈도우 업데이트 신호등")
    s.add_argument("--issues", help="알려진 문제 목록 JSON 경로")
    s.add_argument("--days", type=int, help="'최근' 기준 일수")
    s.add_argument("--no-pending", action="store_true", help="대기 업데이트 검색 생략 (빠름)")
    s.set_defaults(fn=cmd_updates)

    s = sub.add_parser("gamemode", help="게임 모드 전환")
    gs = s.add_subparsers(dest="gm_cmd", required=True)
    g = gs.add_parser("on")
    g.add_argument("--yes", "-y", action="store_true")
    g.add_argument("--dry-run", action="store_true")
    gs.add_parser("off")
    gs.add_parser("status")
    g = gs.add_parser("watch")
    g.add_argument("--yes", "-y", action="store_true")
    s.set_defaults(fn=cmd_gamemode)

    s = sub.add_parser("init", help="예제 설정 파일을 데이터 폴더에 복사")
    s.set_defaults(fn=cmd_init)
    return p


def main(argv=None, system=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    cfg = cfgmod.load_config()
    try:
        return a.fn(a, system or _system(), cfg)
    except (tweaks.TweakError, gamemode.GameModeError) as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
