"""PC 설정 스냅샷 수집·저장·비교."""

import datetime as _dt
import os
import re
import socket

from . import config as cfgmod
from .parsers import (
    as_list,
    guess_channel,
    guess_rated_speed_from_part,
    parse_nvidia_smi,
    parse_powercfg_active,
    to_int,
)

SCHEMA = 1
GB = 1024 ** 3


def _gb(x, nd=1):
    v = to_int(x)
    return round(v / GB, nd) if v is not None else None


# ---------------------------------------------------------------------------
# GGUF 모델 스캔 (llama.cpp)
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"^(.*)-\d{5}-of-\d{5}\.gguf$", re.IGNORECASE)


def scan_models(dirs):
    """GGUF 파일 크기 수집. 분할 파일(-00001-of-00003.gguf)은 하나로 합칩니다."""
    groups = {}
    for d in dirs or []:
        d = os.path.expandvars(os.path.expanduser(d))
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith(".gguf"):
                    continue
                full = os.path.join(root, fn)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                m = _SPLIT_RE.match(fn)
                key = os.path.join(root, m.group(1) if m else fn[:-5])
                groups[key] = groups.get(key, 0) + size
    files = [{"name": os.path.basename(k), "size_gb": round(v / GB, 1)}
             for k, v in sorted(groups.items(), key=lambda kv: -kv[1])]
    return {"files": files, "largest_gb": files[0]["size_gb"] if files else None}


# ---------------------------------------------------------------------------
# 정규화
# ---------------------------------------------------------------------------


def normalize(raw, powercfg_raw=None, nvidia_raw=None, models=None, taken_at=None, hostname=None):
    """PowerShell 원시 JSON + 기타 출력 → pc-doctor 스냅샷 dict."""
    raw = raw or {}
    snap = {
        "schema": SCHEMA,
        "taken_at": taken_at or _dt.datetime.now().isoformat(timespec="seconds"),
        "hostname": hostname or socket.gethostname(),
    }

    cpus = as_list(raw.get("cpu"))
    if cpus:
        c = cpus[0]
        snap["cpu"] = {
            "name": (c.get("Name") or "").strip(),
            "cores": sum(to_int(x.get("NumberOfCores"), 0) for x in cpus),
            "threads": sum(to_int(x.get("NumberOfLogicalProcessors"), 0) for x in cpus),
            "max_mhz": to_int(c.get("MaxClockSpeed")),
        }
    else:
        snap["cpu"] = None

    board = raw.get("board") or {}
    bios = raw.get("bios") or {}
    snap["board"] = {"manufacturer": board.get("Manufacturer"), "product": board.get("Product")}
    snap["bios"] = {"version": bios.get("SMBIOSBIOSVersion"), "date": bios.get("ReleaseDate")}

    # --- 메모리 ---
    mods = []
    for m in as_list(raw.get("memory")):
        mods.append(
            {
                "capacity_gb": _gb(m.get("Capacity"), 0),
                "speed": to_int(m.get("Speed")),
                "configured_speed": to_int(m.get("ConfiguredClockSpeed")),
                "bank": (m.get("BankLabel") or "").strip(),
                "locator": (m.get("DeviceLocator") or "").strip(),
                "manufacturer": (m.get("Manufacturer") or "").strip(),
                "part": (m.get("PartNumber") or "").strip(),
            }
        )
    computer = raw.get("computer") or {}
    total_gb = sum(m["capacity_gb"] or 0 for m in mods) or _gb(computer.get("TotalPhysicalMemory"), 0)
    configured = [m["configured_speed"] for m in mods if m["configured_speed"]]
    spd = [m["speed"] for m in mods if m["speed"]]
    part_guess = [guess_rated_speed_from_part(m["part"]) for m in mods]
    part_guess = [p for p in part_guess if p]
    channels = [guess_channel(m["bank"], m["locator"]) for m in mods]
    snap["memory"] = {
        "total_gb": total_gb,
        "modules": mods,
        "module_count": len(mods),
        "slots": to_int(raw.get("memory_slots")),
        "configured_speed": min(configured) if configured else None,
        "spd_speed": max(spd) if spd else None,
        "part_rated_speed": max(part_guess) if part_guess else None,
        "channels": sorted(set(c for c in channels if c)) if all(channels) and channels else None,
    }

    # --- OS ---
    os_ = raw.get("os") or {}
    extra = raw.get("os_extra") or {}
    snap["os"] = {
        "caption": os_.get("Caption"),
        "version": os_.get("Version"),
        "build": to_int(os_.get("BuildNumber")),
        "ubr": to_int(extra.get("UBR")),
        "display_version": extra.get("DisplayVersion"),
        "free_mem_gb": round(to_int(os_.get("FreePhysicalMemory"), 0) / 1024 / 1024, 1) if os_.get("FreePhysicalMemory") else None,
        "visible_mem_gb": round(to_int(os_.get("TotalVisibleMemorySize"), 0) / 1024 / 1024, 1) if os_.get("TotalVisibleMemorySize") else None,
    }

    # --- GPU ---
    nv = parse_nvidia_smi(nvidia_raw)
    gpus = []
    for g in as_list(raw.get("gpu")):
        name = (g.get("Name") or "").strip()
        entry = {
            "name": name,
            "driver_version": g.get("DriverVersion"),
            "driver_date": g.get("DriverDate"),
            # AdapterRAM은 32비트라 4GB에서 잘림 → nvidia-smi가 있으면 그 값 사용
            "vram_gb": _gb(g.get("AdapterRAM")),
            "vram_source": "wmi(4GB 상한)",
        }
        match = next((n for n in nv if n["name"] and (n["name"] in name or name in n["name"])), None)
        if match:
            entry["nvidia_driver"] = match["driver_version"]
            if match["vram_mb"]:
                entry["vram_gb"] = round(match["vram_mb"] / 1024, 1)
                entry["vram_source"] = "nvidia-smi"
            entry["pcie_width_current"] = match["pcie_width_current"]
            entry["pcie_width_max"] = match["pcie_width_max"]
            entry["pcie_gen_max"] = match["pcie_gen_max"]
        gpus.append(entry)
    if not gpus:
        for n in nv:
            gpus.append({"name": n["name"], "nvidia_driver": n["driver_version"], "driver_version": None,
                         "driver_date": None, "vram_gb": round((n["vram_mb"] or 0) / 1024, 1),
                         "vram_source": "nvidia-smi", "pcie_width_current": n["pcie_width_current"],
                         "pcie_width_max": n["pcie_width_max"], "pcie_gen_max": n["pcie_gen_max"]})
    snap["gpus"] = gpus

    # --- 디스크 ---
    phys = {str(p.get("DeviceId")): p for p in as_list(raw.get("physical_disks"))}
    disks = []
    for v in as_list(raw.get("volumes")):
        size = to_int(v.get("Size"), 0)
        free = to_int(v.get("SizeRemaining"), 0)
        p = phys.get(str(v.get("DiskNumber")), {})
        disks.append(
            {
                "drive": (v.get("DriveLetter") or "").upper(),
                "label": v.get("Label") or "",
                "size_gb": round(size / GB, 1),
                "free_gb": round(free / GB, 1),
                "used_percent": round(100.0 * (size - free) / size, 1) if size else None,
                "media_type": p.get("MediaType") or "Unknown",
                "bus_type": p.get("BusType") or "Unknown",
                "disk_name": p.get("FriendlyName") or "",
                "health": p.get("HealthStatus") or "",
            }
        )
    snap["disks"] = sorted(disks, key=lambda d: d["drive"])

    snap["power_plan"] = parse_powercfg_active(powercfg_raw)

    dg = raw.get("device_guard")
    gm = raw.get("game_mode")
    hags = to_int(raw.get("hags"))
    snap["features"] = {
        # AutoGameModeEnabled 값이 없으면 Windows 기본값(켜짐)
        "game_mode": True if gm is None else bool(to_int(gm, 1)),
        "hags": None if hags is None else hags == 2,
        "vbs_running": None if not dg else to_int(dg.get("VirtualizationBasedSecurityStatus")) == 2,
        "hvci_running": None if not dg else 2 in [to_int(x) for x in as_list(dg.get("SecurityServicesRunning"))],
    }

    startup = as_list(raw.get("startup"))
    snap["startup"] = {"count": len(startup), "names": sorted(set((s.get("Name") or "") for s in startup))}

    pf = as_list(raw.get("pagefile"))
    snap["pagefile"] = {
        "auto_managed": computer.get("AutomaticManagedPagefile"),
        "files": [{"name": p.get("Name"), "size_mb": to_int(p.get("AllocatedBaseSize"))} for p in pf],
    }

    snap["processes"] = {
        "top_memory": [{"name": p.get("Name"), "mb": to_int(p.get("WorkingSetMB"))} for p in as_list(raw.get("top_memory"))],
        "top_cpu": [{"name": p.get("Name"), "cpu_s": p.get("CPUSeconds")} for p in as_list(raw.get("top_cpu"))],
    }
    snap["models"] = models or {"files": [], "largest_gb": None}
    return snap


def collect(system, cfg):
    raw = system.ps_json("snapshot")
    if not isinstance(raw, dict):
        raw = {}
    return normalize(
        raw,
        powercfg_raw=system.powercfg_active_raw(),
        nvidia_raw=system.nvidia_smi_raw(),
        models=scan_models(cfg.get("model_dirs")),
    )


# ---------------------------------------------------------------------------
# 저장/불러오기
# ---------------------------------------------------------------------------


def snapshot_dir():
    return cfgmod.sub_dir("snapshots")


def save(snap, directory=None):
    directory = directory or snapshot_dir()
    stamp = snap["taken_at"].replace(":", "").replace("-", "").replace("T", "-")
    path = os.path.join(directory, "snapshot-%s.json" % stamp)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(directory, "snapshot-%s-%d.json" % (stamp, n))
    cfgmod.write_json_atomic(path, snap)
    return path


def list_snapshots(directory=None):
    directory = directory or snapshot_dir()
    files = [f for f in os.listdir(directory) if f.startswith("snapshot-") and f.endswith(".json")]
    return [os.path.join(directory, f) for f in sorted(files)]


def load(path):
    return cfgmod.read_json(path)


# ---------------------------------------------------------------------------
# 비교
# ---------------------------------------------------------------------------

VOLATILE_PREFIXES = ("taken_at", "processes", "os.free_mem_gb", "startup.names", "models.files")


def _keyed(snap):
    s = dict(snap)
    s["disks"] = {d.get("drive") or str(i): d for i, d in enumerate(snap.get("disks") or [])}
    s["gpus"] = {str(i): g for i, g in enumerate(snap.get("gpus") or [])}
    return s


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, "%s.%s" % (prefix, k) if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, "%s[%d]" % (prefix, i)))
    else:
        out[prefix] = obj
    return out


def _g(snap, *path):
    cur = snap
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and p < len(cur):
            cur = cur[p]
        else:
            return None
    return cur


def diff(old, new):
    """두 스냅샷 비교 → {'highlights': [(severity, msg)], 'changes': [(key, old, new)]}."""
    hl = []

    o, n = _g(old, "memory", "configured_speed"), _g(new, "memory", "configured_speed")
    if o and n and n < o:
        hl.append(("critical", "RAM 동작 속도 %d → %d MT/s 하락! BIOS 초기화·업데이트로 XMP/EXPO가 꺼졌을 가능성이 큽니다. BIOS에서 XMP/EXPO 프로필을 다시 켜세요." % (o, n)))
    elif o and n and n > o:
        hl.append(("info", "RAM 동작 속도 %d → %d MT/s 상승 (XMP/EXPO 적용/오버클럭). 안정성 테스트(TestMem5, OCCT 등)를 권장합니다." % (o, n)))

    o, n = _g(old, "memory", "total_gb"), _g(new, "memory", "total_gb")
    if o and n and o != n:
        hl.append(("critical" if n < o else "info", "RAM 용량 %s → %s GB%s" % (o, n, " (모듈 인식 실패? 장착 상태 확인)" if n < o else "")))
    o, n = _g(old, "memory", "module_count"), _g(new, "memory", "module_count")
    if o and n and o != n:
        hl.append(("warn" if n < o else "info", "RAM 모듈 수 %s → %s개" % (o, n)))

    og, ng = old.get("gpus") or [], new.get("gpus") or []
    for i in range(min(len(og), len(ng))):
        od = og[i].get("nvidia_driver") or og[i].get("driver_version")
        nd = ng[i].get("nvidia_driver") or ng[i].get("driver_version")
        if od and nd and od != nd:
            hl.append(("info", "GPU 드라이버 변경: %s %s → %s" % (ng[i].get("name"), od, nd)))
        ow, nw = og[i].get("pcie_width_current"), ng[i].get("pcie_width_current")
        if ow and nw and nw < ow:
            hl.append(("warn", "GPU PCIe 링크 폭 x%d → x%d 감소 (슬롯/라이저/BIOS 설정 확인)" % (ow, nw)))

    o, n = _g(old, "bios", "version"), _g(new, "bios", "version")
    if o and n and o != n:
        hl.append(("info", "BIOS 버전 %s → %s. 업데이트 후엔 XMP/EXPO·팬·전원 설정이 초기화되는 경우가 많습니다." % (o, n)))

    ob = (_g(old, "os", "build"), _g(old, "os", "ubr"))
    nb = (_g(new, "os", "build"), _g(new, "os", "ubr"))
    if all(ob) and all(nb) and ob != nb:
        hl.append(("info", "Windows 빌드 %d.%d → %d.%d (업데이트 설치됨)" % (ob + nb)))

    for key, label in (("vbs_running", "VBS"), ("hvci_running", "메모리 무결성(HVCI)"), ("game_mode", "게임 모드"), ("hags", "HAGS")):
        o, n = _g(old, "features", key), _g(new, "features", key)
        if o is not None and n is not None and o != n:
            hl.append(("info", "%s: %s → %s" % (label, "켜짐" if o else "꺼짐", "켜짐" if n else "꺼짐")))

    o, n = _g(old, "power_plan", "guid"), _g(new, "power_plan", "guid")
    if o and n and o != n:
        hl.append(("info", "전원 옵션 변경: %s → %s" % (_g(old, "power_plan", "name"), _g(new, "power_plan", "name"))))

    od = {d["drive"]: d for d in old.get("disks") or []}
    for d in new.get("disks") or []:
        p = od.get(d["drive"])
        if p and p.get("free_gb") is not None and d.get("free_gb") is not None:
            delta = d["free_gb"] - p["free_gb"]
            if delta <= -20:
                hl.append(("warn", "%s: 여유 공간 %.0f GB 감소 (%.0f → %.0f GB)" % (d["drive"], -delta, p["free_gb"], d["free_gb"])))
    o, n = _g(old, "startup", "count"), _g(new, "startup", "count")
    if o is not None and n is not None and n > o:
        hl.append(("info", "시작 프로그램 %d → %d개 (새로 추가: %s)" % (
            o, n, ", ".join(sorted(set(_g(new, "startup", "names") or []) - set(_g(old, "startup", "names") or []))) or "?")))

    fo, fn = flatten(_keyed(old)), flatten(_keyed(new))
    changes = []
    for k in sorted(set(fo) | set(fn)):
        if k.startswith(VOLATILE_PREFIXES) or ".free_gb" in k or ".used_percent" in k:
            continue
        if fo.get(k) != fn.get(k):
            changes.append((k, fo.get(k), fn.get(k)))
    return {"highlights": hl, "changes": changes}
