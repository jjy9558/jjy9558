"""작업 유형별 모델 배정 제안표. 단가는 설정 파일(JSON)에서만 받는다. 코드에 단가를 넣지 않는다.

설정 예시(prices.json): {"models": {"haiku": {"in_per_mtok": 0, "out_per_mtok": 0}, ...},
                         "tasks": {"파일 검색": {"tier": "haiku", "in_tokens": 8000, "out_tokens": 500}}}
tasks를 생략하면 아래 기본 작업 유형(토큰 수는 예시 가정)을 쓴다. 단가가 없으면 비용 열은 '단가 없음'.
"""
import json
from pathlib import Path

from .report import Report

# 모델 등급(상대적 추천)이며 금액이 아니다. 토큰 수는 예시 가정.
DEFAULT_TASKS = {
    "파일 검색/목록 정리": {"tier": "small", "in_tokens": 8000, "out_tokens": 500},
    "단순 포맷 변경/이름 바꾸기": {"tier": "small", "in_tokens": 6000, "out_tokens": 800},
    "테스트 작성/기능 구현": {"tier": "mid", "in_tokens": 30000, "out_tokens": 4000},
    "버그 원인 추적": {"tier": "mid", "in_tokens": 50000, "out_tokens": 3000},
    "아키텍처 설계/큰 리팩터링": {"tier": "large", "in_tokens": 80000, "out_tokens": 8000},
    "코드 리뷰/보안 검토": {"tier": "large", "in_tokens": 40000, "out_tokens": 3000},
}


def load_config(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_report(cfg: dict | None, runs: int = 100) -> Report:
    cfg = cfg or {}
    models = cfg.get("models") or {}
    tasks = cfg.get("tasks") or DEFAULT_TASKS
    rep = Report("모델 라우팅 제안표")
    rep.intro.append("단가는 코드에 없습니다. --config 로 준 JSON의 models 값만 사용하며, 값이 없으면 비용은 계산하지 않습니다.")
    rep.intro.append("토큰 수는 예시 가정입니다. 실제 평균은 scan/usage 결과로 설정 파일의 tasks를 채우세요.")
    rows = []
    for name, t in tasks.items():
        tier = t.get("tier", "")
        m = models.get(tier)
        if m and "in_per_mtok" in m and "out_per_mtok" in m:
            one = (t.get("in_tokens", 0) * m["in_per_mtok"] + t.get("out_tokens", 0) * m["out_per_mtok"]) / 1_000_000
            cost = f"{one * runs:,.2f} (추정, 단위는 설정 파일 기준)"
        else:
            cost = "단가 없음"
        rows.append([name, tier, t.get("in_tokens", 0), t.get("out_tokens", 0), cost])
    rep.add(f"작업 유형별 배정 ({runs}회 실행 가정)", ["small = 가장 작은 모델 등급(예: Haiku급), mid = 중간(Sonnet급), large = 큰 모델(Opus급). 등급 이름은 설정의 models 키와 맞추세요."],
            ["작업 유형", "추천 등급", "입력 토큰(가정)", "출력 토큰(가정)", f"{runs}회 비용"], rows)
    return rep
