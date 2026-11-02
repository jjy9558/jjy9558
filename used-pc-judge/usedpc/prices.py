"""중고 부품 가격표 (prices/parts_used_kr.json) 로드/조회/수정.

가격표는 사용자가 거래완료 가격을 보고 직접 관리한다. 제공되는 값은 임시 추정치다.
"""

import copy
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import catalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "prices" / "parts_used_kr.json"
ENV_VAR = "USEDPC_PRICES"

WARNING_TEXT = ("!!! 가격표는 대충 잡은 임시 추정치(ROUGH PLACEHOLDER)입니다. 실제 시세가 아닙니다. "
                "거래완료 가격을 보고 직접 갱신하세요 (python -m usedpc prices set ...) !!!")

DEFAULT_ADJUST = {
    "assembled_factor": 0.93, "brand_pc_factor": 0.80,
    "age_start_years": 3, "age_per_year": 0.03, "age_cap": 0.15,
    "warranty_per_month": 0.004, "warranty_cap": 0.06, "receipt_bonus": 0.01,
    "mining_gpu_discount": 0.12, "refurb_discount": 0.06, "no_box_discount": 0.02,
    "unstable_discount": 0.25, "defect_discount": 0.45,
    "psu_unknown_flat": 50000, "cooler_noise_flat": 20000,
    "monitor_resale_factor": 0.9, "scam_ratio": 0.65,
    "spread_base": 0.08, "spread_per_assumption": 0.03, "spread_cap": 0.30,
    "round_unit": 5000, "stale_days_warn": 45, "upgrade_resale_factor": 0.93,
}


class PriceError(Exception):
    pass


def default_path() -> Path:
    env = os.environ.get(ENV_VAR)
    return Path(env) if env else DEFAULT_PATH


class PriceTable:
    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None):
        self.data = data
        self.path = path

    # ----- 로드/저장 -----
    @classmethod
    def load(cls, path=None) -> "PriceTable":
        p = Path(path) if path else default_path()
        if not p.exists():
            raise PriceError("가격표 파일이 없습니다: {}".format(p))
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError as e:
            raise PriceError("가격표 JSON 형식 오류 ({}): {}".format(p, e))
        return cls(data, p)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriceTable":
        return cls(copy.deepcopy(data), None)

    def save(self, path=None) -> Path:
        p = Path(path) if path else self.path
        if p is None:
            raise PriceError("저장할 경로가 없습니다")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        return p

    # ----- 메타 -----
    @property
    def as_of(self) -> Optional[date]:
        s = self.data.get("as_of")
        try:
            y, m, d = (int(x) for x in str(s).split("-"))
            return date(y, m, d)
        except Exception:
            return None

    @property
    def placeholder(self) -> bool:
        return bool(self.data.get("placeholder", False))

    def adj(self, key: str) -> float:
        v = self.data.get("adjust", {}).get(key)
        return DEFAULT_ADJUST[key] if v is None else v

    def age_days(self, today: date) -> Optional[int]:
        a = self.as_of
        return (today - a).days if a else None

    def warnings(self, today: date) -> list:
        out = []
        if self.placeholder:
            out.append(WARNING_TEXT)
        a = self.as_of
        if a is None:
            out.append("가격표에 as_of(기준일)이 없습니다.")
        else:
            d = (today - a).days
            if d > self.adj("stale_days_warn"):
                out.append("가격표 기준일이 {}일 전({})입니다. 최근 거래완료 가격으로 갱신하세요.".format(d, a.isoformat()))
        return out

    # ----- 조회 -----
    def _section(self, name: str) -> Dict[str, Any]:
        return self.data.get(name, {})

    def cpu(self, name: str) -> Tuple[Optional[int], bool]:
        """-> (가격, 근사 여부). 카탈로그에 없는 모델은 같은 패밀리의 가장 가까운 모델 가격으로 근사."""
        sec = self._section("cpu")
        if name in sec:
            return sec[name], False
        near = catalog.nearest_cpu(name) if name else None
        if near and near in sec:
            return sec[near], True
        return None, False

    def gpu(self, name: str) -> Tuple[Optional[int], bool]:
        sec = self._section("gpu")
        if name in sec:
            return sec[name], False
        return None, False

    def board(self, chipset: str) -> Optional[int]:
        sec = self._section("board")
        return sec.get(chipset.upper())

    def ram(self, gb: int, ddr: Optional[str]) -> Tuple[int, bool]:
        """-> (가격, 규격 가정 여부)"""
        per = self._section("ram_per_gb")
        assumed = False
        if ddr not in per:
            ddr = "DDR4"
            assumed = True
        return int(gb * per.get(ddr, 2500)), assumed

    def storage(self, kind: str, gb: int) -> int:
        if kind == "hdd":
            return max(int(self.data.get("hdd_min", 12000)),
                       int(gb / 1000.0 * self.data.get("hdd_per_tb", 20000)))
        per = self._section("ssd_per_gb")
        rate = per.get(kind, per.get("ssd", 60))
        return max(int(self.data.get("ssd_min", 15000)), int(gb * rate))

    def psu(self, watt: int, grade: Optional[str] = None, tier: Optional[str] = None,
            noname: bool = False) -> int:
        table = {int(k): v for k, v in self._section("psu_by_watt").items()}
        if not table:
            return 40000
        keys = sorted(table)
        base = None
        for k in keys:
            if watt <= k:
                base = table[k]
                break
        if base is None:
            base = table[keys[-1]]
        gm = self._section("psu_grade_mult")
        grade_mult = gm.get(grade, gm.get("default", 0.9)) if grade else gm.get("default", 0.9)
        bm = self._section("psu_brand_mult")
        if noname:
            brand_mult = bm.get("noname", 0.6)
        elif tier:
            brand_mult = bm.get(tier, 1.0)
        else:
            brand_mult = bm.get("unknown", 0.8)
        return int(base * grade_mult * brand_mult)

    def case(self, name: Optional[str] = None) -> int:
        sec = self._section("case")
        if name and re.search(r"프랙탈|fractal|리안리|lian|nzxt|hyte|커세어|corsair", name.lower()):
            return sec.get("premium", sec.get("default", 30000))
        return sec.get("default", 30000)

    def cooler(self, info: Optional[dict] = None) -> int:
        sec = self._section("cooler")
        if not info:
            return sec.get("default", 15000)
        kind = info.get("kind")
        if kind == "aio":
            size = info.get("size")
            return sec.get("aio_%s" % size, sec.get("aio", 55000))
        if kind == "air":
            return sec.get("air", 25000)
        if kind == "stock":
            return sec.get("stock", 0)
        return sec.get("default", 15000)

    def monitor(self, info: Optional[dict] = None) -> int:
        sec = self._section("monitor")
        base = sec.get("default", 50000)
        inch = (info or {}).get("inch")
        if inch:
            by = {int(k): v for k, v in sec.get("by_inch", {}).items()}
            if by:
                nearest = min(by, key=lambda k: abs(k - inch))
                base = by[nearest]
        hz = (info or {}).get("hz")
        if hz:
            hm = {int(k): v for k, v in sec.get("hz_mult", {}).items()}
            mult = 1.0
            for k in sorted(hm):
                if hz >= k:
                    mult = hm[k]
            base *= mult
        res = (info or {}).get("res")
        if res:
            base *= sec.get("res_mult", {}).get(res, 1.0)
        return int(base)

    def peripherals(self, items) -> int:
        sec = self._section("peripherals")
        return int(sum(sec.get(i, 8000) for i in items))

    # ----- 신품가 -----
    def new_price(self, category: str, name: str, used_price: Optional[int]) -> Optional[int]:
        sec = self.data.get("new", {}).get(category, {})
        if name in sec:
            return sec[name]
        if used_price is None:
            return None
        ratio = self.data.get("new_ratio_default", 0.7) or 0.7
        return int(used_price / ratio)

    # ----- 수정 -----
    def resolve(self, part: str) -> Tuple[str, str]:
        """사용자가 입력한 이름 -> (섹션, 키). 못 찾으면 PriceError."""
        part = part.strip()
        # 점 표기: ram_per_gb.DDR4, psu_by_watt.750, adjust.assembled_factor
        if "." in part:
            head, _, tail = part.partition(".")
            if head in self.data and isinstance(self.data[head], dict):
                return head, tail
        low = part.lower()
        for sec in ("gpu", "cpu", "board"):
            for k in self.data.get(sec, {}):
                if k.lower() == low:
                    return sec, k
        from .parser import parse_listing
        p = parse_listing(part)
        for field, sec in (("gpu", "gpu"), ("cpu", "cpu"), ("board", "board")):
            if p.has(field) and p.val(field) in self.data.get(sec, {}):
                return sec, p.val(field)
        # 칩셋 단독 입력
        up = part.upper()
        if up in self.data.get("board", {}):
            return "board", up
        raise PriceError("알 수 없는 부품: '{}'  (예: \"RTX 4070 Super\", \"i5-12400F\", B660, ram_per_gb.DDR4)".format(part))

    def set_price(self, part: str, value: int, new: bool = False) -> Tuple[str, str, Any]:
        sec, key = self.resolve(part)
        if new:
            if sec not in ("gpu", "cpu"):
                raise PriceError("신품가는 gpu/cpu 만 지원합니다")
            tgt = self.data.setdefault("new", {}).setdefault(sec, {})
            old = tgt.get(key)
            tgt[key] = int(value)
            return "new." + sec, key, old
        target = self.data.setdefault(sec, {})
        old = target.get(key)
        if isinstance(old, dict):
            raise PriceError("'{}' 는 세부 항목이 있는 섹션입니다. 예: {}.<항목>".format(sec, sec))
        target[key] = value if sec == "adjust" else int(value)
        return sec, key, old

    def stamp(self, today: date, placeholder: Optional[bool] = False) -> None:
        self.data["as_of"] = today.isoformat()
        if placeholder is not None:
            self.data["placeholder"] = placeholder


def load_table(path=None) -> PriceTable:
    return PriceTable.load(path)
