"""플레이 시간 CSV -> 게임 시간 시급 명세서 데이터.

CSV 형식 (헤더 필수, 열 순서는 자유):
    게임,플레이 시간,지출
    예시 RPG A,120.5,250000
영문 헤더(game,hours,spent)도 허용한다. 같은 게임이 여러 줄이면 합산한다.
엑셀에서 저장한 CP949 파일도 읽는다.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from .validation import ValidationError, parse_number, parse_text

_HEADER_ALIASES = {
    "game": ("game", "게임", "게임명", "타이틀", "title", "name"),
    "hours": ("hours", "hour", "시간", "플레이시간", "플레이타임", "playtime"),
    "spent": ("spent", "spent_krw", "krw", "지출", "결제금액", "금액", "money"),
}
_HEADER_LABELS = {"game": "게임", "hours": "플레이 시간", "spent": "지출"}


@dataclass(frozen=True)
class GameRecord:
    name: str
    hours: float
    spent_krw: float


@dataclass(frozen=True)
class PayRow:
    rank: int
    name: str
    hours: float
    spent_krw: float
    hourly: float
    comment: str


@dataclass(frozen=True)
class Payslip:
    rows: tuple[PayRow, ...]
    total_hours: float
    total_spent: float

    @property
    def avg_hourly(self) -> float:
        return self.total_spent / self.total_hours if self.total_hours else 0.0


def load_playtime_csv(path: str | Path) -> list[GameRecord]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ValidationError(f"CSV 파일을 읽을 수 없습니다: {path} ({exc.strerror or exc})") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp949")
        except UnicodeDecodeError:
            raise ValidationError("CSV 인코딩을 판별할 수 없습니다 (UTF-8 또는 CP949 필요)") from None
    return parse_playtime_csv(text)


def parse_playtime_csv(text: str) -> list[GameRecord]:
    reader = csv.reader(io.StringIO(text))
    header: dict[str, int] | None = None
    totals: dict[str, list[float]] = {}

    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        if header is None:
            header = _map_header(row)
            continue
        line_no = reader.line_num
        try:
            name = parse_text(_cell(row, header["game"]), "게임")
            hours = float(parse_number(
                _strip_suffix(_cell(row, header["hours"]), ("시간", "h", "H")),
                "플레이 시간", minimum=0, exclusive_minimum=True))
            spent = float(parse_number(
                _strip_suffix(_cell(row, header["spent"]), ("원",)),
                "지출", minimum=0))
        except ValidationError as exc:
            raise ValidationError(f"{line_no}행: {exc}") from None
        bucket = totals.setdefault(name, [0.0, 0.0])
        bucket[0] += hours
        bucket[1] += spent

    if header is None:
        raise ValidationError("CSV 가 비어 있습니다 (헤더 필요)")
    if not totals:
        raise ValidationError("데이터 행이 없습니다")
    return [GameRecord(name=name, hours=hours, spent_krw=spent)
            for name, (hours, spent) in totals.items()]


def _map_header(row: list[str]) -> dict[str, int]:
    normalized = [cell.strip().lower().replace(" ", "") for cell in row]
    found: dict[str, int] = {}
    for key, aliases in _HEADER_ALIASES.items():
        for idx, column in enumerate(normalized):
            if column in aliases:
                found[key] = idx
                break
    missing = [_HEADER_LABELS[key] for key in _HEADER_ALIASES if key not in found]
    if missing:
        raise ValidationError(f"헤더에 필요한 열이 없습니다: {', '.join(missing)} (예: 게임,플레이 시간,지출)")
    return found


def _cell(row: list[str], idx: int) -> str:
    return row[idx] if idx < len(row) else ""


def _strip_suffix(text: str, suffixes: tuple[str, ...]) -> str:
    value = text.strip()
    for suffix in suffixes:
        if value.endswith(suffix):
            value = value[: -len(suffix)].strip()
            break
    return value


def wage_comment(hourly: float) -> str:
    label = f"시급 {round(hourly):,}원짜리 알바"
    if hourly <= 0:
        tail = "열정 페이 100%, 돈 한 푼 안 내고 노는 중"
    elif hourly < 1_000:
        tail = "가성비 끝판왕, 사실상 자원봉사"
    elif hourly < 5_000:
        tail = "평범한 동네 알바 페이"
    elif hourly < 20_000:
        tail = "야간 알바급, 피곤하지만 참을 만함"
    else:
        tail = "이쯤 되면 게임사 VIP 고객 등급"
    return f"{label} — {tail}"


def build_payslip(records: list[GameRecord]) -> Payslip:
    if not records:
        raise ValidationError("명세서를 만들 데이터가 없습니다")
    scored = sorted(
        ((r.spent_krw / r.hours, r) for r in records),
        key=lambda item: (-item[0], item[1].name),
    )
    rows: list[PayRow] = []
    rank = 0
    previous: float | None = None
    for position, (hourly, record) in enumerate(scored, start=1):
        if previous is None or hourly != previous:
            rank = position
            previous = hourly
        rows.append(PayRow(
            rank=rank,
            name=record.name,
            hours=record.hours,
            spent_krw=record.spent_krw,
            hourly=hourly,
            comment=wage_comment(hourly),
        ))
    return Payslip(
        rows=tuple(rows),
        total_hours=sum(r.hours for r in records),
        total_spent=sum(r.spent_krw for r in records),
    )
