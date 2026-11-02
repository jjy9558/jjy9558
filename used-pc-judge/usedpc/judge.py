"""적정가 판정: 부품 합계 -> 보정 -> 범위 -> 판정(싸다/적정/비싸다/의심)."""

from dataclasses import dataclass, field
from typing import List, Optional

from . import catalog, perf
from .listing_info import _parse_today
from .models import Parsed
from .prices import PriceTable
from .textutil import fmt_man, round_to

VERDICTS = ("싸다", "적정", "비싸다", "의심", "판단불가")


@dataclass
class Item:
    category: str
    label: str
    amount: int
    assumed: bool = False
    note: str = ""


@dataclass
class Adj:
    label: str
    amount: int
    note: str = ""


@dataclass
class Judgment:
    parsed: Parsed
    items: List[Item] = field(default_factory=list)
    parts_sum: int = 0
    adjustments: List[Adj] = field(default_factory=list)
    extras: List[Adj] = field(default_factory=list)       # 모니터/주변기기
    fair_mid: Optional[int] = None
    fair_low: Optional[int] = None
    fair_high: Optional[int] = None
    spread: float = 0.0
    price: Optional[int] = None
    verdict: str = "판단불가"
    diff_pct: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    confidence: str = "낮음"
    value_score: Optional[float] = None
    perf_index: Optional[float] = None
    sev_penalty: int = 0
    unpriced: List[str] = field(default_factory=list)  # 가격표에 없어서 합계에서 빠진 부품


def cpu_has_igpu(name: str) -> bool:
    if name.startswith("Ultra"):
        return not name.endswith("F") and not name.endswith("KF")
    if name.startswith("i"):
        return not (name.endswith("F") or name.endswith("KF"))
    if name.startswith("Ryzen"):
        if name.endswith("G") or name.endswith("GT"):
            return True
        if "AM5" == catalog.CPUS.get(name, {}).get("socket"):
            return not name.endswith("F")
        return False
    return False


def judge(parsed: Parsed, table: PriceTable, today=None) -> Judgment:
    today_d = _parse_today(today or parsed.today)
    j = Judgment(parsed=parsed)
    j.warnings = table.warnings(today_d)
    kind = parsed.val("kind", "full_pc")
    price = parsed.val("price")
    j.price = price
    excluded = parsed.excluded

    if kind == "laptop":
        j.verdict = "판단불가"
        j.reasons.append("노트북/모바일 기기로 보입니다. 이 도구는 데스크탑 PC 부품 합산 방식이라 판정하지 않습니다.")
        return j

    cpu = parsed.val("cpu")
    gpu = parsed.val("gpu")
    full = kind == "full_pc"
    assumed_weight = 0

    # ---- CPU ----
    if cpu:
        pr, approx = table.cpu(cpu)
        generic = parsed.fields["cpu"].meta.get("generic")
        if pr is None:
            j.unpriced.append("CPU " + cpu)
        else:
            note = "시세표 근사(비슷한 모델 가격 사용)" if approx else ""
            if generic:
                note = "세대만 적혀 있어 대표 모델로 가정"
            j.items.append(Item("cpu", "CPU " + cpu, pr, approx or bool(generic), note))
            if approx or generic or parsed.conf("cpu") < 0.7:
                assumed_weight += 1
    elif full:
        j.missing.append("cpu")

    # ---- GPU ----
    if "gpu" in excluded:
        j.assumptions.append("그래픽카드 제외 판매로 보여 GPU 금액을 넣지 않았습니다")
    elif gpu:
        pr, _ = table.gpu(gpu)
        if pr is None:
            j.unpriced.append("GPU " + gpu)
        else:
            f = parsed.fields["gpu"]
            note = ""
            if f.meta.get("defaulted"):
                note = "VRAM 용량이 안 적혀 있어 {} 로 가정".format(gpu)
                assumed_weight += 1
            j.items.append(Item("gpu", "GPU " + gpu, pr, bool(f.meta.get("defaulted")), note))
    elif full:
        if cpu and cpu_has_igpu(cpu):
            j.assumptions.append("그래픽카드 언급이 없고 CPU에 내장그래픽이 있어 사무용(내장그래픽)으로 보고 GPU 금액을 넣지 않았습니다")
        else:
            j.missing.append("gpu")

    # ---- RAM ----
    ram_gb = parsed.val("ram_gb")
    if "ram" not in excluded:
        if ram_gb:
            ddr = parsed.val("ram_type")
            plat = catalog.platform_memory(cpu, parsed.val("board"))
            ddr_assumed = False
            if not ddr:
                ddr = plat if plat in ("DDR4", "DDR5") else "DDR4"
                ddr_assumed = True
            pr, _ = table.ram(ram_gb, ddr)
            note = "DDR 규격이 없어 {} 로 가정".format(ddr) if ddr_assumed else ""
            guessed = parsed.fields["ram_gb"].meta.get("guessed")
            if guessed:
                note = (note + " / " if note else "") + "용량이 문맥 없이 적혀 있어 RAM으로 추정"
            j.items.append(Item("ram", "RAM {}GB {}".format(ram_gb, ddr), pr, ddr_assumed or bool(guessed), note))
            if ddr_assumed:
                assumed_weight += 0.5
        elif full:
            ddr = catalog.platform_memory(cpu, parsed.val("board"))
            ddr = ddr if ddr in ("DDR4", "DDR5") else "DDR4"
            pr, _ = table.ram(16, ddr)
            j.items.append(Item("ram", "RAM 16GB {}".format(ddr), pr, True, "글에 RAM 정보가 없어 16GB로 가정"))
            j.missing.append("ram")
            assumed_weight += 1

    # ---- 저장장치 ----
    st = parsed.val("storage")
    if "ssd" not in excluded:
        if st:
            for it in st:
                pr = table.storage(it["kind"], it["gb"])
                label = {"nvme": "NVMe", "sata_ssd": "SATA SSD", "ssd": "SSD", "hdd": "HDD"}.get(it["kind"], it["kind"])
                size = "{}TB".format(it["gb"] / 1000.0).replace(".0TB", "TB") if it["gb"] >= 1000 else "{}GB".format(it["gb"])
                note = "NVMe/SATA 구분이 없어 SATA 가격으로 계산" if it["kind"] == "ssd" else ""
                j.items.append(Item("storage", "{} {}".format(label, size), pr, False, note))
        elif full:
            pr = table.storage("nvme", 500)
            j.items.append(Item("storage", "NVMe 500GB", pr, True, "글에 저장장치 정보가 없어 가정"))
            j.missing.append("storage")
            assumed_weight += 1

    # ---- 메인보드 ----
    board = parsed.val("board")
    if full or board:
        if board:
            pr = table.board(board)
            if pr is not None:
                j.items.append(Item("board", "메인보드 " + board, pr))
            else:
                j.unpriced.append("보드 " + board)
        elif cpu and cpu in catalog.CPUS:
            sock = catalog.CPUS[cpu]["socket"]
            chip = catalog.SOCKET_DEFAULT_CHIPSET.get(sock)
            pr = table.board(chip) if chip else None
            if pr is not None:
                j.items.append(Item("board", "메인보드 {}".format(chip), pr, True,
                                    "보드 정보가 없어 {} 소켓 기본 칩셋으로 가정".format(sock)))
                assumed_weight += 1
            j.missing.append("board")

    # ---- 파워 ----
    if "psu" not in excluded and full:
        watt = parsed.val("psu_watt")
        grade = parsed.val("psu_grade")
        brand_tier = parsed.fields["psu_brand"].meta.get("tier") if parsed.has("psu_brand") else None
        noname = parsed.has_flag("psu_unknown")
        if watt:
            pr = table.psu(watt, grade, brand_tier, noname)
            label = "파워 {}W".format(watt)
            if grade:
                label += " " + grade
            if parsed.has("psu_brand"):
                label = parsed.val("psu_brand") + " " + label
            note = ""
            assumed = False
            if not parsed.has("psu_brand") and not noname:
                note = "브랜드 미확인(미확인 브랜드 할인 적용)"
            j.items.append(Item("psu", label, pr, assumed, note))
            if not parsed.has("psu_brand"):
                j.missing.append("psu_brand")
        else:
            rec = catalog.GPUS[gpu]["psu_rec"] if gpu in catalog.GPUS else 550
            pr = table.psu(rec, "Bronze", None, False)
            j.items.append(Item("psu", "파워 {}W 브론즈급".format(rec), pr, True, "파워 정보가 없어 가정"))
            j.missing.append("psu")
            assumed_weight += 1

    # ---- 케이스 / 쿨러 ----
    if full:
        if "case" not in excluded:
            cname = parsed.val("case")
            j.items.append(Item("case", "케이스" + (" ({})".format(cname) if cname else ""),
                                table.case(cname), not cname, "" if cname else "케이스 정보 없음: 기본값 가정"))
        if "cooler" not in excluded:
            ci = parsed.val("cooler")
            if ci:
                j.items.append(Item("cooler", "쿨러 ({}{})".format({"aio": "수랭", "air": "공랭", "stock": "정품 쿨러"}.get(ci["kind"], ci["kind"]),
                                                              " {}mm".format(ci["size"]) if ci.get("size") else ""),
                                    table.cooler(ci), False))
            else:
                j.items.append(Item("cooler", "쿨러", table.cooler(None), True, "쿨러 정보 없음: 기본값 가정"))

    j.parts_sum = sum(i.amount for i in j.items)

    # ---- 보정 ----
    running = float(j.parts_sum)
    flags = {f.code: f for f in parsed.flags}

    def add_adj(label, amount, note=""):
        nonlocal running
        amount = int(round(amount))
        if amount:
            j.adjustments.append(Adj(label, amount, note))
            running += amount

    gpu_item = next((i for i in j.items if i.category == "gpu"), None)
    if "mining" in flags and gpu_item:
        add_adj("채굴 이력 언급 (GPU 할인 {:.0f}%)".format(table.adj("mining_gpu_discount") * 100),
                -gpu_item.amount * table.adj("mining_gpu_discount"))
    if "refurb" in flags:
        add_adj("리퍼/재생품 ({:.0f}%)".format(table.adj("refurb_discount") * 100), -running * table.adj("refurb_discount"))
    if "boot_fail" in flags:
        add_adj("부팅/작동 불량 언급 (-{:.0f}%)".format(table.adj("defect_discount") * 100), -running * table.adj("defect_discount"))
    elif "unstable" in flags:
        add_adj("불안정 증상 언급 (-{:.0f}%)".format(table.adj("unstable_discount") * 100), -running * table.adj("unstable_discount"))

    if full and parsed.val("brand_pc"):
        add_adj("브랜드 PC (x{})".format(table.adj("brand_pc_factor")), running * (table.adj("brand_pc_factor") - 1))
    elif full:
        add_adj("완본체 보정 (부품 합계 x{})".format(table.adj("assembled_factor")),
                running * (table.adj("assembled_factor") - 1),
                "완제품은 부품 단품 합계보다 대체로 낮게 거래된다고 가정")

    age = parsed.val("age_years")
    if age is not None and age > table.adj("age_start_years"):
        rate = min(table.adj("age_cap"), (age - table.adj("age_start_years")) * table.adj("age_per_year"))
        add_adj("사용 연수 {:g}년 (-{:.0f}%)".format(age, rate * 100), -running * rate)

    w = parsed.val("warranty")
    if w and w.get("has") and w.get("months_left"):
        rate = min(table.adj("warranty_cap"), w["months_left"] * table.adj("warranty_per_month"))
        if w.get("receipt"):
            rate += table.adj("receipt_bonus")
        add_adj("무상 보증 약 {:g}개월 남음 (+{:.1f}%)".format(w["months_left"], rate * 100), running * rate)
    elif w and w.get("has") and w.get("months_left") is None:
        add_adj("보증 남아있다고 함 (기간 불명, +{:.1f}%)".format(1.0), running * 0.01, "기간이 안 적혀 있어 최소 가산")
    elif w and w.get("receipt"):
        add_adj("영수증/구매내역 (+{:.1f}%)".format(table.adj("receipt_bonus") * 100), running * table.adj("receipt_bonus"))

    if "no_box" in flags:
        add_adj("박스 없음 (-{:.0f}%)".format(table.adj("no_box_discount") * 100), -running * table.adj("no_box_discount"))
    if "psu_unknown" in flags:
        add_adj("무명/불명 파워 교체 비용", -table.adj("psu_unknown_flat"), "안전을 위해 교체한다고 가정")
    if "cooler_noise" in flags:
        add_adj("쿨러/팬 소음 (교체 비용)", -table.adj("cooler_noise_flat"))

    # 모니터/주변기기 (완본체 보정 대상이 아님)
    mon = parsed.val("monitor")
    extra_total = 0
    if mon and mon.get("included"):
        mv = int(table.monitor(mon) * table.adj("monitor_resale_factor"))
        desc = "모니터 포함"
        if mon.get("inch"):
            desc += " {}인치".format(mon["inch"])
        if mon.get("hz"):
            desc += " {}Hz".format(mon["hz"])
        j.extras.append(Adj(desc + " (중고 가치)", mv))
        extra_total += mv
    per = parsed.val("peripherals")
    if per:
        pv = table.peripherals(per)
        j.extras.append(Adj("주변기기 포함 ({})".format(", ".join(per)), pv))
        extra_total += pv

    mid = max(0.0, running + extra_total)

    # ---- 범위 ----
    spread = table.adj("spread_base") + table.adj("spread_per_assumption") * assumed_weight
    if parsed.has("cpu") and parsed.conf("cpu") < 0.5:
        spread += 0.03
    spread = min(table.adj("spread_cap"), spread)
    unit = int(table.adj("round_unit"))
    j.spread = spread
    j.fair_mid = round_to(mid, unit)
    j.fair_low = round_to(mid * (1 - spread), unit)
    j.fair_high = round_to(mid * (1 + spread), unit)

    # ---- 신뢰도 ----
    core_missing = [m for m in j.missing if m in ("cpu", "gpu")]
    if core_missing or j.unpriced:
        j.confidence = "낮음"
    elif assumed_weight >= 2 or j.missing:
        j.confidence = "보통"
    else:
        j.confidence = "높음"

    # ---- 판정 ----
    _verdict(j, table, kind)
    j.perf_index = perf.general_index(cpu, gpu)
    _value_score(j)
    return j


def _verdict(j: Judgment, table: PriceTable, kind: str) -> None:
    parsed = j.parsed
    price = j.price
    reasons = j.reasons

    if kind == "single_part":
        reasons.append("단품(부품) 매물로 보고 해당 부품 시세만 비교했습니다 (완본체 보정 없음).")

    if price is None:
        j.verdict = "판단불가"
        reasons.append("판매가를 찾지 못했습니다. 적정가 범위만 참고하세요 (가격을 'N만원' 형태로 적어 주세요).")
        return
    if "cpu" in j.missing or "gpu" in j.missing:
        what = "CPU" if "cpu" in j.missing else "그래픽카드"
        j.verdict = "판단불가"
        reasons.append("핵심 부품({}) 정보가 없어 적정가를 믿고 판정하기 어렵습니다. 판매자에게 먼저 물어보세요.".format(what))
        return
    if not j.items:
        j.verdict = "판단불가"
        reasons.append("인식된 부품이 없어 판정할 수 없습니다.")
        return
    if j.unpriced:
        reasons.append("가격표에 없어 합계에서 빠진 부품: {} -> 적정가가 실제보다 낮게 나왔을 수 있음".format(", ".join(j.unpriced)))

    scam_flags = [f for f in parsed.flags if f.scam]
    scam_score = sum(f.severity for f in scam_flags)
    hard_scam = any(f.severity >= 3 for f in scam_flags) or scam_score >= 4
    defect = any(f.defect and f.severity >= 3 for f in parsed.flags)
    ratio_low = j.fair_low and price / float(j.fair_low)

    j.diff_pct = (price - j.fair_mid) / float(j.fair_mid) * 100 if j.fair_mid else None
    pr_txt = "판매가 {} vs 적정가 {} ~ {} (중앙 {})".format(
        fmt_man(price), fmt_man(j.fair_low), fmt_man(j.fair_high), fmt_man(j.fair_mid))

    if hard_scam:
        j.verdict = "의심"
        reasons.append("사기 의심 신호가 겹칩니다: " + ", ".join(f.label for f in scam_flags))
        reasons.append(pr_txt)
    elif j.fair_low and price < j.fair_low * table.adj("scam_ratio") and not defect:
        j.verdict = "의심"
        reasons.append(pr_txt)
        reasons.append("시세 하한의 {:.0f}% 수준: 너무 쌉니다. 사기/장물/숨은 하자 가능성을 의심하세요 "
                       "(단, 가격표가 낡았을 수도 있음).".format(ratio_low * 100))
    elif j.fair_low and price < j.fair_low:
        j.verdict = "싸다"
        reasons.append(pr_txt)
        if any(f.code == "too_good" for f in scam_flags) and ratio_low < 0.85:
            j.verdict = "의심"
            reasons.append("'시세보다 싸다'는 과장 문구 + 시세 이하 가격: 사기 의심 패턴입니다.")
        else:
            reasons.append("시세보다 저렴합니다 ({:+.0f}%). 싼 이유(급처/하자/채굴/보증 없음)를 확인하세요.".format(j.diff_pct))
    elif j.fair_high and price <= j.fair_high:
        j.verdict = "적정"
        reasons.append(pr_txt)
        reasons.append("적정 범위 안입니다 (중앙값 대비 {:+.0f}%).".format(j.diff_pct))
    else:
        j.verdict = "비싸다"
        reasons.append(pr_txt)
        reasons.append("적정 상한보다 {} 비쌉니다 (중앙값 대비 {:+.0f}%). 네고하거나 다른 매물을 보세요.".format(
            fmt_man(price - j.fair_high), j.diff_pct))

    if defect:
        reasons.append("부팅/작동 불량 언급이 있어 '수리/부품용' 가정으로 계산했습니다. 정상 제품 가격과 비교하지 마세요.")
    for a in j.adjustments:
        sign = "+" if a.amount > 0 else "-"
        reasons.append("보정: {} {}{}".format(a.label, sign, fmt_man(abs(a.amount))))
    for a in j.extras:
        reasons.append("가산: {} +{}".format(a.label, fmt_man(a.amount)))
    for f in parsed.flags:
        reasons.append("주의[{}]: {} ('{}')".format("!" * f.severity, f.label, f.evidence))
    for pos in parsed.positives:
        reasons.append("긍정: " + pos)
    for a in j.assumptions:
        reasons.append("가정: " + a)
    for it in j.items:
        if it.assumed and it.note:
            reasons.append("가정: {} - {}".format(it.label, it.note))
    if parsed.has("cpu") and parsed.fields["cpu"].meta.get("generic"):
        reasons.append("CPU가 세대만 적혀 있어({}), 정확한 모델명을 확인해야 합니다.".format(parsed.fields["cpu"].raw))
    for n in parsed.notes:
        reasons.append("참고: " + n)
    if parsed.has("original_price"):
        reasons.append("참고: 글에 언급된 신품/구매 가격 {} (판매가 비교용 아님)".format(fmt_man(parsed.val("original_price"))))


def _value_score(j: Judgment) -> None:
    """compare 정렬용 점수. 100 = 적정가 중앙값과 같음. 높을수록 이득."""
    if not j.price or not j.fair_mid:
        j.value_score = None
        return
    score = j.fair_mid / float(j.price) * 100.0
    penalty = 0
    for f in j.parsed.flags:
        if f.severity >= 3:
            penalty += 25
        elif f.severity == 2:
            penalty += 8
        else:
            penalty += 2
    j.sev_penalty = penalty
    score -= penalty
    if j.verdict == "의심":
        score = min(score, 40.0)
    if j.verdict == "판단불가":
        score = None
    j.value_score = round(score, 1) if score is not None else None
