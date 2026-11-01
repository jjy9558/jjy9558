"""게임 모드 프로필: 켜기 → 전원 옵션 고성능 + 사용자가 지정한 프로그램/서비스 종료, 끄기 → 원상 복구.

안전 장치
- 사용자가 config.json에 직접 적은 프로세스/서비스만 대상으로 합니다.
- 시스템 필수·보안 관련 항목은 적어 두어도 거부합니다 (PROTECTED_*).
- --yes 가 없으면 무엇을 할지 보여 주고 확인을 받습니다.
- 바꾸기 *전에* 이전 상태(전원 옵션 GUID, 종료한 프로그램 경로, 멈춘 서비스)를 state 파일에 저장합니다.
- 프로그램 종료는 강제(/F)가 아닌 정상 종료 요청입니다.
"""

import os
import time

from . import config as cfgmod
from .parsers import POWER_PLAN_GUIDS, GUID_RE, as_list, parse_powercfg_active, parse_sc_state, parse_tasklist
from .system import SAFE_PROCESS_RE, SAFE_SERVICE_RE

PROTECTED_PROCESSES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe",
    "sihost.exe", "ctfmon.exe", "audiodg.exe", "conhost.exe", "taskhostw.exe", "runtimebroker.exe",
    "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe", "securityhealthsystray.exe",
    "smartscreen.exe", "memcompression", "spoolsv.exe", "wudfhost.exe", "dllhost.exe",
    "python.exe", "pythonw.exe", "py.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "windowsterminal.exe",
}
PROTECTED_SERVICES = {
    "windefend", "wdnissvc", "sense", "mpssvc", "bfe", "wscsvc", "securityhealthservice", "eventlog",
    "rpcss", "rpceptmapper", "dcomlaunch", "lsm", "samss", "plugplay", "power", "profsvc", "schedule",
    "audiosrv", "audioendpointbuilder", "dhcp", "dnscache", "nsi", "netprofm", "nlasvc", "winmgmt",
    "cryptsvc", "keyiso", "vaultsvc", "gpsvc", "usermanager", "coremessagingregistrar", "brokerinfrastructure",
    "systemeventsbroker", "themes", "wuauserv", "trustedinstaller", "msiserver", "wlansvc", "netman",
    "nvdisplay.containerlocalsystem", "bits", "appinfo", "staterepository", "tokenbroker", "lanmanworkstation",
}


class GameModeError(Exception):
    pass


def state_path():
    return os.path.join(cfgmod.data_dir(), "gamemode_state.json")


def load_state():
    p = state_path()
    return cfgmod.read_json(p) if os.path.exists(p) else None


def validate_targets(gm_cfg):
    """(프로세스, 서비스, 거부 사유 목록)."""
    procs, svcs, refused = [], [], []
    for p in gm_cfg.get("stop_processes", []):
        low = p.strip().lower()
        if low in PROTECTED_PROCESSES:
            refused.append("%s: 시스템 필수/보안 프로세스라 종료하지 않습니다" % p)
        elif not SAFE_PROCESS_RE.match(p.strip()):
            refused.append("%s: 'name.exe' 형식이 아닙니다" % p)
        else:
            procs.append(low)
    for s in gm_cfg.get("stop_services", []):
        low = s.strip().lower()
        if low in PROTECTED_SERVICES:
            refused.append("%s: 시스템 필수/보안 서비스라 멈추지 않습니다" % s)
        elif not SAFE_SERVICE_RE.match(s.strip()):
            refused.append("%s: 서비스 이름 형식이 아닙니다" % s)
        else:
            svcs.append(s.strip())
    return procs, svcs, refused


def resolve_plan(value):
    v = (value or "high").strip().lower()
    if v in POWER_PLAN_GUIDS:
        return POWER_PLAN_GUIDS[v]
    m = GUID_RE.fullmatch(v)
    if m:
        return v
    raise GameModeError("power_plan은 saver/balanced/high/ultimate 또는 GUID여야 합니다: %r" % value)


def _ask(confirm, question):
    try:
        return confirm(question)
    except EOFError:
        return False


def default_confirm(q):
    return input(q + " [y/N] ").strip().lower() in ("y", "yes", "ㅛ")


def turn_on(system, cfg, yes=False, dry_run=False, confirm=default_confirm, out=print, auto=False):
    if load_state():
        out("이미 게임 모드입니다. (`gamemode off`로 먼저 복원)")
        return False
    gm = cfg.get("gamemode", {})
    procs, svcs, refused = validate_targets(gm)
    for r in refused:
        out("🚫 " + r)
    target = resolve_plan(gm.get("power_plan"))
    current = parse_powercfg_active(system.powercfg_active_raw())
    running = parse_tasklist(system.tasklist_raw())
    kill = [p for p in procs if p in running]
    stop = [s for s in svcs if parse_sc_state(system.service_query_raw(s)) == "RUNNING"]

    out("🎮 게임 모드 계획")
    out("  전원 옵션: %s → %s" % ((current or {}).get("name") or "?", target))
    out("  종료할 프로그램: %s" % (", ".join(kill) or "없음"))
    out("  멈출 서비스: %s%s" % (", ".join(stop) or "없음", " (관리자 권한 필요)" if stop and not system.is_admin() else ""))
    if dry_run:
        out("[dry-run] 아무것도 바꾸지 않았습니다.")
        return False
    if not yes and not _ask(confirm, "진행할까요?"):
        out("취소했습니다.")
        return False

    paths = {}
    if kill and gm.get("relaunch_processes", True):
        for p in as_list(system.ps_json("processes")):
            name = ((p.get("Name") or "") + ".exe").lower()
            if name in kill and p.get("Path"):
                paths.setdefault(name, p["Path"])
    state = {
        "auto": auto,
        "previous_power_plan": (current or {}).get("guid"),
        "processes": [{"name": k, "path": paths.get(k)} for k in kill],
        "services": [],
    }
    # 바꾸기 전에 저장 → 중간에 죽어도 off로 복구 가능
    cfgmod.write_json_atomic(state_path(), state)

    if not system.powercfg_setactive(target):
        out("⚠️  전원 옵션 변경 실패 (해당 구성표가 이 PC에 없을 수 있음: powercfg /list 확인)")
    for k in kill:
        out(("  • 종료 요청: %s" % k) if system.kill_process(k) else ("  ⚠️  종료 실패: %s" % k))
    for s in stop:
        if system.service_stop(s):
            state["services"].append(s)
            out("  • 서비스 중지: %s" % s)
        else:
            out("  ⚠️  서비스 중지 실패: %s" % s)
    cfgmod.write_json_atomic(state_path(), state)
    out("✅ 게임 모드 ON")
    return True


def turn_off(system, cfg, out=print):
    state = load_state()
    if not state:
        out("게임 모드가 켜져 있지 않습니다.")
        return False
    ok = True
    prev = state.get("previous_power_plan")
    if prev:
        if system.powercfg_setactive(prev):
            out("  • 전원 옵션 복원: %s" % prev)
        else:
            ok = False
            out("  ⚠️  전원 옵션 복원 실패: %s" % prev)
    for s in state.get("services", []):
        if system.service_start(s):
            out("  • 서비스 시작: %s" % s)
        else:
            ok = False
            out("  ⚠️  서비스 시작 실패: %s" % s)
    if cfg.get("gamemode", {}).get("relaunch_processes", True):
        running = parse_tasklist(system.tasklist_raw())
        for p in state.get("processes", []):
            if p["name"] in running:
                continue
            if p.get("path") and system.launch(p["path"]):
                out("  • 다시 실행: %s" % p["name"])
            else:
                out("  ℹ️  %s 은(는) 직접 실행해 주세요" % p["name"])
    if ok:
        os.remove(state_path())
        out("✅ 게임 모드 OFF (원래 상태로 복원)")
    else:
        out("일부 복원 실패 — state 파일을 남겨 두었습니다. 문제 해결 후 다시 `gamemode off`.")
    return ok


def watch(system, cfg, out=print, sleep=time.sleep, max_iterations=None):
    """게임 exe가 보이면 켜고, 모두 꺼지면 끕니다 (watch가 켠 경우에만 자동으로 끔)."""
    gm = cfg.get("gamemode", {})
    games = set(g.lower() for g in gm.get("game_exes", []))
    if not games:
        raise GameModeError("config.json의 gamemode.game_exes가 비어 있습니다.")
    interval = max(int(gm.get("poll_seconds", 10)), 2)
    out("👀 감시 중: %s (%d초 간격, Ctrl+C로 종료)" % (", ".join(sorted(games)), interval))
    i = 0
    try:
        while max_iterations is None or i < max_iterations:
            i += 1
            running = parse_tasklist(system.tasklist_raw())
            hit = games & running
            st = load_state()
            if hit and not st:
                out("🎮 감지: %s" % ", ".join(sorted(hit)))
                turn_on(system, cfg, yes=True, out=out, auto=True)
            elif not hit and st and st.get("auto"):
                out("🛑 게임 종료 감지")
                turn_off(system, cfg, out=out)
            sleep(interval)
    except KeyboardInterrupt:
        out("감시 종료")
        st = load_state()
        if st and st.get("auto"):
            turn_off(system, cfg, out=out)
