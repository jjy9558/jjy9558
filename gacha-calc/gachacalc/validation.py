"""사용자 입력 검증 도구. 모든 오류는 ValidationError 로 통일한다."""

from __future__ import annotations

import math


class ValidationError(ValueError):
    """사용자 입력이 잘못되었을 때 발생한다."""


def parse_number(
    value: object,
    field: str,
    *,
    integer: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float | int:
    """문자열(콤마 허용)이나 숫자를 검증해서 돌려준다."""
    if isinstance(value, bool):
        raise ValidationError(f"{field}: 숫자가 필요합니다 (받은 값: {value})")
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        try:
            number = float(text)
        except ValueError:
            raise ValidationError(f"{field}: 숫자가 아닙니다 (받은 값: {value!r})") from None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise ValidationError(f"{field}: 숫자가 필요합니다 (받은 값: {value!r})")

    if not math.isfinite(number):
        raise ValidationError(f"{field}: 유한한 숫자여야 합니다 (받은 값: {value!r})")
    if integer and not number.is_integer():
        raise ValidationError(f"{field}: 정수여야 합니다 (받은 값: {value!r})")
    if minimum is not None and (number < minimum or (exclusive_minimum and number == minimum)):
        wording = "초과여야" if exclusive_minimum else "이상이어야"
        raise ValidationError(f"{field}: {minimum:g} {wording} 합니다 (받은 값: {value!r})")
    if maximum is not None and number > maximum:
        raise ValidationError(f"{field}: {maximum:g} 이하여야 합니다 (받은 값: {value!r})")
    return int(number) if integer else number


def parse_text(value: object, field: str) -> str:
    """비어 있지 않은 문자열인지 확인한다."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field}: 비어 있지 않은 문자열이어야 합니다")
    return value.strip()
