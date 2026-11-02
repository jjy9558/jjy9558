"""야근 수당 명세서: Claude 가 혼자 일한 시간을 월급 명세서 모양의 HTML 한 장으로 만든다 (농담 + 추정치).

시간은 세션 내 응답 타임스탬프 간격(IDLE_GAP 이하)을 이어 붙여 계산한다. 금액은 wallet 의 단가표(예시값 가능)로 추정.
"""
from __future__ import annotations

import datetime as _dt
import html
import os
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from devguard import wallet

IDLE_GAP = _dt.timedelta(minutes=15)     # 이보다 오래 비면 '쉬는 시간' 으로 보고 시간에 넣지 않음
WORK_START, WORK_END = 9, 18             # 정규 근무 (평일 09~18)
NIGHT_START, NIGHT_END = 22, 6           # 심야


@dataclass
class SessionPay:
    session: str
    project: str
    regular: float = 0.0   # 시간
    overtime: float = 0.0
    night: float = 0.0
    weekend: float = 0.0
    tokens: int = 0
    cost: float = 0.0

    @property
    def hours(self) -> float:
        return self.regular + self.overtime + self.night + self.weekend


def month_bounds(month: str, tz: Optional[_dt.tzinfo]) -> "tuple[_dt.datetime, _dt.datetime]":
    try:
        y, m = month.split("-")
        start = _dt.datetime(int(y), int(m), 1, tzinfo=tz)
    except (ValueError, AttributeError):
        raise ValueError("--month 는 YYYY-MM 형식이어야 합니다: %r" % month)
    end = _dt.datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=tz)
    return start, end


def _kind(t: _dt.datetime) -> str:
    if t.weekday() >= 5:
        return "weekend"
    if t.hour >= NIGHT_START or t.hour < NIGHT_END:
        return "night"
    if WORK_START <= t.hour < WORK_END:
        return "regular"
    return "overtime"


def compute(usages: List[wallet.Usage], month: str, prices: Mapping[str, Mapping[str, float]],
            tz: Optional[_dt.tzinfo] = None) -> List[SessionPay]:
    tz = tz or _dt.datetime.now().astimezone().tzinfo
    start, end = month_bounds(month, tz)
    by_sid: Dict[str, List[wallet.Usage]] = {}
    for u in usages:
        if u.ts is not None and start <= u.ts.astimezone(tz) < end:
            by_sid.setdefault(u.session, []).append(u)
    out: List[SessionPay] = []
    for sid, items in by_sid.items():
        items.sort(key=lambda u: u.ts)
        proj = next((u.project for u in items if u.project), "")
        sp = SessionPay(sid, os.path.basename(proj.rstrip("/\\")) if proj else "")
        for u in items:
            sp.tokens += u.total
            sp.cost += wallet.cost_of(u, prices)
        for a, b in zip(items, items[1:]):
            gap = b.ts - a.ts
            if gap > IDLE_GAP or gap <= _dt.timedelta(0):
                continue
            t = a.ts.astimezone(tz)
            mins = int(gap.total_seconds() // 60) or 0
            frac = gap.total_seconds() / 60 - mins
            for i in range(mins):
                k = _kind(t + _dt.timedelta(minutes=i))
                setattr(sp, k, getattr(sp, k) + 1 / 60)
            if frac > 0:
                k = _kind(t + _dt.timedelta(minutes=mins))
                setattr(sp, k, getattr(sp, k) + frac / 60)
        out.append(sp)
    out.sort(key=lambda s: -s.hours)
    return out


def _e(s: object) -> str:
    return html.escape(str(s), quote=True)


def _verdict(h: float) -> str:
    if h <= 0:
        return "이번 달 Claude 는 놀았습니다. 아니, 주인님이 일하셨군요."
    if h < 10:
        return "가벼운 알바 수준입니다. 아직 노동청에 갈 정도는 아닙니다."
    if h < 50:
        return "슬슬 점심도 거르고 일하는 중. 간식비 정도는 챙겨주세요."
    return "과로 경고. Claude 에게 연차를 신청해 주세요 (승인은 주인님 몫)."


def render_html(rows: List[SessionPay], month: str, prices_confirmed: bool = False) -> str:
    tot = SessionPay("합계", "")
    for r in rows:
        for f in ("regular", "overtime", "night", "weekend", "tokens", "cost"):
            setattr(tot, f, getattr(tot, f) + getattr(r, f))
    trs = "\n".join(
        "<tr><td>%s</td><td>%s</td><td class=n>%.1f</td><td class=n>%.1f</td><td class=n>%.1f</td>"
        "<td class=n>%.1f</td><td class=n>%s</td><td class=n>$%.2f</td></tr>" % (
            _e(r.session[:8]), _e(r.project or "-"), r.regular, r.overtime, r.night, r.weekend,
            wallet._fmt_tok(r.tokens), r.cost) for r in rows) or '<tr><td colspan=8>(기록 없음)</td></tr>'
    warn = "" if prices_confirmed else '<p class=warn>%s</p>' % _e(wallet.PRICE_WARNING)
    return """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>야근 수당 명세서 %(month)s</title>
<style>
body{font-family:system-ui,"Malgun Gothic",sans-serif;background:#f3efe4;color:#222;margin:0;padding:24px}
.slip{max-width:820px;margin:auto;background:#fff;border:2px solid #333;padding:24px 28px}
h1{margin:0 0 4px;text-align:center;letter-spacing:.3em}
.sub{text-align:center;color:#666;margin:0 0 18px}
table{width:100%%;border-collapse:collapse;font-size:14px}
th,td{border:1px solid #999;padding:6px 8px;text-align:left}th{background:#eee}
td.n{text-align:right;font-variant-numeric:tabular-nums}tfoot td{font-weight:700;background:#faf3d0}
.warn{color:#a40;font-weight:700}.note{font-size:13px;color:#555}
.stamp{display:inline-block;border:3px solid #c22;color:#c22;padding:4px 12px;transform:rotate(-6deg);font-weight:700;margin-top:12px}
@media(max-width:600px){body{padding:8px}.slip{padding:12px}table{font-size:12px}}
</style></head><body><div class="slip">
<h1>급 여 명 세 서</h1>
<p class="sub">%(month)s · 귀속: Claude Code (무인 근무) · 야근수당 명세</p>
%(warn)s
<table><thead><tr><th>세션</th><th>프로젝트</th><th>정규(h)</th><th>야근(h)</th><th>심야(h)</th><th>특근(h)</th><th>토큰</th><th>환산 청구액</th></tr></thead>
<tbody>
%(trs)s
</tbody>
<tfoot><tr><td colspan=2>합계</td><td class=n>%(r).1f</td><td class=n>%(o).1f</td><td class=n>%(n).1f</td><td class=n>%(w).1f</td><td class=n>%(t)s</td><td class=n>$%(c).2f</td></tr></tfoot></table>
<p>총 근로시간 <b>%(h).1f 시간</b> (정규 09~18시 평일 / 야근 그 외 / 심야 22~06시 / 특근 주말)</p>
<p>%(verdict)s</p>
<div class="stamp">지급 불능 (수령인이 AI)</div>
<p class="note">시간은 응답 기록 사이 %(gap)d분 이내의 간격만 이어 붙인 추정치이고, 금액은 로컬 로그 기반 단가표 환산 추정치입니다. 실제 청구액이 아닙니다. 농담용 문서입니다.</p>
</div></body></html>
""" % {"month": _e(month), "warn": warn, "trs": trs, "r": tot.regular, "o": tot.overtime, "n": tot.night,
       "w": tot.weekend, "t": wallet._fmt_tok(tot.tokens), "c": tot.cost, "h": tot.hours,
       "verdict": _e(_verdict(tot.hours)), "gap": int(IDLE_GAP.total_seconds() // 60)}
