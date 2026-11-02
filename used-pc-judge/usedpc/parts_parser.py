"""하드웨어 부품 추출 (규칙 기반).

각 extract_* 함수는 Doc(정규화 텍스트 + 마스크)에서 부품을 찾아 Parsed 에 채운다.
먼저 해석한 구간은 Doc.claim() 으로 가려서 뒤 단계가 같은 숫자를 다시 쓰지 않는다.
"""

import re
from typing import List, Optional, Tuple

from . import catalog
from .aliases import Doc
from .models import Parsed

# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------
_INTEL_RE = re.compile(
    r"(?<![a-z0-9])(?:intel\s*)?(?:core\s*)?i\s*([3579])[\s\-]*(\d{4,5})\s*(kf|ks|k|f|t)?(?![a-z0-9])")
_ULTRA_RE = re.compile(
    r"(?<![a-z0-9])(?:intel\s*)?(?:core\s*)?ultra\s*([579])[\s\-]*(\d{3})\s*(kf|k|f|s|h|hx)?(?![a-z0-9])")
_RYZEN_RE = re.compile(
    r"(?<![a-z0-9])(?:ryzen\s*(?:([3579])\s*)?|r([3579])[\s\-]*)(\d{4})\s*(x3d|xt|gt|x|g|f)?(?![a-z0-9])")
_AMD_PREFIX_RE = re.compile(
    r"(?<![a-z0-9])amd\s*(?:cpu\s*)?(\d{4})\s*(x3d|xt|gt|x|g|f)?(?![a-z0-9])")
_INTEL_GEN_RE1 = re.compile(
    r"(?<![a-z0-9])(?:intel\s*)?(?:core\s*)?i\s*([3579])[\s\-]*(?:(\d{1,2})\s*세대|(\d{1,2})\s*th\s*gen|(\d{1,2})\s*gen)")
_INTEL_GEN_RE2 = re.compile(
    r"(\d{1,2})\s*세대\s*(?:intel\s*)?(?:core\s*)?i\s*([3579])(?![a-z0-9])")
_BARE_INTEL_RE = re.compile(r"(?<![a-z0-9.,\-])(1[0-4]\d{3}|[6-9]\d{3})\s*(kf|ks|k|f|t)?(?![a-z0-9])")
_BARE_NUM_SUFFIX_RE = re.compile(
    r"(?<![a-z0-9.,\-])(\d{4})\s*(x3d|xtx|xt|gre|gt|x|g|f|ti\s*super|tis|ti|super|s)?(?![a-z0-9])")


def _intel_name(fam: str, num: str, suf: str) -> str:
    return "i{}-{}{}".format(fam, num, suf.upper())


def _resolve_intel(fam: str, num: str, suf: str) -> Tuple[Optional[str], float, bool]:
    """-> (정식 이름, 신뢰도, 근사 여부)"""
    name = _intel_name(fam, num, suf)
    if name in catalog.CPUS:
        return name, 0.95, False
    if not suf:
        # 접미사 없는 번호: F 모델이 있으면 근사 (둘 중 하나)
        for alt in (name + "F", name + "K"):
            if alt in catalog.CPUS:
                return alt, 0.6, True
    near = catalog.nearest_cpu(name)
    if near:
        return near, 0.6, True
    return None, 0.0, False


def _resolve_ryzen(tier: Optional[str], num: str, suf: str) -> Tuple[Optional[str], float, bool]:
    suf = suf.upper()
    if suf == "XT":
        suf = "X"
    cand = []
    if tier:
        cand.append("Ryzen {} {}{}".format(tier, num, suf))
    else:
        for t in "3579":
            cand.append("Ryzen {} {}{}".format(t, num, suf))
    for c in cand:
        if c in catalog.CPUS:
            return c, 0.95 if tier else 0.85, False
    # 접미사를 뺀 모델로 근사 (예: 5600F -> 5600)
    for c in cand:
        base = c[: len(c) - len(suf)] if suf else c
        if base in catalog.CPUS:
            return base, 0.6, True
    return None, 0.0, False


def extract_cpu(doc: Doc, p: Parsed) -> None:
    found: List[Tuple[str, float, bool, str, int, int]] = []

    for m in doc.finditer(_ULTRA_RE):
        fam, num, suf = m.group(1), m.group(2), m.group(3) or ""
        name = "Ultra {} {}{}".format(fam, num, suf.upper())
        if name in catalog.CPUS:
            found.append((name, 0.95, False, m.group(0), m.start(), m.end()))
        else:
            near = catalog.nearest_cpu(name)
            if near:
                found.append((near, 0.6, True, m.group(0), m.start(), m.end()))

    for m in doc.finditer(_INTEL_RE):
        name, conf, approx = _resolve_intel(m.group(1), m.group(2), m.group(3) or "")
        if name:
            found.append((name, conf, approx, m.group(0), m.start(), m.end()))

    for m in doc.finditer(_RYZEN_RE):
        tier = m.group(1) or m.group(2)
        name, conf, approx = _resolve_ryzen(tier, m.group(3), m.group(4) or "")
        if name:
            found.append((name, conf, approx, m.group(0), m.start(), m.end()))

    for m in doc.finditer(_AMD_PREFIX_RE):
        name, conf, approx = _resolve_ryzen(None, m.group(1), m.group(2) or "")
        if name:
            found.append((name, conf, approx, m.group(0), m.start(), m.end()))

    if found:
        found.sort(key=lambda x: (-x[1], x[4]))
        name, conf, approx, raw, s, e = found[0]
        p.set("cpu", name, conf, raw.strip(), approx=approx)
        for f in found:
            doc.claim(f[4], f[5])
        if len(set(f[0] for f in found)) > 1:
            others = sorted(set(f[0] for f in found) - {name})
            p.notes.append("CPU 후보가 여러 개: {} (사용: {})".format(", ".join(others), name))
        return

    # 세대만 적힌 경우 ("i5 12세대")
    for m in doc.finditer(_INTEL_GEN_RE1):
        fam = m.group(1)
        gen = int(m.group(2) or m.group(3) or m.group(4))
        rep = catalog.typical_cpu("i" + fam, gen)
        if rep:
            p.set("cpu", rep, 0.4, m.group(0).strip(), approx=True, generic=True,
                  family="i" + fam, gen=gen)
            doc.claim(m.start(), m.end())
            return
    for m in doc.finditer(_INTEL_GEN_RE2):
        gen, fam = int(m.group(1)), m.group(2)
        rep = catalog.typical_cpu("i" + fam, gen)
        if rep:
            p.set("cpu", rep, 0.4, m.group(0).strip(), approx=True, generic=True,
                  family="i" + fam, gen=gen)
            doc.claim(m.start(), m.end())
            return


def _bare_cpu_candidates(doc: Doc) -> List[Tuple[str, float, bool, str, int, int]]:
    out = []
    for m in doc.finditer(_BARE_INTEL_RE):
        num, suf = m.group(1), m.group(2) or ""
        if len(num) == 4 and not suf:
            continue  # 접미사 없는 4자리는 너무 모호함
        name = None
        approx = False
        for fam in "5793":
            n = _intel_name(fam, num, suf)
            if n in catalog.CPUS:
                name = n
                break
        if not name and not suf:
            for fam in "5793":
                for alt in ("F", "K"):
                    n = _intel_name(fam, num, alt)
                    if n in catalog.CPUS:
                        name, approx = n, True
                        break
                if name:
                    break
        if name:
            out.append((name, 0.6 if approx else 0.75, approx, m.group(0), m.start(), m.end()))
    return out


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------
_NV_PREFIX_RE = re.compile(
    r"(?<![a-z0-9])(?:(?:rtx|gtx|geforce|nvidia|gt)[\s\-]*)+(\d{3,4})(?![0-9])")
_AMD_GPU_RE = re.compile(
    r"(?<![a-z0-9])(?:rx|radeon(?:\s*rx)?)\s*[\-]?\s*(\d{3,4})\s*(xtx|xt|gre|gt)?(?![a-z])")
_ARC_RE = re.compile(r"(?<![a-z0-9])arc\s*([ab])\s*(\d{3})(?![0-9])")
_NV_SUFFIX_RE = re.compile(r"\s*(ti\s*super|ti\s*s(?![a-z])|tis(?![a-z])|ti(?![a-z])|super(?![a-z])|s(?![a-z]))?")
_VRAM_RE = re.compile(r"\s*[,/\-(]?\s*(\d{1,2})\s*(?:gb|g)(?![a-z0-9])")
_S_SHORT_OK = {"1650", "1660", "2060", "2070", "2080", "4070", "4080"}


def _nv_key(num: str, suffix: str) -> Optional[str]:
    suffix = re.sub(r"\s+", "", suffix or "")
    series = "gtx" if num.startswith(("10", "16")) else "rtx"
    key = "{} {}".format(series, num)
    if suffix in ("tisuper", "tis"):
        key += " ti super"
    elif suffix == "ti":
        key += " ti"
    elif suffix in ("super", "s"):
        key += " super"
    return key


def _resolve_nv(num: str, suffix: str, vram: Optional[int]) -> Tuple[Optional[str], bool, int]:
    """-> (정식 이름, 기본값추정 여부, 소비한 VRAM 표시 여부 0/1)"""
    suffix = re.sub(r"\s+", "", suffix or "")
    if suffix == "s" and num not in _S_SHORT_OK:
        suffix = ""
    key = _nv_key(num, suffix)
    name, defaulted = catalog.gpu_by_key(key, vram)
    if name is None and suffix:
        # 존재하지 않는 조합이면 접미사 없이 시도 (예: 3060s)
        name, defaulted = catalog.gpu_by_key(_nv_key(num, ""), vram)
    return name, defaulted, 0


def _take_vram(doc: Doc, pos: int, key: str) -> Tuple[Optional[int], int]:
    """모델명 뒤에 붙은 '12g' 를 VRAM으로 소비. 해당 모델에 있는 용량일 때만."""
    m = _VRAM_RE.match(doc.masked, pos)
    if not m:
        return None, pos
    v = int(m.group(1))
    if v in catalog.gpu_variants(key):
        return v, m.end()
    return None, pos


def extract_gpu(doc: Doc, p: Parsed) -> None:
    found = []  # (name, conf, defaulted, raw, start, end, key)
    for m in doc.finditer(_ARC_RE):
        name = "Arc {}{}".format(m.group(1).upper(), m.group(2))
        for cn in catalog.GPUS:
            if cn.startswith(name):
                found.append((cn, 0.9, False, m.group(0), m.start(), m.end(), name.lower()))
                break

    for m in doc.finditer(_NV_PREFIX_RE):
        num = m.group(1)
        if num not in catalog.NV_NUMBERS_ALL:
            p.notes.append("인식하지 못한 그래픽카드 표기: '{}' (카탈로그에 없는 구형/특수 모델이면 직접 시세를 확인하세요)".format(
                doc.masked[m.start():m.end()].strip()))
            continue
        sm = _NV_SUFFIX_RE.match(doc.masked, m.end())
        suffix = sm.group(1) or ""
        end = sm.end() if suffix else m.end()
        base_key = _nv_key(num, suffix)
        vram, end2 = _take_vram(doc, end, base_key)
        if vram is None and suffix:
            pass
        name, defaulted, _ = _resolve_nv(num, suffix, vram)
        if name:
            conf = 0.95 if not defaulted else 0.75
            found.append((name, conf, defaulted, doc.masked[m.start():end2].strip(), m.start(), end2, base_key))

    for m in doc.finditer(_AMD_GPU_RE):
        num, suf = m.group(1), m.group(2) or ""
        key = "rx {}{}".format(num, " " + suf if suf else "")
        end = m.end()
        vram, end2 = _take_vram(doc, end, key)
        name, defaulted = catalog.gpu_by_key(key, vram)
        if name is None and num == "580":
            name, defaulted = catalog.gpu_by_key("rx 580", vram)
        if name:
            found.append((name, 0.95 if not defaulted else 0.75, defaulted,
                          doc.masked[m.start():end2].strip(), m.start(), end2, key))

    if not found:
        return
    found.sort(key=lambda x: (-x[1], x[4]))
    name, conf, defaulted, raw, s, e, key = found[0]
    meta = {"defaulted": defaulted}
    if defaulted:
        meta["variants"] = catalog.gpu_variants(key)
    p.set("gpu", name, conf, raw, **meta)
    for f in found:
        doc.claim(f[4], f[5])
    names = sorted(set(f[0] for f in found))
    if len(names) > 1:
        p.notes.append("그래픽카드 후보가 여러 개: {} (가장 확실한 것: {}; 2개 장착인지 확인 필요)".format(
            ", ".join(names), name))
        p.set("gpu_candidates", names, 0.6)


def extract_bare_numbers(doc: Doc, p: Parsed) -> None:
    """접두사 없는 숫자('3060ti', '12400f', '5800x3d')를 화이트리스트로 해석한다."""
    cands = []  # (kind, name, conf, raw, start, end, ambiguous, defaulted)
    for m in doc.finditer(_BARE_NUM_SUFFIX_RE):
        num = m.group(1)
        suf = re.sub(r"\s+", "", m.group(2) or "")
        raw = m.group(0).strip()
        # 라이젠 우선 번호 (접미사로 구분)
        if suf in ("x3d", "x", "g", "gt", "f") or (suf == "" and num in ("5600", "5500", "5700", "7600", "7700", "7900", "9600", "9700", "9800")):
            name, conf, approx = _resolve_ryzen(None, num, suf)
            if name and (suf or num in ("5600", "7600", "7700", "7900", "5500", "5700")):
                amb = (suf == "" and num in catalog.AMBIGUOUS_CPU_GPU)
                cands.append(("cpu", name, 0.8 if not amb else 0.5, raw, m.start(), m.end(), amb, False))
                continue
        if suf in ("xt", "xtx", "gre") and num in catalog.AMD_NUMBERS_ALL:
            key = "rx {} {}".format(num, suf)
            name, defaulted = catalog.gpu_by_key(key, None)
            if name:
                cands.append(("gpu", name, 0.8, raw, m.start(), m.end(), False, defaulted))
                continue
        if num in catalog.NV_NUMBERS_BARE:
            sfx = suf if suf in ("ti", "super", "s", "tis", "tisuper") else ""
            if suf == "ti super":
                sfx = "tisuper"
            if sfx == "s" and num not in _S_SHORT_OK:
                sfx = ""
            key = _nv_key(num, sfx)
            if not catalog.gpu_variants(key) and sfx:
                key = _nv_key(num, "")
            vram, end2 = _take_vram(doc, m.end(), key)
            name, defaulted = catalog.gpu_by_key(key, vram)
            if name:
                cands.append(("gpu", name, 0.8 if not defaulted else 0.6, raw, m.start(), end2, False, defaulted))
                continue
        if num in catalog.AMD_NUMBERS_BARE and suf == "":
            key = "rx {}".format(num)
            name, defaulted = catalog.gpu_by_key(key, None)
            if name:
                amb = num in catalog.AMBIGUOUS_CPU_GPU
                cands.append(("gpu", name, 0.7 if not amb else 0.5, raw, m.start(), m.end(), amb, defaulted))
    for c in _bare_cpu_candidates(doc):
        cands.append(("cpu", c[0], c[1], c[3], c[4], c[5], False, False))

    # 모호한 7600/5600 같은 번호 정리: cpu 가 이미 있으면 GPU, gpu 가 이미 있으면 CPU
    have_cpu = p.has("cpu")
    have_gpu = p.has("gpu")
    # 모호하지 않은 후보 먼저 적용
    ordered = sorted(cands, key=lambda c: (c[6], c[4]))
    for kind, name, conf, raw, s, e, amb, defaulted in ordered:
        if amb:
            # 이 번호는 CPU 이름(라이젠 5600 등) 과 GPU 이름(RX 7600 등)이 겹친다
            num = re.search(r"\d{4}", name).group(0)
            if have_cpu and not have_gpu:
                kind, conf = "gpu", 0.5
                gname, defaulted = catalog.gpu_by_key("rx " + num, None)
                if not gname:
                    continue
                name = gname
            elif have_gpu and not have_cpu:
                kind = "cpu"
            elif have_cpu and have_gpu:
                continue
            else:
                kind = "cpu"
        if kind == "cpu" and have_cpu:
            continue
        if kind == "gpu" and have_gpu:
            continue
        if kind == "cpu":
            p.set("cpu", name, conf, raw, approx=conf < 0.7)
            have_cpu = True
        else:
            meta = {"defaulted": defaulted}
            if defaulted:
                key = re.sub(r"\s+\d+GB$", "", name).lower()
                meta["variants"] = catalog.gpu_variants(key)
            p.set("gpu", name, conf, raw, **meta)
            have_gpu = True
        doc.claim(s, e)


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------
_RAM_SIZES = {2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256}
_RAM_CTX = re.compile(r"램|ram|메모리|memory|ddr[2345]|dram|마이크론|하이닉스|삼성|ram")


def _ram_ctx(doc: Doc, s: int, e: int, before: int = 14, after: int = 10) -> bool:
    return bool(_RAM_CTX.search(doc.window(s, e, before, after)))


def extract_ram(doc: Doc, p: Parsed) -> None:
    total = None
    sticks = None
    raw = ""
    conf = 0.9
    claimed: List[Tuple[int, int]] = []

    def take(t, sk, r, c, s, e):
        nonlocal total, sticks, raw, conf
        if total is None:
            total, sticks, raw, conf = t, sk, r, c
        claimed.append((s, e))

    # (a) 8gx2 / 8gb*2 / 8g 2개  (개당 용량 x 개수)
    for m in doc.finditer(r"(?<![\d.])(\d{1,3})\s*(?:gb|g)?\s*[x*×]\s*(\d)\s*(?:개|ea|장|짜리|pcs)?(?![\d])"):
        size, cnt = int(m.group(1)), int(m.group(2))
        if size in _RAM_SIZES and 1 <= cnt <= 4 and (_ram_ctx(doc, m.start(), m.end()) or "g" in m.group(0)):
            if size * cnt >= 4 and (_ram_ctx(doc, m.start(), m.end()) or size <= 64):
                take(size * cnt, cnt, m.group(0).strip(), 0.9, m.start(), m.end())
    for m in doc.finditer(r"(?<![\d.])(\d)\s*[x*×]\s*(\d{1,3})\s*(?:gb|g)(?![a-z0-9])"):
        cnt, size = int(m.group(1)), int(m.group(2))
        if size in _RAM_SIZES and 1 <= cnt <= 4:
            take(size * cnt, cnt, m.group(0).strip(), 0.9, m.start(), m.end())
    for m in doc.finditer(r"(?<![\d.])(\d{1,3})\s*(?:gb|g)\s*(\d)\s*(?:개|ea|장|pcs)"):
        size, cnt = int(m.group(1)), int(m.group(2))
        if size in _RAM_SIZES and 1 <= cnt <= 4 and _ram_ctx(doc, m.start(), m.end(), 14, 14):
            take(size * cnt, cnt, m.group(0).strip(), 0.9, m.start(), m.end())
    # (b) 8+8
    for m in doc.finditer(r"(?<![\d.])(\d{1,3})\s*\+\s*(\d{1,3})\s*(?:gb|g)?(?![\d])"):
        a, b = int(m.group(1)), int(m.group(2))
        if a in _RAM_SIZES and b in _RAM_SIZES and _ram_ctx(doc, m.start(), m.end()):
            take(a + b, 2, m.group(0).strip(), 0.85, m.start(), m.end())

    for s, e in claimed:
        doc.claim(s, e)
    claimed = []

    if total is None:
        # (c) 램/메모리 라벨 뒤 용량, (d) 용량 뒤 램 라벨, (e) ddr 뒤 용량
        pats = [
            r"(?:램|ram|메모리|memory)\s*[:\-/]?\s*(?:ddr[2345]\s*)?(?:\d{4}\s*(?:mhz)?\s*)?(\d{1,3})\s*(?:gb|g)?(?![a-z0-9])",
            r"(?<![\d.])(\d{1,3})\s*(?:gb|g)\s*(?:램|ram|메모리|memory|ddr[2345])",
            r"ddr[2345][\-\s]*(?:\d{4}\s*(?:mhz)?\s*)?(\d{1,3})\s*(?:gb|g)(?![a-z0-9])",
        ]
        for pat in pats:
            for m in doc.finditer(pat):
                size = int(m.group(1))
                if size in _RAM_SIZES:
                    take(size, None, m.group(0).strip(), 0.9, m.start(), m.end())
                    break
            if total is not None:
                break
        for s, e in claimed:
            doc.claim(s, e)
        # (f) 괄호 안의 (8x2) 등으로 개수 확인
        if total is not None:
            m = re.search(r"\(?\s*(\d{1,2})\s*[x*×+]\s*(\d{1,2})\s*\)?", doc.masked)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if a * b == total and b <= 4 and a in _RAM_SIZES:
                    sticks = b
                    doc.claim(m.start(), m.end())
                elif a + b == total and a == b:
                    sticks = 2
                    doc.claim(m.start(), m.end())

    # 램 제외 선언
    # 규격(DDR)
    rtype = None
    rconf = 0.95
    m = re.search(r"ddr\s*([2345])(?![0-9])", doc.text)
    if m:
        rtype = "DDR" + m.group(1)
    # 클럭
    speed = None
    m = re.search(r"ddr[2345][\-\s]*(\d{4})(?![0-9])", doc.text)
    if m and int(m.group(1)) in catalog.RAM_SPEEDS:
        speed = int(m.group(1))
        doc.claim(m.start(1), m.end(1))
    if speed is None:
        m = re.search(r"(\d{4})\s*(?:mhz|mt/s|mts)", doc.text)
        if m and int(m.group(1)) in catalog.RAM_SPEEDS:
            speed = int(m.group(1))
            doc.claim(m.start(1), m.end())
    if speed is None and (total is not None or rtype):
        # 램 라벨/용량 근처의 4자리 클럭
        for m in re.finditer(r"(?<![0-9.,])(\d{4})(?![0-9a-z])", doc.masked):
            if int(m.group(1)) in catalog.RAM_SPEEDS and _ram_ctx(doc, m.start(), m.end(), 18, 6):
                speed = int(m.group(1))
                doc.claim(m.start(), m.end())
                break
    if rtype is None and speed is not None:
        rtype = "DDR4" if speed <= 3600 else "DDR5"
        rconf = 0.7
    if total is not None:
        p.set("ram_gb", total, conf, raw)
        if sticks:
            p.set("ram_sticks", sticks, 0.8, raw)
    if rtype:
        p.set("ram_type", rtype, rconf, rtype)
    if speed:
        p.set("ram_speed", speed, 0.85, str(speed))


def bare_ram_guess(doc: Doc, p: Parsed) -> None:
    """'16g' 처럼 문맥 없는 용량이 남았고 RAM을 못 찾았으면 낮은 신뢰도로 RAM 후보로 쓴다."""
    if p.has("ram_gb") or "ram" in p.excluded:
        return
    for m in doc.finditer(r"(?<![\d.a-z])(\d{1,3})\s*(?:gb|g)(?![a-z0-9])"):
        size = int(m.group(1))
        if size in (8, 16, 32, 64):
            p.set("ram_gb", size, 0.5, m.group(0).strip(), guessed=True)
            doc.claim(m.start(), m.end())
            return


# ---------------------------------------------------------------------------
# 저장장치
# ---------------------------------------------------------------------------
_SIZE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(tb|t|gb|g)(?![a-z0-9])")
_STORAGE_KW = [
    (re.compile(r"nvme|m\.2|m2(?![0-9a-z])|pcie|gen\s*[345]"), "nvme", 3),
    (re.compile(r"sata"), "sata_ssd", 3),
    (re.compile(r"ssd"), "ssd", 1),
    (re.compile(r"hdd|하드디스크|하드(?![가-힣])"), "hdd", 2),
]
_NVME_HINT = re.compile(r"970|980|990|evo plus|pm9a1|pm981|sn[5-8]\d{2}|kc[23]\d{3}|p5 plus|p3 plus|firecuda 5\d{2}|nm[67]\d{2}")
_SATA_HINT = re.compile(r"860 evo|870 evo|mx500|bx500|ssd plus|wd green")
_STORAGE_SIZES = {120, 128, 240, 250, 256, 480, 500, 512, 960, 1000, 1024, 2000, 2048, 3000, 4000,
                  6000, 8000}


def extract_storage(doc: Doc, p: Parsed) -> None:
    items: List[dict] = []
    masked = doc.masked
    taken: List[Tuple[int, int]] = []

    def kind_near(s: int, e: int) -> Tuple[Optional[str], int]:
        window_before = masked[max(0, s - 16):s]
        window_after = masked[e:e + 16]
        best = None  # (distance, -priority, kind)
        for rx, kind, pr in _STORAGE_KW:
            for m in rx.finditer(window_before):
                dist = len(window_before) - m.end()
                cand = (dist, -pr, kind)
                if best is None or cand < best:
                    best = cand
            for m in rx.finditer(window_after):
                dist = m.start() + 2   # 라벨은 보통 용량 앞에 온다 ("ssd 256g hdd 1t")
                cand = (dist, -pr, kind)
                if best is None or cand < best:
                    best = cand
        if best is None:
            ctx = doc.window(s, e, 20, 20)
            if _NVME_HINT.search(ctx):
                return "nvme", 0
            if _SATA_HINT.search(ctx):
                return "sata_ssd", 0
            return None, 0
        kind = best[2]
        if kind == "ssd":
            ctx = masked[max(0, s - 16):e + 16]
            if re.search(r"nvme|m\.2|pcie", ctx):
                kind = "nvme"
            elif re.search(r"sata", ctx):
                kind = "sata_ssd"
            elif _NVME_HINT.search(doc.window(s, e, 20, 20)):
                kind = "nvme"
            elif _SATA_HINT.search(doc.window(s, e, 20, 20)):
                kind = "sata_ssd"
        return kind, 1

    for m in _SIZE_RE.finditer(masked):
        val = float(m.group(1))
        unit = m.group(2)
        gb = val * 1000 if unit in ("tb", "t") else val
        gb = int(round(gb))
        if gb < 100 or gb > 16000:
            continue
        kind, _ = kind_near(m.start(), m.end())
        if kind is None:
            if unit in ("tb", "t") and not items:
                kind, conf = "ssd", 0.4
            elif unit in ("gb", "g") and gb in _STORAGE_SIZES and gb < 1000:
                kind, conf = "ssd", 0.5     # '500G' 처럼 용량만 있고 종류가 없는 경우
            else:
                continue
        else:
            conf = 0.9
        items.append({"kind": kind, "gb": gb, "conf": conf, "raw": m.group(0).strip()})
        taken.append((m.start(), m.end()))

    # 'ssd 500', 'hdd 2' 처럼 단위 없는 숫자
    for m in re.finditer(r"(nvme|m\.2|ssd|sata|hdd|하드)\s*[:\-]?\s*(\d{3,4})(?![\d.a-z])", masked):
        gb = int(m.group(2))
        if gb in _STORAGE_SIZES and not any(s <= m.start(2) < e for s, e in taken):
            kw = m.group(1)
            kind = {"nvme": "nvme", "m.2": "nvme", "ssd": "ssd", "sata": "sata_ssd"}.get(kw, "hdd")
            if kind == "ssd" and re.search(r"nvme|m\.2", masked[max(0, m.start() - 10):m.end() + 10]):
                kind = "nvme"
            elif kind == "ssd" and re.search(r"sata", masked[max(0, m.start() - 10):m.end() + 10]):
                kind = "sata_ssd"
            items.append({"kind": kind, "gb": gb, "conf": 0.8, "raw": m.group(0).strip()})
            taken.append((m.start(), m.end()))
    for m in re.finditer(r"(hdd|하드)\s*[:\-]?\s*(\d)\s*(?:tb|t)?(?![\d.a-z])", masked):
        if not any(s <= m.start(2) < e for s, e in taken) and m.group(0).strip().endswith(("tb", "t")):
            items.append({"kind": "hdd", "gb": int(m.group(2)) * 1000, "conf": 0.8, "raw": m.group(0).strip()})
            taken.append((m.start(), m.end()))

    # 중복 제거
    seen = set()
    uniq = []
    for it in items:
        k = (it["kind"], it["gb"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    for s, e in taken:
        doc.claim(s, e)
    if uniq:
        conf = min(it["conf"] for it in uniq)
        p.set("storage", uniq, conf, ", ".join(i["raw"] for i in uniq))


# ---------------------------------------------------------------------------
# 파워
# ---------------------------------------------------------------------------
_PSU_CTX = re.compile(r"파워|psu|power|서플라이|정격|80\s*\+|80\s*plus|80플러스|atx\s*3")
_GRADE_MAP = {
    "white": "White", "화이트": "White", "bronze": "Bronze", "브론즈": "Bronze",
    "silver": "Silver", "실버": "Silver", "gold": "Gold", "골드": "Gold",
    "platinum": "Platinum", "플래티넘": "Platinum", "플래티늄": "Platinum",
    "titanium": "Titanium", "티타늄": "Titanium",
}
_GRADE_WORDS = "|".join(sorted(_GRADE_MAP, key=len, reverse=True))


def extract_psu(doc: Doc, p: Parsed) -> None:
    masked = doc.masked
    text = doc.text
    # 파워 관련 문장 위치
    ctx_spots = [m.start() for m in _PSU_CTX.finditer(text)]

    def near_psu(pos: int, span: int = 24) -> bool:
        return any(abs(pos - c) <= span for c in ctx_spots)

    # 와트
    watt = None
    wconf = 0.0
    wraw = ""
    cands = []
    for m in re.finditer(r"(?<![\d.])(\d{3,4})\s*(?:w|와트)(?![a-z])", masked):
        n = int(m.group(1))
        if 350 <= n <= 2000:
            cands.append((n, m.start(), m.end(), m.group(0).strip()))
    if cands:
        near = [c for c in cands if near_psu(c[1])]
        pick = near[0] if near else cands[0]
        watt, wraw = pick[0], pick[3]
        wconf = 0.95 if near else 0.7
        doc.claim(pick[1], pick[2])

    if watt is None:
        m = re.search(r"(?:파워|psu|power|서플라이)\s*[:\-]?\s*(\d{3,4})(?![\d.]|\s*(?:gb|g\b|tb|만|원|k\b|hz))", masked)
        if m and 350 <= int(m.group(1)) <= 2000 and int(m.group(1)) not in catalog.NV_NUMBERS_ALL:
            watt, wraw, wconf = int(m.group(1)), m.group(0).strip(), 0.8
            doc.claim(m.start(1), m.end(1))

    # 브랜드
    brand = None
    brand_tier = None
    bpos = None
    for rx, name, tier, needs_ctx in catalog.PSU_BRANDS:
        for m in re.finditer(rx, text):
            if needs_ctx and not near_psu(m.start(), 28):
                continue
            brand, brand_tier, bpos = name, tier, m.start()
            break
        if brand:
            break

    # 모델 번호에서 와트 추정 (예: 'rm750x', 'gx-850')
    if watt is None:
        for m in re.finditer(r"(?:rm[xie]?|gx|gm|gt|gf|focus|mwe|mpe|px|vertex|cv|tx|hx|ax|classic|클래식\s*2?|맥스엘리트|프로)\s*[\-]?\s*(\d{3,4})(?![\d])", text):
            n = int(m.group(1))
            if 400 <= n <= 1600 and (near_psu(m.start(), 30) or brand):
                watt, wraw, wconf = n, m.group(0).strip(), 0.75
                break
    if watt is None and brand and bpos is not None:
        m = re.search(r"(\d{3,4})(?![\d])", text[bpos:bpos + 30])
        if m and 400 <= int(m.group(1)) <= 1600 and int(m.group(1)) not in catalog.NV_NUMBERS_ALL:
            watt, wraw, wconf = int(m.group(1)), m.group(0), 0.6

    # 등급
    grade = None
    m = re.search(r"80\s*(?:\+|plus|플러스)\s*(%s)?" % _GRADE_WORDS, text)
    if m:
        grade = _GRADE_MAP.get(m.group(1) or "", "White")
        if not m.group(1):
            # '80+ 골드' 처럼 뒤쪽 공백 뒤에 올 수도 있음
            m2 = re.match(r"\s*(%s)" % _GRADE_WORDS, text[m.end():])
            if m2:
                grade = _GRADE_MAP[m2.group(1)]
    if grade is None:
        for m in re.finditer(r"(%s)(?:급|\s*등급)?" % _GRADE_WORDS, text):
            if near_psu(m.start(), 20) or (watt and abs(m.start() - (text.find(wraw) if wraw else -999)) <= 14):
                grade = _GRADE_MAP[m.group(1)]
                break

    if watt:
        p.set("psu_watt", watt, wconf, wraw)
    if grade:
        p.set("psu_grade", grade, 0.9, grade)
    if brand:
        p.set("psu_brand", brand, 0.85, brand, tier=brand_tier)


# ---------------------------------------------------------------------------
# 메인보드
# ---------------------------------------------------------------------------
_BOARD_RE = re.compile(r"(?<![a-z0-9])([abhxz]\d{2,3})(e?)(?![0-9])")
_BOARD_MODEL_HINTS = re.compile(
    r"박격포|mortar|토마호크|tomahawk|프로\s*카본|pro\s*carbon|strix|스트릭스|tuf|프라임|prime|aorus|어로스|"
    r"elite|steel\s*legend|스틸\s*레전드|pro\s*rs|taichi|gaming\s*plus|pro-?a|pro-?p|wifi|와이파이|"
    r"아수스|에이수스|asus|기가바이트|gigabyte|msi|엠에스아이|애즈락|asrock|바이오스타|biostar")


def extract_board(doc: Doc, p: Parsed) -> None:
    for m in doc.finditer(_BOARD_RE):
        chip = (m.group(1) + m.group(2)).upper()
        base = m.group(1).upper()
        name = None
        if chip in catalog.CHIPSETS:
            name = chip
        elif base in catalog.CHIPSETS:
            name = base
        if not name:
            continue
        s = m.start()
        e = min(len(doc.text), m.end() + 22)
        snippet = re.split(r"[\n,/|]", doc.text[s:e])[0].strip()
        p.set("board", name, 0.95, m.group(0).strip(), model=snippet)
        doc.claim(m.start(), m.end())
        # DDR 규격이 보드 줄에 적혀 있으면 힌트로 저장
        return


# ---------------------------------------------------------------------------
# 케이스 / 쿨러
# ---------------------------------------------------------------------------
_CASE_BRANDS = (r"다크플래쉬|darkflash|다크플래시|3rsys|쓰리알시스|마이크로닉스|앱코|abko|nzxt|lian\s*li|리안리|프랙탈|fractal|"
                r"쿨러마스터|cooler\s*master|커세어|corsair|thermaltake|써멀테이크|하이엔드|잘만|zalman|황제|시소닉")


def extract_case(doc: Doc, p: Parsed) -> None:
    if "case" in p.excluded:
        return
    text = doc.text
    m = re.search(r"케이스\s*[:\-]?\s*([^\n,/|+]{2,24})", text)
    if m and not re.match(r"\s*(제외|별도|없|미포함|빼고)", m.group(1)):
        p.set("case", m.group(1).strip(), 0.8, m.group(0).strip())
        return
    m = re.search(r"([^\s,/|+]{2,14})\s*케이스", text)
    if m:
        p.set("case", m.group(1).strip() + " 케이스", 0.7, m.group(0).strip())
        return
    m = re.search(r"((?:%s)[^\n,/|+]{0,12}?(?:케이스|case|타워))" % _CASE_BRANDS, text)
    if m:
        p.set("case", m.group(1).strip(), 0.7, m.group(1))


def extract_cooler(doc: Doc, p: Parsed) -> None:
    if "cooler" in p.excluded:
        return
    text = doc.text
    m = re.search(r"(정품\s*쿨러|기본\s*쿨러|번들\s*쿨러|스톡\s*쿨러|박스\s*쿨러|wraith|레이스\s*쿨러)", text)
    stock = m
    m = re.search(r"(수랭|aio|일체형|(?<![\d.])(?:240|280|360|420)\s*mm)", text)
    if m and not re.search(r"수랭\s*(?:제외|없)", text):
        size = None
        ms = re.search(r"(?<![\d.])(120|240|280|360|420)\s*(?:mm|수랭|aio|라지|일체)", text) or \
            re.search(r"(?:수랭|aio)\s*(120|240|280|360|420)", text)
        if ms:
            size = int(ms.group(1))
        p.set("cooler", {"kind": "aio", "size": size, "name": m.group(0)}, 0.85, m.group(0))
        return
    m = re.search(r"(공랭|타워\s*쿨러|듀얼\s*타워|ak\d{3}|pa120|peerless\s*assassin|녹투아|noctua|nh-?[du]\d{2}|"
                  r"써모랩|thermalright|하이퍼\s*212|hyper\s*212|아이스\s*쿨러|fuma|dh\d{2})", text)
    if m:
        p.set("cooler", {"kind": "air", "size": None, "name": m.group(0)}, 0.8, m.group(0))
        return
    if stock:
        p.set("cooler", {"kind": "stock", "size": None, "name": stock.group(0)}, 0.8, stock.group(0))
        return
    m = re.search(r"쿨러\s*[:\-]?\s*([^\n,/|+]{2,20})", text)
    if m and not re.match(r"\s*(소음|소리|제외|별도|없|미포함|빼고|팬)", m.group(1)):
        p.set("cooler", {"kind": "air", "size": None, "name": m.group(1).strip()}, 0.5, m.group(0))


# ---------------------------------------------------------------------------
# 모니터 / 주변기기 / 본체만
# ---------------------------------------------------------------------------
def extract_monitor(doc: Doc, p: Parsed) -> None:
    text = doc.text
    body_only = bool(re.search(r"본체\s*만|본체\s*단독|본체\s*only|본체만", text))
    if body_only:
        p.set("body_only", True, 0.9, "본체만")
    excl = re.search(r"모니터\s*(?:별도|제외|미포함|불포함|없|따로|는\s*별도)|(?:별도|제외)\s*모니터", text)
    incl = re.search(
        r"모니터\s*(?:포함|같이|세트|일괄|증정|서비스|드려요|드림|드립니다|까지|도\s*드)|"
        r"(?:포함|일괄|세트|풀세트)\s*모니터|모니터\s*[\d]{2}\s*(?:인치|inch)|"
        r"(?:\d{2})\s*(?:인치|inch)\s*(?:\w+\s*)?모니터\s*(?:포함|같이|세트|일괄)|풀\s*세트|풀세트", text)
    if excl and not incl:
        p.set("monitor", {"included": False, "inch": None, "hz": None, "res": None}, 0.85, excl.group(0))
        return
    if body_only and not incl:
        p.set("monitor", {"included": False, "inch": None, "hz": None, "res": None}, 0.8, "본체만")
        return
    if incl:
        ctx_start = max(0, incl.start() - 30)
        ctx = text[ctx_start:incl.end() + 40]
        inch = None
        hz = None
        res = None
        m = re.search(r"(\d{2})\s*(?:인치|inch|\")", ctx)
        if m and 15 <= int(m.group(1)) <= 49:
            inch = int(m.group(1))
        m = re.search(r"(\d{2,3})\s*hz", ctx)
        if m:
            hz = int(m.group(1))
        if re.search(r"4k|uhd|2160", ctx):
            res = "4k"
        elif re.search(r"qhd|wqhd|1440|2k", ctx):
            res = "qhd"
        elif re.search(r"fhd|풀hd|1080", ctx):
            res = "fhd"
        p.set("monitor", {"included": True, "inch": inch, "hz": hz, "res": res}, 0.85, incl.group(0))


def extract_peripherals(doc: Doc, p: Parsed) -> None:
    text = doc.text
    items = []
    for word, label in (("키보드", "키보드"), ("마우스", "마우스"), ("스피커", "스피커"),
                        ("웹캠", "웹캠"), ("헤드셋", "헤드셋")):
        for m in re.finditer(word, text):
            ctx = text[max(0, m.start() - 10):m.end() + 12]
            if re.search(r"포함|증정|드림|드려요|같이|세트|일괄|서비스|덤", ctx) and \
                    not re.search(r"제외|별도|미포함|불포함|없", ctx):
                items.append(label)
                break
    if re.search(r"키\s*마\s*(?:포함|증정|드림|세트)|키마\s*(?:포함|증정|드림)", text) and "키보드" not in items:
        items += ["키보드", "마우스"]
    if items:
        p.set("peripherals", sorted(set(items)), 0.8, ",".join(items))


# ---------------------------------------------------------------------------
# 제외 선언 (그래픽카드 제외, 램 제외 ...)
# ---------------------------------------------------------------------------
_EXCL_PARTS = {
    "gpu": r"그래픽(?:\s*카드)?|vga|gpu|외장",
    "ram": r"램|메모리|ram",
    "ssd": r"ssd|하드|저장장치|nvme",
    "psu": r"파워|psu",
    "cooler": r"쿨러",
    "case": r"케이스",
}


def extract_exclusions(doc: Doc, p: Parsed) -> None:
    text = doc.text
    for part, words in _EXCL_PARTS.items():
        if re.search(r"(?:%s)\s*(?:은|는|만)?\s*(?:제외|빼고|별도\s*판매|미포함|불포함|없이|없음|따로\s*판매)" % words, text) or \
                re.search(r"(?:제외|빼고)\s*(?:한\s*)?(?:%s)" % words, text):
            if part == "gpu" and re.search(r"그래픽\s*(?:이?\s*)?(?:있|포함)", text):
                continue
            p.excluded.add(part)
