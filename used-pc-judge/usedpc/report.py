"""콘솔(텍스트)과 Markdown 리포트 출력."""

from typing import List, Optional

from .compare import Ranked
from .judge import Judgment
from .prices import PriceTable
from .questions import Question, seller_message
from .textutil import fmt_man, fmt_money, fmt_won, md_table, pad, table
from .upgrade import UpgradePlan

BAR = "=" * 64
SUB = "-" * 64

VERDICT_MARK = {"싸다": "싸다 (시세 이하)", "적정": "적정", "비싸다": "비싸다 (시세 초과)",
                "의심": "의심 (사기/하자 가능성)", "판단불가": "판단불가"}


def _spec_lines(j: Judgment) -> List[str]:
    p = j.parsed
    lines = []
    for it in j.items:
        mark = " (가정)" if it.assumed else ""
        lines.append((it.label + mark, fmt_man(it.amount)))
    return lines


def _info_lines(j: Judgment) -> List[str]:
    p = j.parsed
    out = []
    if p.has("age_years"):
        out.append("사용 연수: 약 {:g}년 ('{}')".format(p.val("age_years"), p.fields["age_years"].raw))
    w = p.val("warranty")
    if w:
        if w.get("has"):
            txt = "무상 보증 남음"
            if w.get("until"):
                txt += " (~{})".format(w["until"])
            elif w.get("months_left"):
                txt += " (약 {:g}개월)".format(w["months_left"])
            out.append(txt + (" / 영수증 있음" if w.get("receipt") else ""))
        elif w.get("has") is False:
            out.append("보증: 만료/없음")
        elif w.get("receipt"):
            out.append("영수증/구매내역 있음")
    mon = p.val("monitor")
    if mon is not None:
        out.append("모니터: " + ("포함" if mon.get("included") else "제외(본체만)"))
    if p.val("peripherals"):
        out.append("주변기기 포함: " + ", ".join(p.val("peripherals")))
    if p.val("brand_pc"):
        out.append("브랜드 완제품 PC로 보임")
    if p.has("negotiable"):
        out.append("네고: " + ("가능" if p.val("negotiable") else "불가"))
    return out


def render_judgment(j: Judgment, questions: Optional[List[Question]] = None,
                    plan: Optional[UpgradePlan] = None, title: Optional[str] = None) -> str:
    p = j.parsed
    L: List[str] = []
    L.append(BAR)
    L.append(" 중고 PC 판정  |  " + (title or p.title or "(제목 없음)"))
    L.append(BAR)
    for w in j.warnings:
        L.append("[경고] " + w)
    if j.warnings:
        L.append("")
    L.append("판매가       : " + (fmt_money(j.price) if j.price is not None else "(찾지 못함)"))
    if j.fair_mid is not None and j.items:
        L.append("적정가 범위  : {} ~ {}  (중앙 {}, 신뢰도 {})".format(
            fmt_man(j.fair_low), fmt_man(j.fair_high), fmt_man(j.fair_mid), j.confidence))
    L.append("판정         : [ {} ]".format(VERDICT_MARK.get(j.verdict, j.verdict)) +
             ("  (중앙값 대비 {:+.0f}%)".format(j.diff_pct) if j.diff_pct is not None else ""))
    if j.perf_index is not None:
        L.append("성능 지수    : 게임(1440p 기준) 체감 GPU 지수 {:g}  (RTX 4070 = 100)".format(j.perf_index))
    L.append("")

    if j.items:
        L.append("[인식한 구성과 부품 시세]")
        rows = _spec_lines(j)
        w = max(len(r[0]) for r in rows)
        for name, price in rows:
            L.append("  {}  {}".format(pad(name, 34), pad(price, 9, True)))
        L.append("  " + SUB[:46])
        L.append("  {}  {}".format(pad("부품 합계", 34), pad(fmt_man(j.parts_sum), 9, True)))
        for a in j.adjustments:
            L.append("  {}  {}".format(pad("  보정: " + a.label, 34), pad(("+" if a.amount > 0 else "-") + fmt_man(abs(a.amount)), 9, True)))
        for a in j.extras:
            L.append("  {}  {}".format(pad("  가산: " + a.label, 34), pad("+" + fmt_man(a.amount), 9, True)))
        L.append("  {}  {}".format(pad("적정가 중앙값", 34), pad(fmt_man(j.fair_mid), 9, True)))
        L.append("")
    info = _info_lines(j)
    if info:
        L.append("[기타 정보]")
        L.extend("  - " + x for x in info)
        L.append("")

    L.append("[판정 이유]")
    for r in j.reasons:
        L.append("  - " + r)
    L.append("")

    if questions:
        L.append("[판매자에게 물어볼 것]")
        for i, q in enumerate(questions, 1):
            tag = {1: "필수", 2: "권장", 3: "참고"}[q.priority]
            L.append("  {}. ({}) {}".format(i, tag, q.text))
        L.append("")
        L.append("[복사해서 보낼 메시지 템플릿]")
        for line in seller_message(questions).splitlines():
            L.append("  | " + line)
        L.append("")
    if plan is not None:
        L.extend(render_plan(plan).splitlines())
        L.append("")
    L.append("※ 가격표는 사용자가 관리하는 대략치입니다. 최종 판단은 최근 거래완료 시세와 직접 확인으로 하세요.")
    return "\n".join(L)


def render_plan(plan: UpgradePlan) -> str:
    L = ["[업그레이드 추천] 용도: {}".format(plan.use["label"]) +
         ("  /  예산 {}".format(fmt_man(plan.budget)) if plan.budget else "")]
    if plan.target_text:
        L.append("  " + plan.target_text)
    for m in plan.messages:
        L.append("  * " + m)
    if plan.current_notes:
        L.append("  현재 구성 점검:")
        for n in plan.current_notes:
            L.append("    - " + n)
    for i, o in enumerate(plan.options, 1):
        L.append("")
        L.append("  추천 {} - {}".format(i, o.label))
        L.append("    " + o.headline)
        L.append("    예상 성능지수 {:g} (현재 {:g}) / 목표 {}".format(
            o.perf, plan.current_perf or 0, "충족" if o.meets else "미달"))
        for n in o.notes:
            L.append("      · " + n)
    if not plan.options and not plan.messages:
        L.append("  추천할 교체가 없습니다.")
    return "\n".join(L)


# ---------------------------------------------------------------------------
def _perf_per_man(j: Judgment) -> str:
    if j.perf_index is None or not j.price or j.verdict in ("의심", "판단불가"):
        return "-"
    return "{:.2f}".format(j.perf_index / (j.price / 10000.0))


def render_compare(rows: List[Ranked], budget_note: str = "") -> str:
    header = ["순위", "매물", "판매가", "적정가(중앙)", "판정", "점수", "성능", "성능/만원", "주의"]
    data = []
    for r in rows:
        j = r.judgment
        flags = ",".join(f.code for f in j.parsed.flags if f.severity >= 2) or "-"
        data.append([str(r.rank), r.label, fmt_man(j.price) if j.price else "-",
                     fmt_man(j.fair_mid) if j.items else "-", j.verdict,
                     "{:g}".format(j.value_score) if j.value_score is not None else "-",
                     "{:g}".format(j.perf_index) if j.perf_index is not None else "-", _perf_per_man(j), flags])
    L = [BAR, " 매물 비교 (가성비 점수 순, 의심/판단불가는 아래로)", BAR]
    seen = set()
    for r in rows:
        for w in r.judgment.warnings:
            if w not in seen:
                seen.add(w)
                L.append("[경고] " + w)
    L.extend(table(data, header, right_cols=(0, 2, 3, 5, 6, 7)))
    L.append("")
    L.append("점수 = 적정가 중앙값 / 판매가 x 100 - 위험신호 감점 (100 = 적정). 높을수록 이득. 성능 = 1440p 체감 GPU 지수, 성능/만원 = 성능 / 판매가(만원).")
    L.append("※ 점수는 가격표에 전적으로 의존합니다. 상위 매물이라도 '판매자에게 물어볼 것'을 먼저 해결하세요.")
    for r in rows[:3]:
        j = r.judgment
        L.append("")
        L.append("#{} {} -> {}".format(r.rank, r.label, j.verdict))
        for line in j.reasons[:3]:
            L.append("   - " + line)
    return "\n".join(L)


def render_compare_md(rows: List[Ranked]) -> str:
    L = ["# 중고 PC 매물 비교", ""]
    seen = set()
    for r in rows:
        for w in r.judgment.warnings:
            if w not in seen:
                seen.add(w)
                L.append("> **경고**: " + w)
    L.append("")
    header = ["순위", "매물", "판매가", "적정가(중앙)", "판정", "점수", "성능지수", "성능/만원", "주의 신호"]
    data = []
    for r in rows:
        j = r.judgment
        flags = ", ".join(f.code for f in j.parsed.flags if f.severity >= 2) or "-"
        data.append([str(r.rank), r.label, fmt_man(j.price) if j.price else "-",
                     fmt_man(j.fair_mid) if j.items else "-", j.verdict,
                     "{:g}".format(j.value_score) if j.value_score is not None else "-",
                     "{:g}".format(j.perf_index) if j.perf_index is not None else "-", _perf_per_man(j), flags])
    L.extend(md_table(data, header))
    L.append("")
    L.append("점수 = 적정가 중앙값 / 판매가 x 100 - 위험신호 감점 (100 = 적정).")
    return "\n".join(L) + "\n"


def render_judgment_md(j: Judgment, questions: Optional[List[Question]] = None,
                       plan: Optional[UpgradePlan] = None, title: Optional[str] = None) -> str:
    p = j.parsed
    L = ["# 중고 PC 판정: " + (title or p.title or "(제목 없음)"), ""]
    for w in j.warnings:
        L.append("> **경고**: " + w)
    if j.warnings:
        L.append("")
    L.append("| 항목 | 값 |")
    L.append("|---|---|")
    L.append("| 판매가 | {} |".format(fmt_money(j.price) if j.price is not None else "(찾지 못함)"))
    if j.items:
        L.append("| 적정가 범위 | {} ~ {} (중앙 {}) |".format(fmt_man(j.fair_low), fmt_man(j.fair_high), fmt_man(j.fair_mid)))
        L.append("| 신뢰도 | {} |".format(j.confidence))
    L.append("| 판정 | **{}**{} |".format(j.verdict, " (중앙값 대비 {:+.0f}%)".format(j.diff_pct) if j.diff_pct is not None else ""))
    L.append("")
    if j.items:
        L.append("## 인식한 구성과 부품 시세")
        rows = [[it.label + (" (가정)" if it.assumed else ""), fmt_man(it.amount)] for it in j.items]
        rows.append(["**부품 합계**", fmt_man(j.parts_sum)])
        for a in j.adjustments:
            rows.append(["보정: " + a.label, ("+" if a.amount > 0 else "-") + fmt_man(abs(a.amount))])
        for a in j.extras:
            rows.append(["가산: " + a.label, "+" + fmt_man(a.amount)])
        rows.append(["**적정가 중앙값**", fmt_man(j.fair_mid)])
        L.extend(md_table(rows, ["항목", "금액"]))
        L.append("")
    info = _info_lines(j)
    if info:
        L.append("## 기타 정보")
        L.extend("- " + x for x in info)
        L.append("")
    L.append("## 판정 이유")
    L.extend("- " + r for r in j.reasons)
    L.append("")
    if questions:
        L.append("## 판매자에게 물어볼 것")
        for i, q in enumerate(questions, 1):
            L.append("{}. ({}) {}".format(i, {1: "필수", 2: "권장", 3: "참고"}[q.priority], q.text))
        L.append("")
        L.append("### 메시지 템플릿")
        L.append("```")
        L.append(seller_message(questions))
        L.append("```")
        L.append("")
    if plan is not None:
        L.append("## 업그레이드 추천 ({})".format(plan.use["label"]))
        if plan.target_text:
            L.append(plan.target_text)
            L.append("")
        for m in plan.messages:
            L.append("- " + m)
        if plan.current_notes:
            L.append("")
            L.append("**현재 구성 점검**")
            L.extend("- " + n for n in plan.current_notes)
        for i, o in enumerate(plan.options, 1):
            L.append("")
            L.append("### 추천 {} - {}".format(i, o.label))
            L.append("**{}**".format(o.headline))
            L.append("")
            L.append("예상 성능지수 {:g} (현재 {:g}), 목표 {}".format(o.perf, plan.current_perf or 0, "충족" if o.meets else "미달"))
            L.extend("- " + n for n in o.notes)
        L.append("")
    L.append("---")
    L.append("가격표는 사용자가 관리하는 대략치입니다. 최종 판단은 최근 거래완료 시세와 직접 확인으로 하세요.")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
def render_prices(t: PriceTable, section: Optional[str] = None, today=None) -> str:
    from datetime import date
    L = []
    for w in t.warnings(today or date.today()):
        L.append("[경고] " + w)
    L.append("가격표: {}  (기준일 {}, placeholder={})".format(t.path, t.data.get("as_of"), t.placeholder))
    L.append("")
    secs = [section] if section else ["gpu", "cpu", "board", "ram_per_gb", "ssd_per_gb", "hdd_per_tb",
                                      "psu_by_watt", "case", "cooler", "adjust"]
    for s in secs:
        d = t.data.get(s)
        if d is None:
            L.append("(없는 섹션: {})".format(s))
            continue
        L.append("[{}]".format(s))
        if isinstance(d, dict):
            rows = []
            for k, v in d.items():
                if k.startswith("_") or isinstance(v, dict):
                    continue
                if s == "adjust":
                    rows.append(["  " + k, str(v)])
                else:
                    rows.append(["  " + str(k), "{} ({})".format(fmt_won(v), fmt_man(v)) if isinstance(v, (int, float)) else str(v)])
            L.extend("{}  {}".format(pad(r[0], 26), r[1]) for r in rows)
        else:
            L.append("  " + str(d))
        L.append("")
    return "\n".join(L)
