"""부품 카탈로그: 정식 이름, 소켓/VRAM/전력, 대략적인 성능 지수.

성능 지수는 "대략적인 상대 지수"일 뿐이다 (정확한 벤치마크 아님).
 - GPU score: RTX 4070 = 100 기준의 래스터 성능 근사치
 - CPU game: Ryzen 7 7800X3D = 100 기준의 게임 성능 근사치
 - CPU mt: Ryzen 7 5800X = 100 기준의 멀티스레드 근사치
가격은 여기에 없다. 가격은 prices/parts_used_kr.json 에서 사용자가 관리한다.
"""

import re
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# GPU
# (정식 이름, 벤더, VRAM GB, 성능지수, 전력 W, PCIe 레인 수, 권장 파워 W)
# ---------------------------------------------------------------------------
_GPU_ROWS = [
    ("GTX 1050 Ti", "nvidia", 4, 20, 75, 16, 300),
    ("GTX 1060 6GB", "nvidia", 6, 30, 120, 16, 400),
    ("GTX 1060 3GB", "nvidia", 3, 26, 120, 16, 400),
    ("GTX 1070", "nvidia", 8, 43, 150, 16, 500),
    ("GTX 1080", "nvidia", 8, 56, 180, 16, 500),
    ("GTX 1080 Ti", "nvidia", 11, 72, 250, 16, 600),
    ("GTX 1650", "nvidia", 4, 28, 75, 16, 300),
    ("GTX 1650 Super", "nvidia", 4, 36, 100, 16, 350),
    ("GTX 1660", "nvidia", 6, 38, 120, 16, 450),
    ("GTX 1660 Super", "nvidia", 6, 42, 125, 16, 450),
    ("GTX 1660 Ti", "nvidia", 6, 43, 120, 16, 450),
    ("RTX 2060", "nvidia", 6, 50, 160, 16, 500),
    ("RTX 2060 Super", "nvidia", 8, 58, 175, 16, 550),
    ("RTX 2070", "nvidia", 8, 63, 175, 16, 550),
    ("RTX 2070 Super", "nvidia", 8, 71, 215, 16, 650),
    ("RTX 2080", "nvidia", 8, 77, 225, 16, 650),
    ("RTX 2080 Super", "nvidia", 8, 80, 250, 16, 650),
    ("RTX 2080 Ti", "nvidia", 11, 93, 250, 16, 650),
    ("RTX 3050 8GB", "nvidia", 8, 48, 130, 16, 550),
    ("RTX 3050 6GB", "nvidia", 6, 38, 70, 8, 300),
    ("RTX 3060 12GB", "nvidia", 12, 62, 170, 16, 550),
    ("RTX 3060 8GB", "nvidia", 8, 58, 170, 16, 550),
    ("RTX 3060 Ti", "nvidia", 8, 77, 200, 16, 600),
    ("RTX 3070", "nvidia", 8, 88, 220, 16, 650),
    ("RTX 3070 Ti", "nvidia", 8, 96, 290, 16, 750),
    ("RTX 3080 10GB", "nvidia", 10, 112, 320, 16, 750),
    ("RTX 3080 12GB", "nvidia", 12, 118, 350, 16, 750),
    ("RTX 3080 Ti", "nvidia", 12, 125, 350, 16, 750),
    ("RTX 3090", "nvidia", 24, 132, 350, 16, 750),
    ("RTX 3090 Ti", "nvidia", 24, 142, 450, 16, 850),
    ("RTX 4060", "nvidia", 8, 74, 115, 8, 550),
    ("RTX 4060 Ti 8GB", "nvidia", 8, 83, 160, 8, 550),
    ("RTX 4060 Ti 16GB", "nvidia", 16, 84, 165, 8, 550),
    ("RTX 4070", "nvidia", 12, 100, 200, 16, 650),
    ("RTX 4070 Super", "nvidia", 12, 118, 220, 16, 650),
    ("RTX 4070 Ti", "nvidia", 12, 130, 285, 16, 700),
    ("RTX 4070 Ti Super", "nvidia", 16, 143, 285, 16, 750),
    ("RTX 4080", "nvidia", 16, 160, 320, 16, 750),
    ("RTX 4080 Super", "nvidia", 16, 166, 320, 16, 750),
    ("RTX 4090", "nvidia", 24, 215, 450, 16, 850),
    ("RTX 5050", "nvidia", 8, 62, 130, 8, 550),
    ("RTX 5060", "nvidia", 8, 82, 145, 8, 550),
    ("RTX 5060 Ti 8GB", "nvidia", 8, 94, 180, 8, 600),
    ("RTX 5060 Ti 16GB", "nvidia", 16, 95, 180, 8, 600),
    ("RTX 5070", "nvidia", 12, 120, 250, 16, 650),
    ("RTX 5070 Ti", "nvidia", 16, 150, 300, 16, 750),
    ("RTX 5080", "nvidia", 16, 175, 360, 16, 850),
    ("RTX 5090", "nvidia", 32, 260, 575, 16, 1000),
    ("RX 580 8GB", "amd", 8, 28, 185, 16, 500),
    ("RX 5700 XT", "amd", 8, 62, 225, 16, 600),
    ("RX 6500 XT", "amd", 4, 33, 107, 4, 400),
    ("RX 6600", "amd", 8, 60, 132, 8, 450),
    ("RX 6600 XT", "amd", 8, 68, 160, 8, 500),
    ("RX 6650 XT", "amd", 8, 72, 180, 8, 500),
    ("RX 6700 XT", "amd", 12, 87, 230, 16, 650),
    ("RX 6750 XT", "amd", 12, 92, 250, 16, 650),
    ("RX 6800", "amd", 16, 105, 250, 16, 600),
    ("RX 6800 XT", "amd", 16, 118, 300, 16, 750),
    ("RX 6900 XT", "amd", 16, 128, 300, 16, 850),
    ("RX 7600", "amd", 8, 71, 165, 8, 550),
    ("RX 7600 XT", "amd", 16, 78, 190, 8, 600),
    ("RX 7700 XT", "amd", 12, 100, 245, 16, 700),
    ("RX 7800 XT", "amd", 16, 119, 263, 16, 700),
    ("RX 7900 GRE", "amd", 16, 134, 260, 16, 750),
    ("RX 7900 XT", "amd", 20, 145, 315, 16, 800),
    ("RX 7900 XTX", "amd", 24, 170, 355, 16, 800),
    ("RX 9060 XT 8GB", "amd", 8, 92, 150, 16, 550),
    ("RX 9060 XT 16GB", "amd", 16, 93, 160, 16, 550),
    ("RX 9070", "amd", 16, 135, 220, 16, 650),
    ("RX 9070 XT", "amd", 16, 152, 304, 16, 750),
    ("Arc B580", "intel", 12, 80, 190, 8, 600),
    ("Arc A770 16GB", "intel", 16, 68, 225, 16, 600),
]

GPUS: Dict[str, dict] = {}
for _n, _v, _vram, _s, _tdp, _lanes, _psu in _GPU_ROWS:
    GPUS[_n] = {"vendor": _v, "vram": _vram, "score": _s, "tdp": _tdp,
                "lanes": _lanes, "psu_rec": _psu}

# 모델 키(VRAM 접미사 제거) -> {vram: 정식 이름}
GPU_KEYS: Dict[str, Dict[int, str]] = {}
for _n, _d in GPUS.items():
    _key = re.sub(r"\s+\d+GB$", "", _n).lower()
    GPU_KEYS.setdefault(_key, {})[_d["vram"]] = _n

# VRAM 변형이 여러 개일 때 글에 용량이 없으면 쓰는 기본값 (가장 흔한/보수적인 쪽)
GPU_DEFAULT_VRAM = {
    "gtx 1060": 6,
    "rtx 3050": 8,
    "rtx 3060": 12,
    "rtx 3080": 10,
    "rtx 4060 ti": 8,
    "rtx 5060 ti": 8,
    "rx 9060 xt": 16,
}

# 숫자 -> 접두사 (글에 접두사 없이 숫자만 있을 때 사용)
NV_NUMBERS_BARE = {
    "1650", "1660", "2060", "2070", "2080",
    "3050", "3060", "3070", "3080", "3090",
    "4060", "4070", "4080", "4090",
    "5050", "5060", "5070", "5080", "5090",
}
NV_NUMBERS_ALL = NV_NUMBERS_BARE | {"1050", "1060", "1070", "1080", "1030"}
AMD_NUMBERS_BARE = {"6600", "6650", "6700", "6750", "6800", "6900",
                    "7600", "7700", "7800", "7900", "9060", "9070"}
AMD_NUMBERS_ALL = AMD_NUMBERS_BARE | {"580", "5500", "5600", "5700", "6400", "6500"}
# 숫자만 보면 CPU(라이젠)와 겹칠 수 있는 번호
AMBIGUOUS_CPU_GPU = {"7600", "7700", "7900", "5500", "5600", "5700"}

# ---------------------------------------------------------------------------
# CPU
# (이름, 소켓, 게임지수, 멀티지수, 피크전력 W, CPU PCIe 세대)
# 같은 세대/패밀리에서 먼저 나오는 항목이 "세대만 적혀 있을 때의 대표 모델".
# ---------------------------------------------------------------------------
_CPU_ROWS = [
    # LGA1700 (12~14세대)
    ("i3-12100F", "LGA1700", 52, 55, 60, 5.0),
    ("i3-12100", "LGA1700", 52, 55, 60, 5.0),
    ("i3-13100F", "LGA1700", 56, 58, 65, 5.0),
    ("i3-14100F", "LGA1700", 57, 58, 65, 5.0),
    ("i5-12400F", "LGA1700", 62, 80, 100, 5.0),
    ("i5-12400", "LGA1700", 62, 80, 100, 5.0),
    ("i5-12500", "LGA1700", 64, 85, 100, 5.0),
    ("i5-12600K", "LGA1700", 72, 105, 150, 5.0),
    ("i5-12600KF", "LGA1700", 72, 105, 150, 5.0),
    ("i5-13400F", "LGA1700", 70, 100, 110, 5.0),
    ("i5-13400", "LGA1700", 70, 100, 110, 5.0),
    ("i5-13500", "LGA1700", 77, 130, 130, 5.0),
    ("i5-13600K", "LGA1700", 82, 165, 180, 5.0),
    ("i5-13600KF", "LGA1700", 82, 165, 180, 5.0),
    ("i5-14400F", "LGA1700", 71, 100, 110, 5.0),
    ("i5-14400", "LGA1700", 71, 100, 110, 5.0),
    ("i5-14600K", "LGA1700", 86, 170, 180, 5.0),
    ("i5-14600KF", "LGA1700", 86, 170, 180, 5.0),
    ("i7-12700", "LGA1700", 76, 125, 130, 5.0),
    ("i7-12700F", "LGA1700", 76, 125, 130, 5.0),
    ("i7-12700K", "LGA1700", 78, 130, 180, 5.0),
    ("i7-12700KF", "LGA1700", 78, 130, 180, 5.0),
    ("i7-13700K", "LGA1700", 90, 190, 230, 5.0),
    ("i7-13700KF", "LGA1700", 90, 190, 230, 5.0),
    ("i7-13700F", "LGA1700", 86, 175, 180, 5.0),
    ("i7-14700K", "LGA1700", 93, 215, 230, 5.0),
    ("i7-14700KF", "LGA1700", 93, 215, 230, 5.0),
    ("i7-14700F", "LGA1700", 90, 200, 200, 5.0),
    ("i9-12900K", "LGA1700", 84, 155, 220, 5.0),
    ("i9-12900KF", "LGA1700", 84, 155, 220, 5.0),
    ("i9-13900K", "LGA1700", 96, 245, 280, 5.0),
    ("i9-13900KF", "LGA1700", 96, 245, 280, 5.0),
    ("i9-14900K", "LGA1700", 98, 260, 300, 5.0),
    ("i9-14900KF", "LGA1700", 98, 260, 300, 5.0),
    ("i9-14900KS", "LGA1700", 99, 265, 320, 5.0),
    # LGA1851 (Core Ultra 200S)
    ("Ultra 5 225F", "LGA1851", 76, 105, 100, 5.0),
    ("Ultra 5 245K", "LGA1851", 84, 150, 140, 5.0),
    ("Ultra 5 245KF", "LGA1851", 84, 150, 140, 5.0),
    ("Ultra 7 265K", "LGA1851", 94, 215, 190, 5.0),
    ("Ultra 7 265KF", "LGA1851", 94, 215, 190, 5.0),
    ("Ultra 9 285K", "LGA1851", 98, 290, 230, 5.0),
    # LGA1200 (10~11세대)
    ("i5-10400F", "LGA1200", 50, 62, 90, 3.0),
    ("i5-10400", "LGA1200", 50, 62, 90, 3.0),
    ("i5-10600K", "LGA1200", 56, 78, 140, 3.0),
    ("i5-10600KF", "LGA1200", 56, 78, 140, 3.0),
    ("i5-11400F", "LGA1200", 55, 70, 100, 4.0),
    ("i5-11400", "LGA1200", 55, 70, 100, 4.0),
    ("i5-11600K", "LGA1200", 60, 80, 160, 4.0),
    ("i5-11600KF", "LGA1200", 60, 80, 160, 4.0),
    ("i3-10100F", "LGA1200", 42, 45, 70, 3.0),
    ("i3-10100", "LGA1200", 42, 45, 70, 3.0),
    ("i7-10700", "LGA1200", 56, 88, 120, 3.0),
    ("i7-10700F", "LGA1200", 56, 88, 120, 3.0),
    ("i7-10700K", "LGA1200", 60, 95, 160, 3.0),
    ("i7-10700KF", "LGA1200", 60, 95, 160, 3.0),
    ("i7-11700K", "LGA1200", 64, 95, 180, 4.0),
    ("i7-11700KF", "LGA1200", 64, 95, 180, 4.0),
    ("i9-10900K", "LGA1200", 62, 115, 200, 3.0),
    ("i9-10900KF", "LGA1200", 62, 115, 200, 3.0),
    # LGA1151 (6~9세대)
    ("i5-8400", "LGA1151", 42, 52, 80, 3.0),
    ("i5-9400F", "LGA1151", 44, 54, 80, 3.0),
    ("i5-9400", "LGA1151", 44, 54, 80, 3.0),
    ("i5-9600K", "LGA1151", 47, 58, 130, 3.0),
    ("i5-9600KF", "LGA1151", 47, 58, 130, 3.0),
    ("i5-8500", "LGA1151", 43, 54, 80, 3.0),
    ("i5-7500", "LGA1151", 36, 35, 70, 3.0),
    ("i5-6500", "LGA1151", 33, 32, 70, 3.0),
    ("i7-7700", "LGA1151", 44, 45, 80, 3.0),
    ("i7-7700K", "LGA1151", 45, 45, 120, 3.0),
    ("i7-6700", "LGA1151", 38, 40, 80, 3.0),
    ("i7-8700", "LGA1151", 50, 70, 100, 3.0),
    ("i7-8700K", "LGA1151", 52, 78, 140, 3.0),
    ("i7-9700", "LGA1151", 52, 70, 100, 3.0),
    ("i7-9700K", "LGA1151", 54, 70, 140, 3.0),
    ("i7-9700KF", "LGA1151", 54, 70, 140, 3.0),
    ("i9-9900K", "LGA1151", 58, 100, 170, 3.0),
    ("i9-9900KF", "LGA1151", 58, 100, 170, 3.0),
    ("i7-4790K", "LGA1150", 36, 40, 120, 3.0),
    # AM4
    ("Ryzen 5 5600", "AM4", 63, 75, 90, 4.0),
    ("Ryzen 5 5600X", "AM4", 65, 78, 100, 4.0),
    ("Ryzen 5 5600G", "AM4", 58, 76, 90, 3.0),
    ("Ryzen 5 5600GT", "AM4", 60, 76, 90, 3.0),
    ("Ryzen 5 5500", "AM4", 55, 62, 85, 3.0),
    ("Ryzen 5 4500", "AM4", 50, 58, 80, 3.0),
    ("Ryzen 5 3600", "AM4", 50, 65, 90, 4.0),
    ("Ryzen 5 2600", "AM4", 38, 52, 90, 3.0),
    ("Ryzen 7 5700X", "AM4", 68, 97, 110, 4.0),
    ("Ryzen 7 5700X3D", "AM4", 80, 95, 110, 4.0),
    ("Ryzen 7 5700G", "AM4", 62, 92, 100, 3.0),
    ("Ryzen 7 5800X", "AM4", 70, 100, 140, 4.0),
    ("Ryzen 7 5800X3D", "AM4", 82, 98, 120, 4.0),
    ("Ryzen 7 3700X", "AM4", 54, 85, 100, 4.0),
    ("Ryzen 7 2700X", "AM4", 42, 70, 130, 3.0),
    ("Ryzen 9 5900X", "AM4", 72, 150, 170, 4.0),
    ("Ryzen 9 5950X", "AM4", 74, 195, 200, 4.0),
    # AM5
    ("Ryzen 5 7500F", "AM5", 76, 82, 100, 5.0),
    ("Ryzen 5 7600", "AM5", 78, 90, 100, 5.0),
    ("Ryzen 5 7600X", "AM5", 80, 95, 140, 5.0),
    ("Ryzen 5 8600G", "AM5", 74, 85, 100, 4.0),
    ("Ryzen 5 9600X", "AM5", 84, 100, 100, 5.0),
    ("Ryzen 7 7700", "AM5", 82, 110, 110, 5.0),
    ("Ryzen 7 7700X", "AM5", 86, 115, 150, 5.0),
    ("Ryzen 7 7800X3D", "AM5", 100, 112, 110, 5.0),
    ("Ryzen 7 8700G", "AM5", 80, 108, 110, 4.0),
    ("Ryzen 7 9700X", "AM5", 88, 120, 110, 5.0),
    ("Ryzen 7 9800X3D", "AM5", 118, 125, 130, 5.0),
    ("Ryzen 9 7900", "AM5", 82, 150, 140, 5.0),
    ("Ryzen 9 7900X", "AM5", 86, 165, 190, 5.0),
    ("Ryzen 9 7950X", "AM5", 90, 230, 250, 5.0),
    ("Ryzen 9 7950X3D", "AM5", 108, 225, 200, 5.0),
    ("Ryzen 9 9900X", "AM5", 88, 170, 190, 5.0),
    ("Ryzen 9 9950X", "AM5", 92, 245, 250, 5.0),
    ("Ryzen 9 9950X3D", "AM5", 118, 240, 220, 5.0),
]

CPUS: Dict[str, dict] = {}
CPU_ORDER: List[str] = []
for _n, _sock, _g, _mt, _pk, _pcie in _CPU_ROWS:
    CPUS[_n] = {"socket": _sock, "game": _g, "mt": _mt, "peak_w": _pk, "pcie": _pcie}
    CPU_ORDER.append(_n)

# 소켓 -> 메모리 규격
SOCKET_MEM = {
    "AM4": "DDR4", "AM5": "DDR5", "LGA1200": "DDR4", "LGA1151": "DDR4",
    "LGA1150": "DDR3", "LGA1851": "DDR5", "LGA1700": "DDR4/DDR5",
}

# ---------------------------------------------------------------------------
# 메인보드 칩셋: 이름 -> (소켓, GPU 슬롯 PCIe 세대)
# ---------------------------------------------------------------------------
CHIPSETS: Dict[str, Tuple[str, float]] = {}
for _c in ["H110", "B150", "H170", "Z170", "B250", "H270", "Z270", "H310", "B360", "B365",
           "H370", "Z370", "Z390"]:
    CHIPSETS[_c] = ("LGA1151", 3.0)
for _c in ["H410", "B460", "H470", "Z490"]:
    CHIPSETS[_c] = ("LGA1200", 3.0)
for _c in ["H510", "B560", "H570", "Z590"]:
    CHIPSETS[_c] = ("LGA1200", 4.0)
for _c in ["H610", "B660", "H670", "B760", "H770"]:
    CHIPSETS[_c] = ("LGA1700", 4.0)
for _c in ["Z690", "Z790"]:
    CHIPSETS[_c] = ("LGA1700", 5.0)
for _c in ["H810", "B860", "Z890"]:
    CHIPSETS[_c] = ("LGA1851", 5.0)
for _c in ["A320", "B350", "X370", "B450", "X470"]:
    CHIPSETS[_c] = ("AM4", 3.0)
for _c in ["A520", "B550", "X570"]:
    CHIPSETS[_c] = ("AM4", 4.0)
for _c in ["A620", "B650", "X670", "B840", "B850", "X870"]:
    CHIPSETS[_c] = ("AM5", 5.0)

# 소켓별 "기본으로 가정하는" 칩셋 (글에 보드 정보가 없을 때 가격 추정용)
SOCKET_DEFAULT_CHIPSET = {
    "LGA1700": "B660", "AM4": "B550", "AM5": "B650", "LGA1200": "B560",
    "LGA1151": "B360", "LGA1851": "B860", "LGA1150": "H81",
}


def chipset_info(chipset: str) -> Optional[Tuple[str, float]]:
    return CHIPSETS.get(chipset.upper())


# ---------------------------------------------------------------------------
# PSU 브랜드 (대략적인 등급 A/B; 목록에 없으면 "미확인")
# ---------------------------------------------------------------------------
# (정규식, 표시 이름, 등급, 다른 부품에도 쓰이는 브랜드여서 파워 문맥이 필요한가)
PSU_BRANDS = [
    (r"seasonic|시소닉|씨소닉", "Seasonic", "A", False),
    (r"corsair|커세어", "Corsair", "A", True),
    (r"super\s*flower|슈퍼\s*플라워|super\s*플라워", "Super Flower", "A", False),
    (r"be\s*quiet|비\s*콰이어트", "be quiet!", "A", False),
    (r"evga|이브이지에이", "EVGA", "A", True),
    (r"마이크로닉스|micronics", "마이크로닉스", "B", False),
    (r"쿨러\s*마스터|cooler\s*master", "쿨러마스터", "B", True),
    (r"antec|안텍", "Antec", "B", True),
    (r"fsp", "FSP", "B", False),
    (r"에너맥스|enermax", "Enermax", "B", False),
    (r"잘만|zalman", "잘만", "B", True),
    (r"딥쿨|deepcool", "DeepCool", "B", True),
    (r"thermaltake|써멀테이크", "Thermaltake", "B", True),
    (r"asus|에이수스|아수스", "ASUS", "B", True),
    (r"msi|엠에스아이", "MSI", "B", True),
    (r"gigabyte|기가바이트", "Gigabyte", "B", True),
    (r"앱코|abko", "앱코", "B", True),
    (r"다크플래쉬|darkflash|다크플래시", "darkFlash", "B", True),
    (r"3rsys|쓰리알시스", "3RSYS", "B", True),
    (r"lian\s*li|리안리", "Lian Li", "B", True),
]

# ---------------------------------------------------------------------------
# 인식 가능한 숫자 (한글 숫자 읽기 "삼공육공" -> 3060 변환 시 오탐 방지용)
# ---------------------------------------------------------------------------
RAM_SPEEDS = {2133, 2400, 2666, 3000, 3200, 3600, 4000, 4800, 5200, 5600, 6000, 6200,
              6400, 6800, 7200, 7600, 8000}


def _cpu_number_strings() -> set:
    out = set()
    for n in CPUS:
        m = re.search(r"(\d{3,5})", n)
        if m:
            out.add(m.group(1))
    return out


KNOWN_NUMBERS = (_cpu_number_strings() | NV_NUMBERS_ALL | AMD_NUMBERS_ALL
                 | {str(s) for s in RAM_SPEEDS})


# ---------------------------------------------------------------------------
# 조회 보조
# ---------------------------------------------------------------------------
def cpu_family(name: str) -> str:
    """'i5-12400F' -> 'i5', 'Ryzen 5 5600' -> 'r5', 'Ultra 5 245K' -> 'u5'."""
    m = re.match(r"i([3579])-", name)
    if m:
        return "i" + m.group(1)
    m = re.match(r"Ryzen ([3579])", name)
    if m:
        return "r" + m.group(1)
    m = re.match(r"Ultra ([579])", name)
    if m:
        return "u" + m.group(1)
    return "?"


def cpu_number(name: str) -> int:
    m = re.search(r"(\d{3,5})", name)
    return int(m.group(1)) if m else 0


def intel_gen(name: str) -> int:
    m = re.match(r"i[3579]-(\d{4,5})", name)
    if not m:
        return 0
    n = m.group(1)
    return int(n[:2]) if len(n) == 5 else int(n[0])


def typical_cpu(family: str, gen: int) -> Optional[str]:
    """'i5', 12 -> 'i5-12400F' (카탈로그에서 먼저 나오는 항목)."""
    for n in CPU_ORDER:
        if cpu_family(n) == family and intel_gen(n) == gen:
            return n
    return None


def nearest_cpu(name: str) -> Optional[str]:
    """카탈로그에 없는 모델을 같은 패밀리에서 가장 가까운 번호의 모델로 근사."""
    fam = cpu_family(name)
    num = cpu_number(name)
    best, best_d = None, None
    for n in CPU_ORDER:
        if cpu_family(n) != fam:
            continue
        d = abs(cpu_number(n) - num)
        if best_d is None or d < best_d:
            best, best_d = n, d
    return best


def gpu_by_key(key: str, vram: Optional[int] = None) -> Tuple[Optional[str], bool]:
    """모델 키('rtx 3060')와 VRAM으로 정식 이름을 찾는다. (이름, 기본값추정 여부)."""
    variants = GPU_KEYS.get(key.lower())
    if not variants:
        return None, False
    if vram is not None and vram in variants:
        return variants[vram], False
    if len(variants) == 1:
        return next(iter(variants.values())), False
    default = GPU_DEFAULT_VRAM.get(key.lower())
    if default in variants:
        return variants[default], True
    # 가장 작은 VRAM (보수적)
    return variants[min(variants)], True


def gpu_variants(key: str) -> List[int]:
    return sorted(GPU_KEYS.get(key.lower(), {}))


def platform_memory(cpu: Optional[str] = None, chipset: Optional[str] = None) -> Optional[str]:
    """소켓으로 추정한 메모리 규격 ('DDR4', 'DDR5', 'DDR4/DDR5')."""
    sock = None
    if chipset and chipset_info(chipset):
        sock = chipset_info(chipset)[0]
    elif cpu and cpu in CPUS:
        sock = CPUS[cpu]["socket"]
    return SOCKET_MEM.get(sock) if sock else None
