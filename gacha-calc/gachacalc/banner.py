"""배너 설정과 뽑기 1회당 5성(적중) 확률 모델.

용어
- 천장 카운트: 마지막 5성 이후 뽑은 횟수. 다음 뽑기는 "카운트 + 1" 번째 뽑기다.
- 소프트 천장: soft_pity_start 번째 뽑기부터 적중 확률이 매 뽑기마다 증가한다.
- 하드 천장: hard_pity 번째 뽑기는 확률 100% (반드시 5성).
- 50/50: 5성 적중 시 50% 확률로 픽업. 빗나가면 다음 5성 적중은 픽업 확정.
- guarantee: 5성 적중 = 픽업 (빗나감이 없는 확정형).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .validation import ValidationError, parse_number, parse_text

RULE_FIFTY_FIFTY = "50/50"
RULE_GUARANTEE = "guarantee"
RULES = (RULE_FIFTY_FIFTY, RULE_GUARANTEE)
FIFTY_FIFTY_SHARE = 0.5
MAX_HARD_PITY = 1000

_REQUIRED_KEYS = (
    "name",
    "base_rate_pct",
    "soft_pity_start",
    "soft_pity_increase_pct",
    "hard_pity",
    "cost_per_pull",
)
_OPTIONAL_KEYS = ("rule", "currency_name", "label")


@dataclass(frozen=True)
class Banner:
    name: str
    base_rate_pct: float
    soft_pity_start: int
    soft_pity_increase_pct: float
    hard_pity: int
    cost_per_pull: float
    rule: str = RULE_FIFTY_FIFTY
    currency_name: str = "재화"
    label: str = ""

    def __post_init__(self) -> None:
        hard = int(parse_number(self.hard_pity, "하드 천장(뽑기 수)", integer=True,
                                minimum=1, maximum=MAX_HARD_PITY))
        soft = int(parse_number(self.soft_pity_start, "소프트 천장 시작 번호", integer=True,
                                minimum=1, maximum=hard))
        base = float(parse_number(self.base_rate_pct, "기본 확률(%)", minimum=0, maximum=100,
                                  exclusive_minimum=True))
        increase = float(parse_number(self.soft_pity_increase_pct, "소프트 천장 증가량(%p)",
                                      minimum=0, maximum=100))
        cost = float(parse_number(self.cost_per_pull, "뽑기 1회 비용", minimum=0,
                                  exclusive_minimum=True))
        rule = parse_text(self.rule, "룰")
        if rule not in RULES:
            raise ValidationError(f"룰은 {' 또는 '.join(RULES)} 중 하나여야 합니다 (받은 값: {rule!r})")
        values = {
            "name": parse_text(self.name, "배너 이름"),
            "base_rate_pct": base,
            "soft_pity_start": soft,
            "soft_pity_increase_pct": increase,
            "hard_pity": hard,
            "cost_per_pull": cost,
            "rule": rule,
            "currency_name": parse_text(self.currency_name, "재화 이름"),
            "label": "" if self.label is None else str(self.label),
        }
        for field, value in values.items():
            object.__setattr__(self, field, value)

    @property
    def base_rate(self) -> float:
        return self.base_rate_pct / 100.0

    @property
    def featured_share(self) -> float:
        """5성 적중 한 번이 픽업일 확률."""
        return FIFTY_FIFTY_SHARE if self.rule == RULE_FIFTY_FIFTY else 1.0

    @classmethod
    def from_dict(cls, data: object) -> "Banner":
        if not isinstance(data, dict):
            raise ValidationError("배너 설정은 JSON 객체여야 합니다")
        unknown = sorted(set(data) - set(_REQUIRED_KEYS) - set(_OPTIONAL_KEYS))
        if unknown:
            raise ValidationError(f"알 수 없는 항목: {', '.join(unknown)}")
        missing = [key for key in _REQUIRED_KEYS if key not in data]
        if missing:
            raise ValidationError(f"필수 항목 누락: {', '.join(missing)}")
        return cls(**data)

    @classmethod
    def load(cls, path: str | Path) -> "Banner":
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationError(f"배너 파일을 읽을 수 없습니다: {path} ({exc.strerror or exc})") from None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"배너 JSON 형식 오류 ({path}): {exc}") from None
        return cls.from_dict(data)


def hit_probability(banner: Banner, pull_no: int) -> float:
    """pull_no 번째 뽑기(천장 카운트 이후 1부터 셈)에서 5성이 나올 확률."""
    if pull_no < 1:
        raise ValueError("뽑기 번호는 1부터 시작합니다")
    if pull_no >= banner.hard_pity:
        return 1.0
    if pull_no < banner.soft_pity_start:
        return banner.base_rate
    steps = pull_no - banner.soft_pity_start + 1
    return min(1.0, banner.base_rate + steps * banner.soft_pity_increase_pct / 100.0)
