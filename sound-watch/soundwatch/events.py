"""엔진이 내보내는 이벤트 자료형."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Detection:
    """소리 인식 결과. 시각은 스트림 시작 기준 초(엔진이 epoch 로 바꿔 t_epoch 를 채움)."""
    kind: str               # template | knock | beep | melody
    name: str
    t_start: float
    t_end: float
    score: float = 0.0      # 템플릿: 평균 비용(낮을수록 좋음). 그 외 0
    message: str = ""
    info: Dict = field(default_factory=dict)
    t_epoch: float = 0.0
    notify: bool = True

    def to_dict(self):
        return {"kind": self.kind, "name": self.name, "t_start": round(self.t_start, 3),
                "t_end": round(self.t_end, 3), "score": round(self.score, 3), "message": self.message,
                "info": self.info, "t_epoch": self.t_epoch}


@dataclass
class NoiseEvent:
    start: float            # 스트림 상대 초 (엔진이 epoch 로 바꿔 저장)
    end: float
    duration: float
    peak_dbfs: float        # A-가중 dBFS (100 ms 프레임 최대)
    leq_dbfs: float         # 이벤트 구간 에너지 평균
    kind: str = ""          # impact | sustained (자동 추정)
    n_segments: int = 1
