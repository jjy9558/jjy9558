"""구매 후 업그레이드 추천: "이 매물 사서 이것만 바꾸면 이 예산에 이 성능".

간단한 성능 등급표(perf.py)와 같은 가격표(prices)를 쓴다. 병목/파워/PCIe/램 규격 메모 포함.
"""

import itertools
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import catalog, perf
from .judge import Judgment, judge
from .models import Parsed
from .prices import PriceTable
from .textutil import fmt_man, fmt_won, ro


@dataclass
class Swap:
    category: str          # gpu / cpu / ram / psu
    from_name: str
    to_name: str
    new_price: int
    resale: int
    delta: int
    note: str = ""


@dataclass
class Option:
    label: str
    swaps: List[Swap]
    config: Dict
    perf: float
    meets: bool
    delta_total: int
    outlay: int
    new_equiv: Optional[int]
    saving_pct: Optional[float]
    notes: List[str] = field(default_factory=list)
    headline: str = ""
    within_budget: bool = True


@dataclass
class UpgradePlan:
    use: Dict
    budget: Optional[int]
    price: Optional[int]
    price_is_fair_mid: bool = False
    current_perf: Optional[float] = None
    current_meets: bool = False
    current_notes: List[str] = field(default_factory=list)
    options: List[Option] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    target_text: str = ""


# ---------------------------------------------------------------------------
# 구성(config) 만들기 / 평가
# ---------------------------------------------------------------------------
def config_from(parsed: Parsed) -> Dict:
    return {
        "cpu": parsed.val("cpu"),
        "gpu": parsed.val("gpu"),
        "ram_gb": parsed.val("ram_gb"),
        "ram_type": parsed.val("ram_type"),
        "ram_sticks": parsed.val("ram_sticks"),
        "psu_watt": parsed.val("psu_watt"),
        "psu_grade": parsed.val("psu_grade"),
        "psu_tier": parsed.fields["psu_brand"].meta.get("tier") if parsed.has("psu_brand") else None,
        "psu_unknown": parsed.has_flag("psu_unknown"),
        "board": parsed.val("board"),
        "storage": parsed.val("storage") or [],
    }


def evaluate(cfg: Dict, use: Dict) -> Dict:
    """-> {perf, meets, target, why}"""
    cpu, gpu = cfg.get("cpu"), cfg.get("gpu")
    ram = cfg.get("ram_gb") or 0
    kind = use["kind"]
    why = []
    if kind == "gaming":
        res = use["res"]
        ge = perf.gaming_effective(cpu, gpu, res)
        if ge is None:
            return {"perf": 0.0, "meets": False, "target": perf.RES_TARGET_GPU[res], "why": ["CPU/GPU 정보 부족"]}
        p = ge["eff"]
        if ram and ram < 16:
            p *= 0.9
            why.append("RAM 16GB 미만")
        target = perf.RES_TARGET_GPU[res]
        meets = p >= target * 0.97 and (not ram or ram >= 16)
        if ge["vram_short"]:
            why.append("VRAM {}GB 미만".format(perf.RES_VRAM[res]))
        if ge["bottleneck"] == "cpu":
            why.append("CPU 병목")
        return {"perf": round(p, 1), "meets": meets, "target": target, "why": why}
    if kind in ("llm", "diffusion"):
        if not gpu or gpu not in catalog.GPUS:
            return {"perf": 0.0, "meets": False, "target": 0, "why": ["GPU 정보 없음"]}
        vram = catalog.GPUS[gpu]["vram"]
        need = use["vram_need"]
        sc = catalog.GPUS[gpu]["score"]
        fits = vram >= need
        if fits:
            p = float(sc)
            if kind == "diffusion" and sc < 60:
                fits = False
                why.append("GPU 성능 부족")
        else:
            p = 10.0 + 30.0 * min(1.0, vram / need)
            why.append("VRAM {}GB < 필요 약 {:g}GB (CPU/RAM 오프로드 시 매우 느려짐)".format(vram, need))
        if kind == "llm" and ram and ram < 32:
            why.append("RAM 32GB 미만 (모델 로딩/오프로드 여유 부족)")
            p *= 0.95
        return {"perf": round(p, 1), "meets": fits, "target": need, "why": why}
    if kind == "editing":
        mt = perf.cpu_mt(cpu)
        gs = perf.gpu_score(gpu) if gpu else 20
        if mt is None:
            return {"perf": 0.0, "meets": False, "target": 100, "why": ["CPU 정보 없음"]}
        gs = gs or 20
        ram_bonus = 15 if ram >= 32 else (0 if ram >= 16 else -10)
        p = 0.55 * min(mt, 200) + 0.30 * min(gs, 150) + ram_bonus
        meets = mt >= 85 and gs >= 55 and ram >= 32
        if mt < 85:
            why.append("CPU 멀티스레드 부족")
        if gs < 55:
            why.append("GPU 가속 성능 부족")
        if ram < 32:
            why.append("RAM 32GB 미만")
        return {"perf": round(p, 1), "meets": meets, "target": 100, "why": why}
    return {"perf": 0.0, "meets": False, "target": 0, "why": []}


# ---------------------------------------------------------------------------
# 파워 / PCIe / 메모리 / 병목 메모
# ---------------------------------------------------------------------------
def recommended_psu(cfg: Dict) -> Optional[int]:
    gpu, cpu = cfg.get("gpu"), cfg.get("cpu")
    if gpu not in catalog.GPUS:
        return None
    g = catalog.GPUS[gpu]
    peak = catalog.CPUS[cpu]["peak_w"] if cpu in catalog.CPUS else 120
    calc = (g["tdp"] + peak + 100) * 1.25
    need = max(g["psu_rec"], calc)
    return int(math.ceil(need / 50.0) * 50)


def pcie_effective(cfg: Dict) -> Optional[float]:
    gens = []
    if cfg.get("cpu") in catalog.CPUS:
        gens.append(catalog.CPUS[cfg["cpu"]]["pcie"])
    if cfg.get("board") and catalog.chipset_info(cfg["board"]):
        gens.append(catalog.chipset_info(cfg["board"])[1])
    return min(gens) if gens else None


def bottleneck_notes(cfg: Dict, use: Dict, table: Optional[PriceTable] = None) -> List[str]:
    notes: List[str] = []
    cpu, gpu = cfg.get("cpu"), cfg.get("gpu")
    kind = use["kind"]

    # CPU-GPU 균형
    if kind == "gaming":
        ge = perf.gaming_effective(cpu, gpu, use["res"])
        if ge:
            used_pct = min(100.0, ge["cpu_cap"] / ge["gpu"] * 100)
            if ge["bottleneck"] == "cpu":
                notes.append("CPU-GPU 균형: {} 가 {} 를 {}에서 다 못 먹입니다 (GPU 성능의 약 {:.0f}% 활용). "
                             "CPU도 같이 올리거나 해상도/옵션을 높게 쓰세요.".format(cpu, gpu, perf.RES_LABEL[use["res"]], used_pct))
            elif ge["bottleneck"] == "gpu":
                notes.append("CPU-GPU 균형: GPU 쪽이 상대적으로 약합니다. GPU 교체 효과가 큽니다.")
            else:
                notes.append("CPU-GPU 균형: {} + {} 은(는) {}에서 비교적 균형이 맞습니다.".format(cpu, gpu, perf.RES_LABEL[use["res"]]))
    elif kind == "llm" and gpu in catalog.GPUS:
        vram = catalog.GPUS[gpu]["vram"]
        if vram >= use["vram_need"]:
            notes.append("LLM: VRAM {}GB >= 필요 약 {:g}GB 이므로 전량 GPU 로드 가능. 속도는 GPU 메모리 대역폭이 좌우하고 CPU 영향은 작습니다.".format(vram, use["vram_need"]))
            if vram - use["vram_need"] < 2:
                notes.append("LLM: 여유 VRAM이 {:.1f}GB뿐이라 컨텍스트를 길게 잡으면 부족할 수 있습니다 (KV 캐시).".format(vram - use["vram_need"]))
            if catalog.GPUS[gpu]["vendor"] == "amd":
                notes.append("LLM: AMD GPU는 llama.cpp(Vulkan/ROCm)는 되지만 CUDA 전용 도구는 못 씁니다. 쓸 런타임 지원 여부를 먼저 확인하세요.")
        else:
            notes.append("LLM: VRAM {}GB < 필요 약 {:g}GB. 부족분은 시스템 RAM으로 오프로드되어 속도가 크게 떨어집니다 "
                         "(DDR5 + 64GB 이상이 그나마 유리).".format(vram, use["vram_need"]))
        if use["vram_need"] > 32:
            notes.append("LLM: 단일 소비자용 GPU(최대 32GB)로는 부족합니다. 24GB 2장(3090 x2)이나 양자화를 더 낮춘 모델을 고려하세요.")
    elif kind == "editing":
        mt = perf.cpu_mt(cpu)
        if mt is not None:
            notes.append("영상편집: 인코딩/렌더링은 CPU 멀티스레드(현재 지수 {})와 GPU 인코더가 모두 영향을 줍니다. 캐시/프록시는 NVMe 권장.".format(mt))

    # 파워
    need = recommended_psu(cfg)
    watt = cfg.get("psu_watt")
    if need:
        if watt:
            if watt < need * 0.95:
                notes.append("파워: 현재 {}W < 권장 약 {}W -> 부족합니다 (교체 필요).".format(watt, need))
            else:
                notes.append("파워: {}W 로 권장 약 {}W 충족 (정격/품질 확인은 별개).".format(watt, need))
        else:
            notes.append("파워: 용량을 모릅니다. 권장 약 {}W 이상인지 확인 필요.".format(need))
        if cfg.get("psu_unknown") or (cfg.get("psu_tier") is None and watt):
            if gpu in catalog.GPUS and catalog.GPUS[gpu]["tdp"] >= 200:
                notes.append("파워: 무명/미확인 브랜드 파워에 고전력 GPU는 위험합니다. 신뢰할 수 있는 브랜드로 교체를 권장합니다.")
        if gpu in ("RTX 4090", "RTX 5090", "RTX 5080", "RTX 5070 Ti", "RTX 4080", "RTX 4080 Super", "RTX 4070 Ti Super", "RTX 3090 Ti"):
            notes.append("파워: 16핀(12VHPWR/12V-2x6) 또는 8핀 3~4개 어댑터가 필요할 수 있습니다. 케이블/커넥터 확인.")

    # PCIe
    eff = pcie_effective(cfg)
    if gpu in catalog.GPUS and eff:
        lanes = catalog.GPUS[gpu]["lanes"]
        if lanes <= 8 and eff < 4.0:
            notes.append("PCIe: {} 는 x{} 카드인데 플랫폼이 PCIe {:g}라 대역폭 손실(특히 VRAM 부족 게임)이 큽니다.".format(gpu, lanes, eff))
        elif eff < 4.0 and catalog.GPUS[gpu]["score"] >= 130:
            notes.append("PCIe: 플랫폼 PCIe {:g} - 고성능 GPU는 1~3% 정도 손실 가능 (큰 문제는 아님).".format(eff))

    # 메모리 규격 / 플랫폼
    sock = None
    if cpu in catalog.CPUS:
        sock = catalog.CPUS[cpu]["socket"]
    elif cfg.get("board") and catalog.chipset_info(cfg["board"]):
        sock = catalog.chipset_info(cfg["board"])[0]
    ddr = cfg.get("ram_type") or (catalog.SOCKET_MEM.get(sock) if sock in ("AM4", "AM5", "LGA1200", "LGA1151", "LGA1851") else None)
    if ddr == "DDR4":
        if sock == "AM4":
            notes.append("플랫폼: AM4/DDR4 는 5700X3D·5800X3D 가 마지막 업그레이드 경로입니다. 그 이후엔 보드+램까지 교체해야 합니다.")
        elif sock == "LGA1700":
            notes.append("플랫폼: DDR4 보드는 DDR5로 못 바꿉니다. 나중에 DDR5로 가려면 보드+램 교체가 필요합니다.")
        else:
            notes.append("플랫폼: DDR4 구형 플랫폼입니다. CPU를 크게 올리려면 보드+램 동반 교체가 필요할 수 있습니다.")
    elif ddr == "DDR5":
        notes.append("플랫폼: DDR5 플랫폼 (램 가격이 DDR4보다 비쌈, 대신 CPU 업그레이드 여지 큼).")
    elif ddr is None and sock == "LGA1700":
        notes.append("플랫폼: LGA1700 보드는 DDR4/DDR5 중 하나만 지원합니다. 램 규격을 확인하세요.")
    if cfg.get("ram_sticks") == 1:
        notes.append("램: 1개만 꽂혀 있으면 싱글 채널이라 성능 손해가 있습니다 (2개 구성 권장).")
    ram = cfg.get("ram_gb")
    if ram:
        if kind == "gaming" and ram < 16:
            notes.append("램: {}GB 는 게임에 부족합니다 (16GB 이상 권장, 32GB면 여유).".format(ram))
        elif kind in ("editing", "llm") and ram < 32:
            notes.append("램: {}GB 는 {}에 부족합니다 (32GB 이상 권장).".format(ram, "영상편집" if kind == "editing" else "로컬 LLM"))

    # 저장장치
    if kind == "editing":
        st = cfg.get("storage") or []
        if st and not any(i["kind"] == "nvme" for i in st):
            notes.append("저장장치: NVMe 가 없습니다. 영상편집 캐시/프로젝트용 NVMe 1TB 추가를 권장합니다.")

    # 업그레이드 경로 제약
    if cpu in catalog.CPUS:
        b = cfg.get("board")
        if b in ("B450", "X470", "A320", "B350", "X370") and catalog.CPUS[cpu]["socket"] == "AM4":
            notes.append("호환: {} 보드는 라이젠 5000번대 사용 시 BIOS 업데이트가 필요할 수 있습니다.".format(b))
        if b in ("H610", "B660", "H670") and catalog.CPUS[cpu]["socket"] == "LGA1700":
            notes.append("호환: {} 보드는 13/14세대 CPU에 BIOS 업데이트가 필요할 수 있고, 메모리 오버클럭/전원부 한계가 있습니다.".format(b))
    if gpu in catalog.GPUS and catalog.GPUS[gpu]["tdp"] >= 250:
        notes.append("물리: 대형 GPU는 케이스 길이/두께와 전원 커넥터를 확인하세요.")
    return notes


# ---------------------------------------------------------------------------
# 후보 생성
# ---------------------------------------------------------------------------
def _resale(table: PriceTable, price: Optional[int]) -> int:
    return int((price or 0) * table.adj("upgrade_resale_factor"))


def _gpu_swaps(cfg: Dict, table: PriceTable, use: Dict) -> List[Swap]:
    out = []
    cur = cfg.get("gpu")
    cur_price = table.gpu(cur)[0] if cur else 0
    cur_score = catalog.GPUS[cur]["score"] if cur in catalog.GPUS else 0
    cur_vram = catalog.GPUS[cur]["vram"] if cur in catalog.GPUS else 0
    for name, d in catalog.GPUS.items():
        if name == cur:
            continue
        pr, _ = table.gpu(name)
        if pr is None:
            continue
        better = d["score"] > cur_score * 1.08
        if use["kind"] in ("llm", "diffusion"):
            better = d["vram"] > cur_vram or (d["score"] > cur_score * 1.15 and d["vram"] >= cur_vram)
        if not better:
            continue
        out.append(Swap("gpu", cur or "(없음/내장)", name, pr, _resale(table, cur_price), pr - _resale(table, cur_price)))
    return out


def _cpu_swaps(cfg: Dict, table: PriceTable, use: Dict) -> List[Swap]:
    cur = cfg.get("cpu")
    if cur not in catalog.CPUS or use["kind"] not in ("gaming", "editing"):
        return []
    sock = catalog.CPUS[cur]["socket"]
    key = "game" if use["kind"] == "gaming" else "mt"
    cur_v = catalog.CPUS[cur][key]
    cur_price = table.cpu(cur)[0]
    out = []
    for name, d in catalog.CPUS.items():
        if d["socket"] != sock or name == cur:
            continue
        if d[key] <= cur_v * 1.1:
            continue
        pr, approx = table.cpu(name)
        if pr is None or approx:
            continue
        out.append(Swap("cpu", cur, name, pr, _resale(table, cur_price), pr - _resale(table, cur_price)))
    return out


def _ram_swaps(cfg: Dict, table: PriceTable, use: Dict) -> List[Swap]:
    cur = cfg.get("ram_gb") or 0
    ddr = cfg.get("ram_type") or catalog.platform_memory(cfg.get("cpu"), cfg.get("board"))
    if ddr not in ("DDR3", "DDR4", "DDR5"):
        ddr = "DDR4"
    targets = []
    if use["kind"] == "gaming":
        targets = [16, 32]
    else:
        targets = [32, 64]
    cur_price, _ = table.ram(cur, ddr) if cur else (0, False)
    out = []
    for t in targets:
        if t <= cur:
            continue
        pr, _ = table.ram(t, ddr)
        out.append(Swap("ram", "{}GB".format(cur) if cur else "(미상)", "{}GB {}".format(t, ddr), pr,
                        _resale(table, cur_price), pr - _resale(table, cur_price),
                        "듀얼 채널 {}x2 구성 권장".format(t // 2)))
    return out


def _apply(cfg: Dict, swaps: List[Swap]) -> Dict:
    c = dict(cfg)
    for s in swaps:
        if s.category == "gpu":
            c["gpu"] = s.to_name
        elif s.category == "cpu":
            c["cpu"] = s.to_name
        elif s.category == "ram":
            c["ram_gb"] = int(s.to_name.split("GB")[0])
            c["ram_sticks"] = 2
    return c


def _psu_swap(cfg_after: Dict, cfg_before: Dict, table: PriceTable) -> Optional[Swap]:
    """교체 후 구성이 현재 파워로 부족하면 파워 교체를 비용에 포함."""
    need = recommended_psu(cfg_after)
    watt = cfg_after.get("psu_watt")
    if not need or not watt:
        return None
    if watt >= need * 0.95:
        return None
    old_price = table.psu(watt, cfg_after.get("psu_grade"), cfg_after.get("psu_tier"), cfg_after.get("psu_unknown"))
    new_price = table.psu(need, "Gold", "B", False)
    resale = _resale(table, old_price)
    return Swap("psu", "{}W".format(watt), "{}W 80+ Gold급 파워".format(need), new_price, resale, new_price - resale,
                "현재 파워가 부족해 교체 비용 포함")


def _new_equiv(j: Judgment, cfg: Dict, table: PriceTable) -> Optional[int]:
    """같은 최종 구성을 신품으로 샀을 때의 대략 가격 (모니터/주변기기 제외)."""
    total = 0
    ratio = table.data.get("new_ratio_default", 0.7) or 0.7
    seen_cpu = seen_gpu = seen_ram = False
    for it in j.items:
        if it.category == "cpu":
            seen_cpu = True
            pr, _ = table.cpu(cfg.get("cpu")) if cfg.get("cpu") else (None, False)
            n = table.new_price("cpu", cfg.get("cpu"), pr)
            total += n or int(it.amount / ratio)
        elif it.category == "gpu":
            seen_gpu = True
            pr, _ = table.gpu(cfg.get("gpu")) if cfg.get("gpu") else (None, False)
            n = table.new_price("gpu", cfg.get("gpu"), pr)
            total += n or int(it.amount / ratio)
        elif it.category == "ram":
            seen_ram = True
            ddr = cfg.get("ram_type") or "DDR4"
            pr, _ = table.ram(cfg.get("ram_gb") or 16, ddr)
            total += int(pr / ratio)
        else:
            total += int(it.amount / ratio)
    if not seen_gpu and cfg.get("gpu"):
        pr, _ = table.gpu(cfg["gpu"])
        total += table.new_price("gpu", cfg["gpu"], pr) or 0
    return total or None


def advise(parsed: Parsed, table: PriceTable, use_text, budget_man: Optional[float] = None,
           today=None, judgment: Optional[Judgment] = None) -> UpgradePlan:
    use = use_text if isinstance(use_text, dict) else perf.parse_use(use_text)
    budget = int(budget_man * 10000) if budget_man else None
    j = judgment or judge(parsed, table, today)
    plan = UpgradePlan(use=use, budget=budget, price=None)
    plan.warnings = list(j.warnings)

    cfg = config_from(parsed)
    if not cfg["cpu"] or (not cfg["gpu"] and use["kind"] != "editing"):
        plan.messages.append("CPU/그래픽카드 정보가 부족해 업그레이드 추천을 할 수 없습니다. 판매자에게 먼저 물어보세요.")
        return plan

    price = j.price
    if price is None:
        price = j.fair_mid
        plan.price_is_fair_mid = True
        plan.messages.append("판매가를 몰라 적정가 중앙값({})을 매물 가격으로 가정했습니다.".format(fmt_man(price)))
    plan.price = price
    extras = sum(a.amount for a in j.extras)

    cur = evaluate(cfg, use)
    plan.current_perf = cur["perf"]
    plan.current_meets = cur["meets"]
    plan.current_notes = bottleneck_notes(cfg, use, table)
    if use["kind"] == "gaming":
        plan.target_text = "{} 목표: 체감 GPU 지수 {} 이상 (현재 {})".format(use["label"], cur["target"], cur["perf"])
    elif use["kind"] in ("llm", "diffusion"):
        plan.target_text = "{} 목표: VRAM 약 {:g}GB 이상".format(use["label"], use["vram_need"])
    else:
        plan.target_text = "{} 목표: CPU 멀티 지수 85+, GPU 55+, RAM 32GB+".format(use["label"])

    if budget is not None and budget < price:
        plan.messages.append("예산 {} 이(가) 매물 가격 {} 보다 적습니다: 이 매물만으로 예산을 넘습니다.".format(fmt_man(budget), fmt_man(price)))

    # 후보 단일 교체
    singles: Dict[str, List[Swap]] = {
        "gpu": _gpu_swaps(cfg, table, use),
        "cpu": _cpu_swaps(cfg, table, use),
        "ram": _ram_swaps(cfg, table, use),
    }
    # 단일 후보를 평가해 카테고리별 상위만 조합에 사용
    def eval_swaps(swaps: List[Swap]):
        c2 = _apply(cfg, swaps)
        ev = evaluate(c2, use)
        return c2, ev

    scored: Dict[str, List] = {}
    for cat, lst in singles.items():
        rows = []
        for s in lst:
            c2, ev = eval_swaps([s])
            rows.append((s, ev))
        # 성능 향상 대비 비용이 좋은 순 (+ 목표 충족 우선)
        rows.sort(key=lambda r: (not r[1]["meets"], r[0].delta / max(1.0, r[1]["perf"] - cur["perf"] + 1)))
        scored[cat] = rows

    combos: List[List[Swap]] = []
    for cat, rows in scored.items():
        for s, ev in rows:
            combos.append([s])
    tops = {cat: [s for s, _ in rows[:6]] for cat, rows in scored.items()}
    for a, b in (("gpu", "ram"), ("gpu", "cpu"), ("cpu", "ram")):
        for sa, sb in itertools.product(tops.get(a, []), tops.get(b, [])):
            combos.append([sa, sb])

    options: List[Option] = []
    for swaps in combos:
        c2 = _apply(cfg, swaps)
        ev = evaluate(c2, use)
        if ev["perf"] < cur["perf"] * 1.05 and not (ev["meets"] and not cur["meets"]):
            continue
        all_swaps = list(swaps)
        psu = _psu_swap(c2, cfg, table)
        if psu:
            all_swaps.append(psu)
        delta = sum(s.delta for s in all_swaps)
        outlay = price + delta
        equiv = _new_equiv(j, c2, table)
        saving = None
        if equiv:
            saving = (1.0 - (outlay - extras) / float(equiv)) * 100.0
        within = budget is None or outlay <= budget
        options.append(Option(label="", swaps=all_swaps, config=c2, perf=ev["perf"], meets=ev["meets"],
                              delta_total=delta, outlay=outlay, new_equiv=equiv, saving_pct=saving,
                              within_budget=within))

    if cur["meets"]:
        plan.messages.append("현재 구성으로 이미 목표({})를 충족합니다. 교체 없이 사용해도 됩니다.".format(plan.use["label"]))

    pool = [o for o in options if o.within_budget]
    if not pool:
        if options:
            cheapest = min(options, key=lambda o: o.outlay)
            plan.messages.append("예산 {} 안에서 목표를 올릴 교체 조합이 없습니다 (가장 싼 조합이라도 총 {}).".format(
                fmt_man(budget) if budget else "-", fmt_man(cheapest.outlay)))
        else:
            plan.messages.append("현재 구성보다 의미 있게 좋아지는 교체 후보가 없습니다.")
        return plan

    def key_cost(o):  # 싼 순, 같으면 성능 높은 순, 교체 개수 적은 순
        return (o.outlay, -o.perf, len(o.swaps))

    meets = [o for o in pool if o.meets]
    chosen: List[Option] = []
    if cur["meets"]:
        # 이미 충족: 선택 사항으로 가장 가성비 좋은 성능 향상 하나만
        better = [o for o in pool if o.perf >= cur["perf"] * 1.15]
        if better:
            o = min(better, key=lambda o: (o.delta_total / max(1.0, o.perf - cur["perf"]), len(o.swaps)))
            o.label = "선택 사항 (여유 확보)"
            chosen.append(o)
    else:
        if meets:
            a = min(meets, key=key_cost)
            a.label = "가성비 교체 (목표 충족 최소 비용)"
        else:
            a = max(pool, key=lambda o: (o.perf, -o.outlay))
            a.label = "예산 내 최선 (목표에는 못 미침)"
        chosen.append(a)
        # 두 번째: 예산 내 최대 성능 (a 와 의미 있게 다를 때)
        bmax = max(pool, key=lambda o: (o.perf, -o.outlay, -len(o.swaps)))
        if bmax is not a and bmax.perf > a.perf * 1.08:
            # 성능 차이가 작으면 더 싼 쪽을 선택
            near = [o for o in pool if o.perf >= bmax.perf * 0.97]
            bsel = min(near, key=key_cost)
            if bsel is not a and bsel.perf > a.perf * 1.08:
                bsel.label = "여유 교체 (예산 내 최대 성능)" if budget else "여유 교체 (한 단계 위)"
                chosen.append(bsel)

    for o in chosen:
        _finalize(o, price, cfg, use, table)
    plan.options = chosen
    return plan


def _finalize(o: Option, price: int, cfg: Dict, use: Dict, table: PriceTable) -> None:
    parts = []
    for s in o.swaps:
        verb = "교체" if s.category != "ram" else "증설/교체"
        parts.append("{}{} {}(차액 {})".format(s.to_name, ro(s.to_name), verb, fmt_won(s.delta)))
    save = ""
    if o.saving_pct is not None:
        save = ", 신품 대비 {}% 절약".format(int(round(o.saving_pct))) if o.saving_pct >= 0 else \
            ", 신품 대비 {}% 더 비쌈".format(int(round(-o.saving_pct)))
    o.headline = "이 매물 {} + {} → 총 {}{}".format(fmt_won(price), " + ".join(parts), fmt_won(o.outlay), save)
    o.notes = bottleneck_notes(o.config, use, table)
    for s in o.swaps:
        if s.note:
            o.notes.append("{}: {}".format(s.category.upper(), s.note))
    if o.saving_pct is not None:
        o.notes.append("신품 환산가는 시세표 'new' 값 또는 중고가/{:.2f} 로 계산한 대략치입니다.".format(
            table.data.get("new_ratio_default", 0.7)))
    o.notes.append("차액 = 새 부품 중고가 - 기존 부품 중고가x{:.2f}(되팔 때 수수료·감가 반영). 되팔지 않으면 차액이 아니라 전액이 듭니다.".format(
        table.adj("upgrade_resale_factor")))
