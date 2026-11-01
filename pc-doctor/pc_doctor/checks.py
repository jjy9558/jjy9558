"""규칙 기반 진단. 규칙 하나 = 작은 함수 하나.

각 규칙은 (스냅샷, 설정)을 받아 Finding 하나(또는 None/리스트)를 돌려줍니다.
새 규칙은 `@rule("id")`만 붙이면 자동 등록됩니다.
"""

import datetime as _dt
from dataclasses import asdict, dataclass

SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}
SEVERITY_LABEL = {"critical": "🔴 심각", "warn": "🟡 주의", "info": "🔵 참고"}


@dataclass
class Finding:
    id: str
    severity: str
    title: str
    detail: str
    fix: str

    def to_json(self):
        return asdict(self)


RULES = []


def rule(rule_id):
    def deco(fn):
        fn.rule_id = rule_id
        RULES.append(fn)
        return fn

    return deco


def _date(s):
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def rated_ram_speed(snap, cfg):
    """(정격 속도, 근거) — 설정값 > 부품번호 추정 > SPD 최대 속도 순."""
    mem = snap.get("memory") or {}
    if cfg.get("expected_ram_mts"):
        return int(cfg["expected_ram_mts"]), "설정값(expected_ram_mts)"
    if mem.get("part_rated_speed"):
        return mem["part_rated_speed"], "부품번호 추정"
    if mem.get("spd_speed"):
        return mem["spd_speed"], "SPD 보고 속도"
    return None, None


# ---------------------------------------------------------------------------
# 메모리
# ---------------------------------------------------------------------------


@rule("ram_below_rated")
def ram_below_rated(snap, cfg):
    cur = (snap.get("memory") or {}).get("configured_speed")
    rated, src = rated_ram_speed(snap, cfg)
    if not cur or not rated or cur >= rated * 0.97:
        return None
    return Finding(
        "ram_below_rated", "warn",
        "RAM이 정격보다 느리게 동작 중 (%d / %d MT/s)" % (cur, rated),
        "현재 %d MT/s, 정격 %d MT/s(%s). XMP/EXPO가 꺼져 있으면 메모리 대역폭이 줄어 게임 프레임과 "
        "CPU 오프로딩된 LLM 토큰 속도가 함께 떨어집니다. BIOS 초기화·업데이트 직후 흔합니다." % (cur, rated, src),
        "BIOS에서 XMP(인텔) / EXPO(AMD) 프로필을 켜세요. 4개 장착 시엔 정격보다 낮춰야 안정적일 수 있습니다. "
        "적용 후 메모리 안정성 테스트를 권장합니다.",
    )


@rule("ram_single_channel")
def ram_single_channel(snap, cfg):
    mem = snap.get("memory") or {}
    n = mem.get("module_count") or 0
    if n == 1:
        return Finding(
            "ram_single_channel", "warn", "RAM 1개만 장착 (단일 채널)",
            "메모리 1개는 단일 채널이라 대역폭이 듀얼 채널의 절반 수준입니다.",
            "같은 모델로 1개를 추가하거나 2개 키트로 교체하세요.",
        )
    ch = mem.get("channels")
    if n >= 2 and ch is not None and len(ch) == 1:
        return Finding(
            "ram_single_channel", "warn", "RAM %d개가 모두 같은 채널(%s)에 장착된 것으로 보임" % (n, ch[0]),
            "슬롯 위치가 잘못되면 2개를 꽂아도 단일 채널로 동작합니다 (BankLabel/DeviceLocator 기반 추정).",
            "메인보드 설명서의 권장 슬롯(보통 A2/B2)에 다시 장착하세요.",
        )
    return None


@rule("ram_mixed_kits")
def ram_mixed_kits(snap, cfg):
    mods = (snap.get("memory") or {}).get("modules") or []
    parts = set(m.get("part") for m in mods if m.get("part"))
    caps = set(m.get("capacity_gb") for m in mods)
    if len(mods) >= 2 and (len(parts) > 1 or len(caps) > 1):
        return Finding(
            "ram_mixed_kits", "info", "서로 다른 RAM이 섞여 있음",
            "부품번호: %s / 용량: %s GB. 서로 다른 키트는 XMP/EXPO 속도에서 불안정할 수 있습니다." % (
                ", ".join(sorted(parts)) or "?", ", ".join(str(c) for c in sorted(c for c in caps if c is not None))),
            "불안정(블루스크린, 앱 튕김)하면 속도·타이밍을 낮추거나 같은 키트로 맞추세요.",
        )
    return None


@rule("low_free_ram")
def low_free_ram(snap, cfg):
    os_ = snap.get("os") or {}
    free, total = os_.get("free_mem_gb"), os_.get("visible_mem_gb")
    if free is None or not total or free / total >= 0.10:
        return None
    top = (snap.get("processes") or {}).get("top_memory") or []
    tops = ", ".join("%s(%s MB)" % (p["name"], p["mb"]) for p in top[:3])
    return Finding(
        "low_free_ram", "info", "여유 RAM 부족 (%.1f / %.1f GB)" % (free, total),
        "스냅샷 시점 여유 메모리가 10%% 미만입니다. 메모리 상위: %s" % (tops or "?"),
        "큰 모델을 띄우기 전에 상위 프로그램을 정리하거나 모델 양자화를 낮추세요.",
    )


# ---------------------------------------------------------------------------
# 디스크
# ---------------------------------------------------------------------------


@rule("disk_almost_full")
def disk_almost_full(snap, cfg):
    out = []
    for d in snap.get("disks") or []:
        used = d.get("used_percent")
        if used is None:
            continue
        if used >= cfg.get("disk_critical_percent", 95):
            sev = "critical"
        elif used >= cfg.get("disk_warn_percent", 90):
            sev = "warn"
        else:
            continue
        out.append(Finding(
            "disk_almost_full", sev, "%s: 드라이브 %.0f%% 사용 (여유 %.0f GB)" % (d["drive"], used, d["free_gb"]),
            "여유 공간이 부족하면 업데이트 실패, 페이지파일 확장 실패, SSD 성능 저하가 생길 수 있습니다.",
            "디스크 정리(저장소 센스), 안 쓰는 게임/모델 이동, 큰 파일 다른 드라이브로 옮기기.",
        ))
    return out


@rule("system_disk_hdd")
def system_disk_hdd(snap, cfg):
    for d in snap.get("disks") or []:
        if d.get("drive") == "C" and str(d.get("media_type")).upper() == "HDD":
            return Finding(
                "system_disk_hdd", "warn", "Windows가 HDD에 설치되어 있음",
                "부팅·로딩·업데이트가 크게 느려집니다.",
                "NVMe SSD로 시스템 드라이브를 옮기세요.",
            )
    return None


# ---------------------------------------------------------------------------
# Windows 설정
# ---------------------------------------------------------------------------


@rule("vbs_on")
def vbs_on(snap, cfg):
    f = snap.get("features") or {}
    if not f.get("hvci_running") and not f.get("vbs_running"):
        return None
    what = "메모리 무결성(HVCI)" if f.get("hvci_running") else "VBS"
    return Finding(
        "vbs_on", "info", "%s 켜짐 — 게임 성능이 다소 떨어질 수 있음" % what,
        "가상화 기반 보안(VBS)/메모리 무결성은 커널을 보호하는 보안 기능입니다. 일부 게임에서 성능 저하가 보고됩니다. "
        "pc-doctor는 보안 기능이라 이 설정을 절대 바꾸지 않습니다.",
        "보안과 성능의 트레이드오프이므로 직접 판단하세요: Windows 보안 → 장치 보안 → 코어 격리 세부 정보.",
    )


@rule("power_plan_saver")
def power_plan_saver(snap, cfg):
    p = snap.get("power_plan") or {}
    if p.get("kind") != "saver":
        return None
    return Finding(
        "power_plan_saver", "warn", "전원 옵션이 '절전'으로 설정됨",
        "CPU 클럭이 제한되어 게임·컴파일·LLM 추론이 느려집니다.",
        "설정 → 시스템 → 전원에서 '균형' 이상으로, 또는 `pc-doctor gamemode on` 사용.",
    )


@rule("gpu_driver_old")
def gpu_driver_old(snap, cfg):
    ref = _date(snap.get("taken_at")) or _dt.date.today()
    limit = cfg.get("gpu_driver_max_age_days", 180)
    out = []
    for g in snap.get("gpus") or []:
        d = _date(g.get("driver_date"))
        if not d or "microsoft basic" in (g.get("name") or "").lower():
            continue
        age = (ref - d).days
        if age > limit:
            out.append(Finding(
                "gpu_driver_old", "warn" if age > limit * 2 else "info",
                "GPU 드라이버가 오래됨 (%s, %d일 전)" % (g.get("nvidia_driver") or g.get("driver_version"), age),
                "%s 드라이버 날짜 %s. 신작 게임 최적화·버그 수정이 빠져 있을 수 있습니다." % (g.get("name"), d),
                "제조사 앱/사이트에서 최신 드라이버 설치. 최신 버전에 문제가 알려져 있으면 한 단계 이전 안정 버전도 괜찮습니다.",
            ))
    return out


@rule("gpu_pcie_width")
def gpu_pcie_width(snap, cfg):
    for g in snap.get("gpus") or []:
        cur, mx = g.get("pcie_width_current"), g.get("pcie_width_max")
        if cur and mx and cur < mx:
            return Finding(
                "gpu_pcie_width", "info", "GPU PCIe 링크 폭 x%d (최대 x%d)" % (cur, mx),
                "GPU가 최대 폭보다 좁게 연결되어 있습니다. M.2 슬롯과 레인 공유, 라이저 케이블, 덜 꽂힘이 원인일 수 있습니다.",
                "메인보드 설명서에서 레인 분배 확인, GPU 재장착, BIOS의 PCIe 설정 확인.",
            )
    return None


@rule("startup_too_many")
def startup_too_many(snap, cfg):
    n = (snap.get("startup") or {}).get("count") or 0
    lim = cfg.get("startup_apps_warn", 15)
    if n <= lim:
        return None
    return Finding(
        "startup_too_many", "info", "시작 프로그램이 많음 (%d개)" % n,
        "부팅 후 백그라운드에서 RAM·CPU를 씁니다 (WMI 기준 추정, 작업 관리자에서 꺼 둔 항목도 포함될 수 있음).",
        "작업 관리자 → 시작 앱 탭에서 필요 없는 항목을 '사용 안 함'으로.",
    )


@rule("pagefile_off")
def pagefile_off(snap, cfg):
    pf = snap.get("pagefile") or {}
    # 자동 관리가 명시적으로 꺼져 있고(False) 페이지 파일도 하나도 없을 때만 (정보 누락과 구분)
    if pf.get("auto_managed") is not False or pf.get("files"):
        return None
    largest = (snap.get("models") or {}).get("largest_gb")
    ram = (snap.get("memory") or {}).get("total_gb") or 0
    big = largest is not None and ram and largest >= ram * 0.5
    return Finding(
        "pagefile_off", "warn" if big else "info", "페이지 파일이 꺼져 있음",
        ("가장 큰 모델 %.1f GB가 RAM %s GB의 절반 이상입니다. 페이지 파일이 없으면 커밋 한도 초과로 모델 로딩이나 "
         "게임이 갑자기 종료될 수 있습니다." % (largest, ram)) if big else
        "페이지 파일이 없으면 메모리 커밋 한도가 RAM 크기로 묶여 일부 프로그램이 메모리 부족으로 튕길 수 있습니다.",
        "시스템 속성 → 고급 → 성능 설정 → 고급 → 가상 메모리에서 'SSD에 시스템 관리 크기'를 권장합니다.",
    )


@rule("game_mode_off")
def game_mode_off(snap, cfg):
    if (snap.get("features") or {}).get("game_mode") is False:
        return Finding(
            "game_mode_off", "info", "Windows 게임 모드 꺼짐",
            "게임 모드는 게임 중 백그라운드 업데이트/알림을 줄여 줍니다.",
            "설정 → 게임 → 게임 모드 켜기.",
        )
    return None


@rule("hags_off")
def hags_off(snap, cfg):
    if (snap.get("features") or {}).get("hags") is not False:
        return None
    rtx_fg = any(k in (g.get("name") or "").upper() for g in snap.get("gpus") or [] for k in ("RTX 40", "RTX 50"))
    return Finding(
        "hags_off", "warn" if rtx_fg else "info", "하드웨어 가속 GPU 예약(HAGS) 꺼짐",
        "RTX 40/50 시리즈의 DLSS 프레임 생성은 HAGS가 켜져 있어야 동작합니다." if rtx_fg else
        "일부 GPU 기능(프레임 생성 등)은 HAGS가 필요합니다.",
        "설정 → 시스템 → 디스플레이 → 그래픽 → 기본 그래픽 설정 변경 → HAGS 켜기 (재부팅 필요).",
    )


def run_checks(snap, cfg, rules=None):
    findings = []
    for fn in rules or RULES:
        try:
            res = fn(snap, cfg)
        except Exception as e:  # 규칙 하나가 깨져도 나머지는 계속
            res = Finding(fn.rule_id, "info", "규칙 실행 오류: %s" % fn.rule_id, repr(e), "버그 제보 부탁드립니다.")
        if res is None:
            continue
        findings.extend(res if isinstance(res, list) else [res])
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    return findings


def format_findings(findings):
    if not findings:
        return ["✅ 발견된 문제가 없습니다."]
    lines = []
    for f in findings:
        lines.append("%s [%s] %s" % (SEVERITY_LABEL.get(f.severity, f.severity), f.id, f.title))
        lines.append("    설명: %s" % f.detail)
        lines.append("    해결: %s" % f.fix)
    return lines
