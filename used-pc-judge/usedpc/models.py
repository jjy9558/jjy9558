"""파싱 결과 데이터 모델."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Field:
    """파싱된 필드 하나. conf 는 0~1 신뢰도, src 는 'rule' 또는 'llm'."""
    value: Any
    conf: float = 0.9
    src: str = "rule"
    raw: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Flag:
    """주의/위험 신호. severity 1(참고) 2(주의) 3(심각)."""
    code: str
    severity: int
    label: str
    evidence: str = ""
    scam: bool = False      # 사기 의심 계열인가
    defect: bool = False    # 고장/불량 계열인가
    src: str = "rule"


@dataclass
class Parsed:
    text: str = ""
    title: str = ""
    fields: Dict[str, Field] = field(default_factory=dict)
    flags: List[Flag] = field(default_factory=list)
    positives: List[str] = field(default_factory=list)
    excluded: set = field(default_factory=set)       # 판매 제외 부품: gpu, ram, ssd, psu ...
    notes: List[str] = field(default_factory=list)   # 파서가 남기는 참고 메모
    price_candidates: List[dict] = field(default_factory=list)
    today: Optional[str] = None

    def has(self, name: str) -> bool:
        return name in self.fields

    def val(self, name: str, default: Any = None) -> Any:
        f = self.fields.get(name)
        return f.value if f is not None else default

    def conf(self, name: str) -> float:
        f = self.fields.get(name)
        return f.conf if f is not None else 0.0

    def set(self, name: str, value: Any, conf: float = 0.9, raw: str = "",
            src: str = "rule", **meta: Any) -> Field:
        f = Field(value, conf, src, raw, dict(meta))
        self.fields[name] = f
        return f

    def flag_codes(self) -> List[str]:
        return [f.code for f in self.flags]

    def has_flag(self, code: str) -> bool:
        return any(f.code == code for f in self.flags)

    def add_flag(self, flag: Flag) -> None:
        if not self.has_flag(flag.code):
            self.flags.append(flag)
