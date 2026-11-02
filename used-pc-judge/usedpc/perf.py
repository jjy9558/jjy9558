"""간단한 성능 등급표 기반 계산 (대략치).

정확한 벤치마크가 아니라, "이 조합이면 대충 어느 정도"를 가늠하는 용도다.
 - GPU score: RTX 4070 = 100
 - CPU game: Ryzen 7 7800X3D = 100 / CPU mt: Ryzen 7 5800X = 100
"""

import re
from typing import Dict, Optional

from . import catalog

# 해상도별 "CPU가 GPU를 얼마나 따라가야 하나" 계수 (cpu_game >= gpu_score * k 이면 병목 거의 없음)
RES_K = {"1080p": 0.65, "1440p": 0.55, "4k": 0.40}
# 해상도별 목표 GPU 점수 (고옵션 60fps 안팎 감각)
RES_TARGET_GPU = {"1080p": 60, "1440p": 100, "4k": 150}
RES_VRAM = {"1080p": 8, "1440p": 12, "4k": 16}
RES_LABEL = {"1080p": "1080p", "1440p": "1440p", "4k": "4K"}


def gpu_score(name: Optional[str]) -> Optional[int]:
    return catalog.GPUS[name]["score"] if name in catalog.GPUS else None


def cpu_game(name: Optional[str]) -> Optional[int]:
    return catalog.CPUS[name]["game"] if name in catalog.CPUS else None


def cpu_mt(name: Optional[str]) -> Optional[int]:
    return catalog.CPUS[name]["mt"] if name in catalog.CPUS else None


def gaming_effective(cpu: Optional[str], gpu: Optional[str], res: str = "1440p") -> Optional[Dict]:
    """게임 체감 성능(GPU 점수 단위)과 병목 위치."""
    g = gpu_score(gpu)
    c = cpu_game(cpu)
    if g is None or c is None:
        return None
    cap = c / RES_K[res]
    eff = min(g, cap)
    vram = catalog.GPUS[gpu]["vram"]
    penalty = 1.0
    if vram < RES_VRAM[res]:
        penalty = 0.85
    return {
        "eff": eff * penalty,
        "gpu": g,
        "cpu_cap": cap,
        "bottleneck": "cpu" if cap < g * 0.9 else ("gpu" if g < cap * 0.6 else "balanced"),
        "vram_short": vram < RES_VRAM[res],
    }


def llm_vram_need(params_b: float, quant: str = "q4") -> float:
    """대략적인 필요 VRAM(GB): 가중치 + KV 캐시/오버헤드. (Q4_K_M 기준 약 0.57 GB/B)"""
    bytes_per_param = {"q4": 0.57, "q5": 0.70, "q6": 0.82, "q8": 1.06, "fp16": 2.0}.get(quant, 0.57)
    return round(params_b * bytes_per_param + 1.5 + params_b * 0.03, 1)


def parse_use(text: str) -> Dict:
    """'게임 1440p', '로컬 LLM 30B', '영상편집' -> 용도 dict. 알 수 없으면 ValueError."""
    t = (text or "").lower().strip()
    if not t:
        raise ValueError("용도가 비어 있습니다")
    res = None
    if re.search(r"4k|uhd|2160", t):
        res = "4k"
    elif re.search(r"1440|qhd|2k|wqhd", t):
        res = "1440p"
    elif re.search(r"1080|fhd|풀\s*hd", t):
        res = "1080p"
    m = re.search(r"(\d+(?:\.\d+)?)\s*b(?![a-z])", t)
    q = "q4"
    mq = re.search(r"(q4|q5|q6|q8|fp16)", t)
    if mq:
        q = mq.group(1)
    if re.search(r"llm|엘엘엠|라마|llama|ollama|올라마|로컬\s*ai|로컬ai|gguf|qwen|gemma|mistral|딥시크|deepseek", t):
        if not m:
            raise ValueError("LLM 용도는 모델 크기가 필요합니다. 예: '로컬 LLM 30B'")
        params = float(m.group(1))
        return {"kind": "llm", "params_b": params, "quant": q,
                "label": "로컬 LLM {:g}B ({})".format(params, q.upper()),
                "vram_need": llm_vram_need(params, q)}
    if re.search(r"스테이블|stable\s*diffusion|\bsd\b|comfy|이미지\s*생성|sdxl|flux", t):
        return {"kind": "diffusion", "label": "이미지 생성(Stable Diffusion 등)", "vram_need": 12.0}
    if re.search(r"영상|편집|프리미어|premiere|다빈치|davinci|렌더|인코딩|유튜브\s*편집", t):
        return {"kind": "editing", "res": res or "1080p",
                "label": "영상편집" + (" ({})".format(RES_LABEL[res]) if res else "")}
    if re.search(r"게임|겜|gaming|롤|발로란트|배그|오버워치|디아블로|로아|로스트아크|fps|레이트레이싱", t) or res:
        r = res or "1080p"
        return {"kind": "gaming", "res": r, "label": "게임 {}".format(RES_LABEL[r])}
    raise ValueError("알 수 없는 용도: '{}'  (예: '게임 1440p', '로컬 LLM 30B', '영상편집')".format(text))


def general_index(cpu: Optional[str], gpu: Optional[str]) -> Optional[float]:
    """compare 표에 보여줄 '게임 성능지수'(1440p 기준 체감 GPU점수)."""
    r = gaming_effective(cpu, gpu, "1440p")
    return round(r["eff"], 1) if r else None
