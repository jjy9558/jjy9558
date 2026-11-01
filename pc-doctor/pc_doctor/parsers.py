"""명령 출력 파서 (순수 함수, OS 무관 → 테스트 가능)."""

import csv
import io
import re

GUID_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")

# 잘 알려진 기본 전원 구성표 GUID (Windows 공통)
POWER_PLANS = {
    "a1841308-3542-4df3-9d6c-0bbc1e8a1b7c": "saver",
    "381b4222-f2b5-4f9c-a9d4-e6b4e9ca5e9a": "balanced",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "high",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "ultimate",
}
POWER_PLAN_GUIDS = {v: k for k, v in POWER_PLANS.items()}


def as_list(x):
    """ConvertTo-Json은 원소 1개짜리 배열을 객체로 풀어버리므로 항상 리스트로 맞춤."""
    if x is None:
        return []
    if isinstance(x, list):
        return [i for i in x if i is not None]
    return [x]


def to_int(x, default=None):
    try:
        if x is None or x == "":
            return default
        return int(float(x))
    except (TypeError, ValueError):
        return default


def parse_powercfg_active(raw):
    """'Power Scheme GUID: 381b...  (Balanced)' / '전원 구성표 GUID: ... (균형 조정)'."""
    if not raw:
        return None
    m = GUID_RE.search(raw)
    if not m:
        return None
    guid = m.group(1).lower()
    name_m = re.search(r"\(([^()]*)\)\s*$", raw.strip())
    return {
        "guid": guid,
        "name": name_m.group(1).strip() if name_m else "",
        "kind": POWER_PLANS.get(guid, "custom"),
    }


def parse_nvidia_smi(raw):
    """--format=csv,noheader,nounits 출력 → GPU 리스트."""
    gpus = []
    if not raw:
        return gpus
    for line in raw.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        parts += [""] * (8 - len(parts))

        def num(s):
            return to_int(s) if s and "N/A" not in s and "Not Supported" not in s else None

        gpus.append(
            {
                "name": parts[0],
                "driver_version": parts[1],
                "vram_mb": num(parts[2]),
                "vram_used_mb": num(parts[3]),
                "pcie_gen_current": num(parts[4]),
                "pcie_gen_max": num(parts[5]),
                "pcie_width_current": num(parts[6]),
                "pcie_width_max": num(parts[7]),
            }
        )
    return gpus


def parse_tasklist(raw):
    """tasklist /FO CSV /NH → 소문자 이미지 이름 집합."""
    names = set()
    if not raw:
        return names
    for row in csv.reader(io.StringIO(raw)):
        if row and row[0].lower().endswith(".exe"):
            names.add(row[0].strip().lower())
    return names


def parse_sc_state(raw):
    """sc query 출력에서 상태 토큰(RUNNING/STOPPED/...)만 추출. 언어 무관."""
    if not raw:
        return None
    m = re.search(r":\s*\d+\s+(RUNNING|STOPPED|START_PENDING|STOP_PENDING|PAUSED|CONTINUE_PENDING|PAUSE_PENDING)", raw)
    return m.group(1) if m else None


STANDARD_RAM_SPEEDS = {
    2133, 2400, 2666, 2933, 3000, 3200, 3466, 3600, 3733, 3800, 4000, 4266, 4400, 4800,
    5200, 5600, 6000, 6200, 6400, 6600, 6800, 7000, 7200, 7600, 8000, 8200, 8400, 8800,
}


def guess_rated_speed_from_part(part):
    """메모리 부품번호에서 정격(XMP/EXPO) 속도 추정. 예: CMK32GX5M2B6000C36 → 6000.

    표준 속도 목록에 있는 4자리 숫자만 인정합니다 (CL 타이밍 숫자 오인 방지). 추정값입니다.
    """
    if not part:
        return None
    found = [int(n) for n in re.findall(r"(?<!\d)(\d{4})(?!\d)", part) if int(n) in STANDARD_RAM_SPEEDS]
    return max(found) if found else None


def guess_channel(bank_label, device_locator):
    """BankLabel/DeviceLocator에서 채널 문자(A, B, ...) 추정. 모르면 None."""
    s = ("%s %s" % (bank_label or "", device_locator or "")).upper()
    m = re.search(r"CHANNEL\s*([A-H])\b", s) or re.search(r"CHANNEL([A-H])", s)
    if m:
        return m.group(1)
    m = re.search(r"DIMM[_\s-]?([A-H])\d", s)
    if m:
        return m.group(1)
    return None
