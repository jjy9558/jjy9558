"""테스트 공용 도구: 결정적인 가격표와 파싱 단축 함수."""

import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from usedpc.parser import parse_listing  # noqa: E402
from usedpc.prices import DEFAULT_ADJUST, PriceTable  # noqa: E402

TODAY = "2026-11-02"


def make_table(**overrides) -> PriceTable:
    """배포용 가격표를 바탕으로 하되, 판정 수학 테스트에 쓰는 값은 고정해 둔다.

    사용자가 prices/parts_used_kr.json 을 고쳐도 테스트가 깨지지 않게 하기 위함이다.
    """
    base = PriceTable.load().data
    data = copy.deepcopy(base)
    data["as_of"] = "2026-10-20"       # TODAY 와 13일 차이 (오래됨 경고 없음)
    data["placeholder"] = False
    data["adjust"] = dict(DEFAULT_ADJUST)
    # 고정 가격 (원)
    data["cpu"].update({"i5-12400F": 100000, "i5-13400F": 160000, "Ryzen 5 5600": 90000,
                        "Ryzen 7 7800X3D": 380000, "i7-8700K": 90000, "i9-14900K": 520000})
    data["gpu"].update({"RTX 3060 12GB": 200000, "RTX 3060 Ti": 280000, "RTX 4070 Super": 680000,
                        "RTX 4090": 2600000, "GTX 1060 6GB": 50000, "RX 6600": 160000,
                        "RTX 3070": 330000, "RX 6800 XT": 420000, "RTX 3090": 900000})
    data["board"].update({"B660": 100000, "B550": 90000, "B650": 140000, "Z370": 50000})
    data["ram_per_gb"] = {"DDR3": 700, "DDR4": 2500, "DDR5": 5000}
    data["ssd_per_gb"] = {"nvme": 80, "sata_ssd": 60, "ssd": 60}
    data["ssd_min"] = 15000
    data["hdd_per_tb"] = 20000
    data["hdd_min"] = 12000
    data["psu_by_watt"] = {"400": 15000, "500": 22000, "650": 40000, "750": 55000, "850": 75000, "1000": 105000}
    data["psu_grade_mult"] = {"White": 0.85, "Bronze": 1.0, "Gold": 1.25, "default": 0.9}
    data["psu_brand_mult"] = {"A": 1.25, "B": 1.0, "unknown": 0.8, "noname": 0.6}
    data["case"] = {"default": 30000, "premium": 80000}
    data["cooler"] = {"stock": 0, "air": 25000, "aio_240": 50000, "aio_360": 80000, "aio": 55000, "default": 15000}
    data["monitor"] = {"default": 50000, "by_inch": {"24": 40000, "27": 70000},
                       "hz_mult": {"144": 1.4}, "res_mult": {"fhd": 1.0, "qhd": 1.4}}
    data["peripherals"] = {"키보드": 10000, "마우스": 8000}
    data["cpu"]["i5-12400"] = 110000
    for k, v in overrides.items():
        data["adjust"][k] = v
    return PriceTable.from_dict(data)


def parse(text: str, **kw):
    return parse_listing(text, today=kw.pop("today", TODAY), **kw)


class Base(unittest.TestCase):
    maxDiff = None
