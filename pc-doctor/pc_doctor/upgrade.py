"""병목 분석 + 업그레이드 조언 (실시간 가격 없음, 대략적인 규칙 기반)."""

from dataclasses import dataclass

from .checks import rated_ram_speed

# llama.cpp Q4_K_M 기준 대략적인 GGUF 크기 (GB). 실제는 모델마다 다릅니다.
TYPICAL_Q4_SIZES = [("7~8B", 4.9), ("12~14B", 9.0), ("24B", 14.0), ("30~32B", 19.5), ("70B", 42.5)]
KV_OVERHEAD_GB = 1.5  # 컨텍스트(KV 캐시)·CUDA 버퍼 여유분 대략치
RAM_STEPS = [32, 48, 64, 96, 128, 192, 256]


@dataclass
class Advice:
    area: str
    title: str
    detail: str


def memory_bandwidth_gbs(snap):
    """이론 메모리 대역폭(GB/s) ≈ MT/s × 8바이트 × 채널 수. 소비자 플랫폼은 최대 2채널로 가정."""
    mem = snap.get("memory") or {}
    speed = mem.get("configured_speed")
    if not speed:
        return None
    n = mem.get("module_count") or 1
    ch = mem.get("channels")
    channels = 1 if n == 1 or (ch is not None and len(ch) == 1) else 2
    return round(speed * 8 * channels / 1000.0, 1)


def _vram(snap):
    vals = [g.get("vram_gb") or 0 for g in snap.get("gpus") or [] if g.get("vram_source") == "nvidia-smi" or (g.get("vram_gb") or 0) > 0]
    return max(vals) if vals else 0


def advise(snap, cfg):
    out = []
    mem = snap.get("memory") or {}
    ram = mem.get("total_gb") or 0
    vram = _vram(snap)
    bw = memory_bandwidth_gbs(snap)
    largest = (snap.get("models") or {}).get("largest_gb")
    cpu = snap.get("cpu") or {}

    # 0) 돈 안 드는 것부터
    cur = mem.get("configured_speed")
    rated, _src = rated_ram_speed(snap, cfg)
    free_fixes = []
    if cur and rated and cur < rated * 0.97:
        free_fixes.append("XMP/EXPO 켜기 (%d → %d MT/s, 대역폭 약 %d%% 증가)" % (cur, rated, round((rated / cur - 1) * 100)))
    if mem.get("module_count") == 1:
        free_fixes.append("RAM 1개 → 듀얼 채널 구성 (대역폭 약 2배)")
    if free_fixes:
        out.append(Advice("무료", "업그레이드 전에 먼저 할 일", " / ".join(free_fixes)))

    # 1) 로컬 LLM
    if largest:
        usable = max(vram - KV_OVERHEAD_GB, 0)
        if largest <= usable:
            out.append(Advice(
                "LLM", "가장 큰 모델(%.1f GB)이 VRAM(%.0f GB)에 다 올라감" % (largest, vram),
                "주 병목은 GPU 연산/VRAM 대역폭입니다. RAM 증설은 토큰 속도에 거의 영향이 없습니다. "
                "더 큰 모델을 쓰고 싶다면 VRAM이 더 큰 GPU가 핵심입니다."))
        else:
            off = round(largest - usable, 1)
            est = ("CPU 쪽 처리 한계 추정 ≈ %.1f tok/s 이하 (대역폭 %.0f GB/s ÷ 오프로드 %.1f GB)" % (bw / off, bw, off)) if bw and off else ""
            out.append(Advice(
                "LLM", "모델 %.1f GB > 쓸 수 있는 VRAM 약 %.1f GB → %.1f GB가 RAM으로 오프로드" % (largest, usable, off),
                "오프로드된 부분은 시스템 RAM 대역폭에 묶입니다(RAM-bound). %s\n"
                "      선택지: ① 양자화 낮추기(Q4_K_M 등) ② MoE 모델이면 llama.cpp에서 전문가 텐서만 CPU로 "
                "(--override-tensor / --n-cpu-moe) ③ RAM 속도·채널 최적화 ④ VRAM 더 큰 GPU." % est))
        need = largest + 8  # OS·브라우저 여유분
        if need > ram + vram * 0.9:
            target = next((s for s in RAM_STEPS if s >= need), RAM_STEPS[-1])
            out.append(Advice(
                "RAM", "RAM 용량 부족 가능성 (모델 %.1f GB + 여유 8 GB > RAM %s GB + VRAM)" % (largest, ram),
                "부족하면 mmap 페이징으로 디스크에서 읽어 극단적으로 느려집니다. 목표 %d GB 이상 권장. "
                "DDR5 4개 장착은 속도가 크게 떨어지는 경우가 많아 고용량 2개(예: 2×48GB, 2×64GB) 구성이 유리합니다." % target))
    else:
        fits = [label for label, size in TYPICAL_Q4_SIZES if size + KV_OVERHEAD_GB <= vram]
        out.append(Advice(
            "LLM", "VRAM %.0f GB 기준 GPU에 다 올라가는 모델 (Q4 대략)" % vram,
            ("%s 까지. " % fits[-1] if fits else "7B급도 빠듯. ") +
            "config.json의 model_dirs에 GGUF 폴더를 넣으면 실제 모델 크기로 분석합니다."))

    if bw:
        out.append(Advice("RAM", "현재 이론 메모리 대역폭 약 %.0f GB/s" % bw,
                          "RAM으로 오프로드되는 LLM 추론 속도는 대략 이 값 ÷ 오프로드 크기(GB) tok/s가 상한입니다."))

    # 2) 저장장치
    for d in snap.get("disks") or []:
        if d.get("drive") == "C" and str(d.get("media_type")).upper() == "HDD":
            out.append(Advice("저장장치", "시스템 드라이브 SSD 교체가 체감 1순위", "부팅·로딩·업데이트 모두 빨라집니다."))
        if (d.get("used_percent") or 0) >= 90:
            out.append(Advice("저장장치", "%s 드라이브 공간 부족" % d["drive"], "NVMe SSD 추가로 게임/모델 저장소를 분리하세요."))

    # 3) 게임
    if cpu.get("cores") and cpu["cores"] < 6:
        out.append(Advice("CPU", "CPU 코어 %d개 — 최신 게임에서 CPU 병목 가능" % cpu["cores"], "6코어 이상 권장."))
    if vram and vram < 8:
        out.append(Advice("GPU", "VRAM %.0f GB — 최신 게임 고해상도 텍스처에 부족" % vram, "12GB 이상 GPU 권장."))

    if not out:
        out.append(Advice("요약", "뚜렷한 병목 없음", "현재 구성이 균형 잡혀 있습니다."))
    return out


def format_advice(items):
    lines = ["※ 실시간 가격 정보 없이 규칙으로 추정한 조언입니다."]
    for a in items:
        lines.append("[%s] %s" % (a.area, a.title))
        lines.append("      %s" % a.detail)
    return lines
