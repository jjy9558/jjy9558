"""터미널 출력 (한국어). 표 정렬은 table.render_table 을 쓴다."""

from __future__ import annotations

from .banner import RULE_FIFTY_FIFTY, RULE_GUARANTEE
from .currency import fmt_krw, fmt_won
from .pity import PityReport
from .rate import Payslip
from .table import pad, render_table

_WIDTH = 60
_PITY_NOTE = "참고: 확률표 기준 기댓값 계산이며 실제 결과를 보장하지 않습니다. 과소비 주의."


def render_pity_report(report: PityReport) -> str:
    banner = report.banner
    dist = report.distribution
    package = report.package
    unit = banner.currency_name

    if banner.rule == RULE_FIFTY_FIFTY:
        rule_text = "50/50 (5성 적중 시 50% 픽업, 빗나가면 다음 적중 확정)"
        guarantee_text = "예 (다음 5성 픽업 확정)" if dist.guaranteed else "아니오"
    elif banner.rule == RULE_GUARANTEE:
        rule_text = "확정형 (5성 적중 = 픽업)"
        guarantee_text = "해당 없음"
    else:  # pragma: no cover - Banner 가 검증한다
        rule_text = banner.rule
        guarantee_text = "-"

    lines = [f"■ {banner.name}"]
    if banner.label:
        lines.append(f"  {banner.label}")
    lines += [
        f"  확률: 기본 {banner.base_rate_pct:g}% · 소프트 천장 {banner.soft_pity_start}회차부터 "
        f"매회 +{banner.soft_pity_increase_pct:g}%p · 하드 천장 {banner.hard_pity}회",
        f"  룰: {rule_text}",
        f"  현재 상태: 천장 카운트 {dist.pity_count}/{banner.hard_pity} "
        f"(하드 천장까지 {banner.hard_pity - dist.pity_count}회), 확정 상태: {guarantee_text}",
        f"  뽑기 1회: {banner.cost_per_pull:,g}{unit}",
        f"  환율: {package.price_krw:,}원 = {package.units:,}{package.unit_name} "
        f"(1{package.unit_name} ≈ {package.krw_per_unit:,.1f}원)",
        f"  보유: {report.currency_units:,g}{unit} → 뽑기 {report.budget_pulls:,}회 가능",
        "",
    ]

    rows = [
        ("기대 뽑기 횟수", f"{report.expected_pulls:,.1f}회"),
        ("중앙값 (50% 달성)", f"{report.median_pulls:,}회"),
        ("90% 달성 뽑기 횟수", f"{report.p90_pulls:,}회"),
        ("최악 뽑기 횟수 (천장 기준)", f"{report.worst_pulls:,}회"),
        ("기대 비용 (환율 환산)", fmt_krw(report.expected_krw)),
        ("90% 달성 비용 (패키지 올림 구매)", fmt_krw(report.p90_krw)),
        ("최악 비용 (패키지 올림 구매)", fmt_krw(report.worst_krw)),
        (f"보유 재화 {report.budget_pulls:,}회 안에 픽업 확률",
         f"{report.p_within_budget * 100:.1f}%"),
    ]
    lines.append(render_table(["항목", "값"], rows, ["l", "r"]))
    lines += ["", f"판정: {report.verdict}", _PITY_NOTE]
    return "\n".join(lines)


def render_payslip(slip: Payslip) -> str:
    bar = "=" * _WIDTH
    lines = [
        bar,
        pad("게임 시간 시급 명세서", _WIDTH, "c").rstrip(),
        bar,
        f" 총 플레이 시간 : {slip.total_hours:,.1f} 시간",
        f" 총 지출(공제)  : {fmt_won(slip.total_spent)}",
        f" 평균 시급      : {fmt_won(slip.avg_hourly)} / 시간",
        "-" * _WIDTH,
    ]
    rows = [
        [str(r.rank), r.name, f"{r.hours:,.1f}", fmt_won(r.spent_krw), fmt_won(r.hourly), r.comment]
        for r in slip.rows
    ]
    lines.append(render_table(
        ["순위", "게임", "플레이(시간)", "지출", "시급", "한줄평"],
        rows,
        ["r", "l", "r", "r", "r", "l"],
    ))
    lines.append("시급 = 지출 ÷ 플레이 시간. 순위는 시급 높은 순 (1위 = 가장 비싼 알바).")
    return "\n".join(lines)
