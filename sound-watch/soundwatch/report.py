"""증거 스타일 리포트: 일별/주별 표, 시간대 분포, 타임라인(SVG), 야간(22-06) 집계, 상위 이벤트, CSV.

단일 HTML(인라인 SVG/CSS, 외부 요청 없음, 라이트/다크, 인쇄 가능). 모든 문자열은 html.escape 한다.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import io
import math
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, dsp
from .config import Calibration, parse_hhmm, tz_of

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
KIND_LABEL = {"impact": "충격성(추정)", "sustained": "지속성(추정)", "": "-"}


# ----------------------------------------------------------------------------- 시간 유틸
def is_night_minute(minute_of_day: int, start_min: int, end_min: int) -> bool:
    """야간 여부. start > end 면 자정을 넘는 구간(기본 22:00-06:00)."""
    if start_min > end_min:
        return minute_of_day >= start_min or minute_of_day < end_min
    return start_min <= minute_of_day < end_min


def local_dt(epoch: float, tz: dt.timezone) -> dt.datetime:
    return dt.datetime.fromtimestamp(epoch, tz)


def day_start_epoch(d: dt.date, tz: dt.timezone) -> float:
    return dt.datetime(d.year, d.month, d.day, tzinfo=tz).timestamp()


# ----------------------------------------------------------------------------- 데이터 구성
def build_report_data(store, cfg: Dict[str, Any], start_date: dt.date, end_date: dt.date,
                      top_n: int = 10, now: Optional[float] = None) -> Dict[str, Any]:
    tz = tz_of(cfg)
    cal = Calibration.from_cfg(cfg)
    ns = parse_hhmm(cfg["night"]["start"])
    ne = parse_hhmm(cfg["night"]["end"])
    t0 = day_start_epoch(start_date, tz)
    t1 = day_start_epoch(end_date + dt.timedelta(days=1), tz)
    thr_db = float(cfg["noise"]["threshold_db"])

    events = []
    for r in store.events_between(t0, t1):
        ld = local_dt(r["start"], tz)
        mod = ld.hour * 60 + ld.minute
        events.append({
            "id": r["id"], "start": r["start"], "local": ld, "date": ld.date(), "hour": ld.hour,
            "duration": r["duration"], "peak_db": cal.to_db(r["peak_dbfs"]), "leq_db": cal.to_db(r["leq_dbfs"]),
            "kind": r["kind"] or "", "night": is_night_minute(mod, ns, ne), "clip": r["clip"] or "",
            "segments": r["n_segments"],
        })

    days = []
    d = start_date
    while d <= end_date:
        days.append(d)
        d += dt.timedelta(days=1)
    drows = {d: {"date": d, "events": 0, "night": 0, "max_peak": None, "dur": 0.0, "mon_s": 0.0,
                 "max_leq1": None, "leq1s": []} for d in days}
    hourly_cov = {d: [0.0] * 24 for d in days}
    for m in store.minutes_between(t0, t1):
        ts = m["minute"] * 60
        if ts < t0 or ts >= t1:
            continue
        ld = local_dt(ts, tz)
        row = drows.get(ld.date())
        if row is None:
            continue
        secs = m["n"] * 0.1
        row["mon_s"] += secs
        hourly_cov[ld.date()][ld.hour] += secs
        if m["n"] >= 300:  # 30초 이상 담긴 분만 1분 Leq 로 인정
            lv = cal.to_db(dsp.power_to_db(m["sum_power"] / m["n"]))
            row["leq1s"].append(lv)
            row["max_leq1"] = lv if row["max_leq1"] is None else max(row["max_leq1"], lv)
    for e in events:
        row = drows[e["date"]]
        row["events"] += 1
        row["night"] += 1 if e["night"] else 0
        row["dur"] += e["duration"]
        row["max_peak"] = e["peak_db"] if row["max_peak"] is None else max(row["max_peak"], e["peak_db"])
    for row in drows.values():
        xs = sorted(row.pop("leq1s"))
        row["median_leq1"] = xs[len(xs) // 2] if xs else None

    hourly = [0] * 24
    hourly_night = [0] * 24
    for e in events:
        hourly[e["hour"]] += 1

    # ISO 주 단위 합계
    weeks: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for d in days:
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        w = weeks.setdefault(key, {"label": "%d-W%02d" % key, "first": d, "last": d, "events": 0, "night": 0,
                                   "max_peak": None, "dur": 0.0, "mon_s": 0.0, "days_with_data": 0})
        w["last"] = d
        row = drows[d]
        w["events"] += row["events"]
        w["night"] += row["night"]
        w["dur"] += row["dur"]
        w["mon_s"] += row["mon_s"]
        if row["max_peak"] is not None:
            w["max_peak"] = row["max_peak"] if w["max_peak"] is None else max(w["max_peak"], row["max_peak"])
        if row["mon_s"] > 0:
            w["days_with_data"] += 1

    top_peak = sorted(events, key=lambda e: -e["peak_db"])[:top_n]
    top_dur = sorted(events, key=lambda e: -e["duration"])[:min(5, top_n)]
    night_events = [e for e in events if e["night"]]
    detections = [r for r in store.detections_between(t0, t1)]

    data = {
        "start_date": start_date, "end_date": end_date, "tz": tz, "cfg": cfg, "cal": cal,
        "night_start": cfg["night"]["start"], "night_end": cfg["night"]["end"],
        "threshold_db": thr_db, "events": events, "days": [drows[d] for d in days], "hourly": hourly,
        "hourly_cov": hourly_cov, "weeks": [weeks[k] for k in sorted(weeks)],
        "top_peak": top_peak, "top_dur": top_dur, "night_events": night_events,
        "total_events": len(events), "total_night": len(night_events),
        "total_mon_s": sum(r["mon_s"] for r in drows.values()),
        "max_peak": max((e["peak_db"] for e in events), default=None),
        "total_dur": sum(e["duration"] for e in events),
        "detections": len(detections),
        "generated": local_dt(now if now is not None else __import__("time").time(), tz),
    }
    data["fingerprint"] = hashlib.sha256(events_csv(data).encode("utf-8")).hexdigest()[:16]
    return data


# ----------------------------------------------------------------------------- CSV
def _fmt_dt(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def events_csv(data: Dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    cal = data["cal"]
    w.writerow(["id", "start_local", "weekday", "duration_s", "peak_db", "leq_db", "kind", "night",
                "level_basis", "clip"])
    basis = "calibrated_offset_%+.1f" % cal.offset_db if cal.calibrated else "uncalibrated_relative"
    for e in data["events"]:
        w.writerow([e["id"], _fmt_dt(e["local"]), WEEKDAYS[e["local"].weekday()], "%.1f" % e["duration"],
                    "%.1f" % e["peak_db"], "%.1f" % e["leq_db"], e["kind"], 1 if e["night"] else 0, basis, e["clip"]])
    return buf.getvalue()


def daily_csv(data: Dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", "weekday", "monitored_hours", "events", "night_events", "max_peak_db",
                "max_1min_leq_db", "median_1min_leq_db", "total_event_s"])
    for r in data["days"]:
        w.writerow([r["date"].isoformat(), WEEKDAYS[r["date"].weekday()], "%.2f" % (r["mon_s"] / 3600.0),
                    r["events"], r["night"], "" if r["max_peak"] is None else "%.1f" % r["max_peak"],
                    "" if r["max_leq1"] is None else "%.1f" % r["max_leq1"],
                    "" if r["median_leq1"] is None else "%.1f" % r["median_leq1"], "%.1f" % r["dur"]])
    return buf.getvalue()


def write_csv(path: str, text: str):
    """엑셀(윈도우)에서 한글이 깨지지 않도록 UTF-8 BOM 으로 저장."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)


# ----------------------------------------------------------------------------- SVG
E = html.escape


def _nice_max(v: float) -> int:
    if v <= 20:
        return max(4, int(math.ceil(v / 4.0) * 4))  # 눈금이 정수가 되도록 4의 배수
    mag = 10 ** int(math.floor(math.log10(v)))
    for m in (1, 2, 5, 10):
        if v <= m * mag:
            return int(m * mag)
    return int(10 * mag)


def _svg(w: int, h: int, label: str, body: str, uid: str) -> str:
    return ('<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s" class="chart">'
            '<defs><pattern id="%s-h" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            '<line x1="0" y1="0" x2="0" y2="6" class="hatch"/></pattern></defs>%s</svg>') % (w, h, E(label), uid, body)


def _fmt_tick_date(d: dt.date) -> str:
    return "%02d-%02d" % (d.month, d.day)


def svg_timeline(data: Dict[str, Any]) -> str:
    """일별 24시간 띠: 행=날짜, 이벤트=세로 막대(높이=최고 레벨), 빗금=측정 안 된 시간."""
    days = [r["date"] for r in data["days"]]
    shown = days[-31:]
    left, right, top, rh, gap = 78, 12, 26, 20, 4
    W = 760
    pw = W - left - right
    H = top + len(shown) * (rh + gap) + 8
    ns, ne = parse_hhmm(data["night_start"]) / 60.0, parse_hhmm(data["night_end"]) / 60.0
    thr = data["threshold_db"]
    parts = []
    for h in range(0, 25, 3):
        x = left + pw * h / 24.0
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" class="grid"/>' % (x, top - 4, x, H - 8))
        parts.append('<text x="%.1f" y="%d" class="tick" text-anchor="middle">%02d</text>' % (x, top - 10, h % 24))
    byday: Dict[dt.date, list] = {}
    for e in data["events"]:
        byday.setdefault(e["date"], []).append(e)
    for i, d in enumerate(shown):
        y = top + i * (rh + gap)
        parts.append('<text x="%d" y="%d" class="tick" text-anchor="end">%s(%s)</text>' % (
            left - 6, y + rh - 6, _fmt_tick_date(d), WEEKDAYS[d.weekday()]))
        parts.append('<rect x="%d" y="%d" width="%d" height="%d" class="band"/>' % (left, y, pw, rh))
        # 야간 음영 (자정 넘김 처리)
        spans = [(ns, 24.0), (0.0, ne)] if ns > ne else [(ns, ne)]
        for a, b in spans:
            parts.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" class="nightbg"/>' % (
                left + pw * a / 24.0, y, pw * (b - a) / 24.0, rh))
        cov = data["hourly_cov"].get(d, [0.0] * 24)
        for h in range(24):
            if cov[h] < 1800:  # 해당 시간의 절반 미만만 측정됨
                parts.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="url(#tl-h)"/>' % (
                    left + pw * h / 24.0, y, pw / 24.0, rh))
        for e in byday.get(d, []):
            frac = (e["local"].hour * 3600 + e["local"].minute * 60 + e["local"].second) / 86400.0
            x = left + pw * frac
            wdt = max(2.0, pw * e["duration"] / 86400.0)
            hh = max(5.0, min(rh, 5.0 + (e["peak_db"] - thr) / 30.0 * (rh - 5)))
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="%s"><title>%s</title></rect>' % (
                x, y + rh - hh, wdt, hh, "ev-night" if e["night"] else "ev-day",
                E("%s · %.1f초 · 최고 %.1f dB" % (_fmt_dt(e["local"]), e["duration"], e["peak_db"]))))
    label = "일별 24시간 타임라인: 소음 이벤트 %d건" % len(data["events"])
    return _svg(W, H, label, "".join(parts), "tl")


def svg_hourly(data: Dict[str, Any]) -> str:
    W, H, left, bottom, top = 760, 230, 40, 30, 20
    pw, ph = W - left - 12, H - bottom - top
    vals = data["hourly"]
    ymax = _nice_max(max(vals) if vals else 0)
    ns, ne = parse_hhmm(data["night_start"]), parse_hhmm(data["night_end"])
    parts = []
    for k in range(0, 5):
        v = ymax * k / 4.0
        y = top + ph - ph * k / 4.0
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/>' % (left, y, W - 12, y))
        parts.append('<text x="%d" y="%.1f" class="tick" text-anchor="end">%g</text>' % (left - 6, y + 4, v))
    bw = pw / 24.0
    for h in range(24):
        night = is_night_minute(h * 60, ns, ne)
        v = vals[h]
        bh = ph * v / float(ymax)
        x = left + h * bw
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="%s"><title>%s</title></rect>' % (
            x + 2, top + ph - bh, bw - 4, bh, "ev-night" if night else "ev-day", E("%02d시: %d건" % (h, v))))
        if v:
            parts.append('<text x="%.1f" y="%.1f" class="tick" text-anchor="middle">%d</text>' % (x + bw / 2, top + ph - bh - 4, v))
        if h % 3 == 0:
            parts.append('<text x="%.1f" y="%d" class="tick" text-anchor="middle">%02d</text>' % (x + bw / 2, H - 10, h))
    return _svg(W, H, "시간대별 소음 이벤트 건수", "".join(parts), "hr")


def svg_daily(data: Dict[str, Any]) -> str:
    rows = data["days"][-31:]
    W, H, left, bottom, top = 760, 230, 40, 34, 20
    pw, ph = W - left - 12, H - bottom - top
    ymax = _nice_max(max((r["events"] for r in rows), default=0))
    parts = []
    for k in range(0, 5):
        y = top + ph - ph * k / 4.0
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/>' % (left, y, W - 12, y))
        parts.append('<text x="%d" y="%.1f" class="tick" text-anchor="end">%g</text>' % (left - 6, y + 4, ymax * k / 4.0))
    n = max(1, len(rows))
    bw = pw / float(n)
    every = 1 if n <= 14 else (2 if n <= 28 else 3)
    for i, r in enumerate(rows):
        x = left + i * bw
        day_ct = r["events"] - r["night"]
        h_day = ph * day_ct / float(ymax)
        h_night = ph * r["night"] / float(ymax)
        bx = x + max(1.0, bw * 0.12)
        bwid = bw * 0.76
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="ev-night"><title>%s</title></rect>' % (
            bx, top + ph - h_night, bwid, h_night, E("%s 야간 %d건" % (r["date"].isoformat(), r["night"]))))
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="ev-day"><title>%s</title></rect>' % (
            bx, top + ph - h_night - h_day, bwid, h_day, E("%s 주간 %d건" % (r["date"].isoformat(), day_ct))))
        if r["events"]:
            parts.append('<text x="%.1f" y="%.1f" class="tick" text-anchor="middle">%d</text>' % (
                x + bw / 2, top + ph - h_night - h_day - 4, r["events"]))
        if i % every == 0:
            parts.append('<text x="%.1f" y="%d" class="tick" text-anchor="middle">%s</text>' % (
                x + bw / 2, H - 14, _fmt_tick_date(r["date"])))
    return _svg(W, H, "일별 소음 이벤트 건수 (야간 구분)", "".join(parts), "dy")


def svg_scatter(data: Dict[str, Any]) -> str:
    ev = data["events"]
    W, H, left, bottom, top = 760, 260, 44, 34, 20
    pw, ph = W - left - 12, H - bottom - top
    t0 = day_start_epoch(data["start_date"], data["tz"])
    t1 = day_start_epoch(data["end_date"] + dt.timedelta(days=1), data["tz"])
    thr = data["threshold_db"]
    top_v = max([e["peak_db"] for e in ev] + [thr + 10])
    lo, hi = math.floor(thr - 5), math.ceil(top_v + 3)
    span = float(max(1, hi - lo))
    parts = []
    step = max(5, int(math.ceil(span / 5.0 / 5.0)) * 5)
    v = lo
    while v <= hi:
        y = top + ph - ph * (v - lo) / span
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/>' % (left, y, W - 12, y))
        parts.append('<text x="%d" y="%.1f" class="tick" text-anchor="end">%d</text>' % (left - 6, y + 4, v))
        v += step
    ythr = top + ph - ph * (thr - lo) / span
    parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="thr"/>' % (left, ythr, W - 12, ythr))
    parts.append('<text x="%d" y="%.1f" class="tick" text-anchor="end">기록 기준 %g</text>' % (W - 14, ythr - 4, thr))
    ndays = (data["end_date"] - data["start_date"]).days + 1
    every = max(1, int(math.ceil(ndays / 10.0)))
    for i in range(ndays):
        x = left + pw * (i * 86400.0) / (t1 - t0)
        if i % every == 0:
            d = data["start_date"] + dt.timedelta(days=i)
            parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" class="grid"/>' % (x, top, x, top + ph))
            parts.append('<text x="%.1f" y="%d" class="tick" text-anchor="middle">%s</text>' % (x, H - 14, _fmt_tick_date(d)))
    pts = ev if len(ev) <= 2000 else sorted(ev, key=lambda e: -e["peak_db"])[:2000]
    for e in pts:
        x = left + pw * (e["start"] - t0) / (t1 - t0)
        y = top + ph - ph * (e["peak_db"] - lo) / span
        parts.append('<circle cx="%.1f" cy="%.1f" r="3.2" class="%s"><title>%s</title></circle>' % (
            x, y, "pt-night" if e["night"] else "pt-day", E("%s · 최고 %.1f dB · %.1f초" % (_fmt_dt(e["local"]), e["peak_db"], e["duration"]))))
    return _svg(W, H, "이벤트별 최고 레벨 분포", "".join(parts), "sc")


# ----------------------------------------------------------------------------- HTML
CSS = """
:root{--bg:#fff;--fg:#1c2330;--muted:#5b6575;--line:#d5dae3;--panel:#f5f7fa;--warn-bg:#fff6e5;--warn-line:#e0a100;
--day:#2b6cdf;--night:#d1560b;--nightbg:rgba(209,86,11,.10);--hatch:#9aa3b2}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#10141b;--fg:#e6e9ef;--muted:#9aa5b6;--line:#2c3441;
--panel:#171c25;--warn-bg:#2a2210;--warn-line:#b88a00;--day:#6ea2ff;--night:#ff9a52;--nightbg:rgba(255,154,82,.12);--hatch:#5d677a}}
:root[data-theme="dark"]{--bg:#10141b;--fg:#e6e9ef;--muted:#9aa5b6;--line:#2c3441;--panel:#171c25;--warn-bg:#2a2210;
--warn-line:#b88a00;--day:#6ea2ff;--night:#ff9a52;--nightbg:rgba(255,154,82,.12);--hatch:#5d677a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 "Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.15rem;margin:1.8em 0 .5em;border-bottom:1px solid var(--line);padding-bottom:.2em}
.sub{color:var(--muted);margin:0 0 1em}
.notice{background:var(--warn-bg);border:1px solid var(--warn-line);border-left-width:5px;border-radius:6px;padding:10px 14px;margin:14px 0}
.notice ul{margin:.4em 0 0 1.1em;padding:0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.kpi b{display:block;font-size:1.35rem}.kpi span{color:var(--muted);font-size:.85rem}
table{border-collapse:collapse;width:100%;font-size:.88rem;margin:6px 0}
caption{caption-side:top;text-align:left;color:var(--muted);padding:2px 0}
th,td{border:1px solid var(--line);padding:4px 7px;text-align:right}th{background:var(--panel)}
td.l,th.l{text-align:left}tr.night td{background:var(--nightbg)}
.scroll{overflow-x:auto}
svg.chart{display:block;max-width:100%;height:auto;margin:6px 0}
.chart .tick{fill:var(--muted);font-size:10px}.chart .grid{stroke:var(--line);stroke-width:1}
.chart .band{fill:var(--panel)}.chart .nightbg{fill:var(--nightbg)}.chart .hatch{stroke:var(--hatch);stroke-width:1.5;opacity:.55}
.chart .ev-day,.chart .pt-day{fill:var(--day)}.chart .ev-night,.chart .pt-night{fill:var(--night)}
.chart .thr{stroke:var(--muted);stroke-dasharray:5 4;stroke-width:1}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:.85rem;color:var(--muted)}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.mono{font-family:ui-monospace,Consolas,monospace}
footer{margin-top:2.5em;color:var(--muted);font-size:.82rem;border-top:1px solid var(--line);padding-top:10px}
@media print{:root{--bg:#fff;--fg:#000;--muted:#444;--line:#999;--panel:#f2f2f2;--day:#1f4fb3;--night:#b34400}
body{font-size:12px}main{max-width:none;padding:0}svg.chart,table,.kpis{break-inside:avoid}h2{break-after:avoid}}
"""

LIMITATIONS = [
    ("측정 도구의 한계",
     "이 기록은 스마트폰 마이크와 소프트웨어로 계산한 값입니다. 스마트폰 마이크는 교정된 소음계(KS C IEC 61672 등 규격에 맞는 장비)가 아니며, "
     "마이크 감도·자동 이득 제어·주파수 특성·케이스/거치 위치·실내 반사에 따라 같은 소리도 값이 달라질 수 있습니다. "
     "A-가중치는 소프트웨어 필터로 근사한 것입니다. 보정하지 않은 값은 '상대값'이며 절대적인 dB(A)가 아닙니다."),
    ("보정(calibration)",
     "사용자가 실제 소음계와 비교해 입력한 오프셋(단일 값)을 일괄 더한 것입니다. 하나의 소리·하나의 음량에서 맞춘 값이므로 "
     "다른 음량·주파수의 소리에서는 오차가 남을 수 있고, 이 도구는 보정이 올바른지 검증하지 않습니다. 보정 값을 나중에 바꾸면 과거 기록의 표시 값도 함께 바뀝니다."),
    ("레벨 정의",
     "'최고 레벨'은 100 ms 프레임 에너지 평균의 최댓값(공식 측정의 Fast 시간 가중 최고소음도와 다를 수 있음), 'Leq'는 이벤트 구간의 에너지 평균, "
     "'1분 Leq'는 1분 동안의 프레임 에너지 평균입니다. 충격성/지속성 구분은 길이와 최고-평균 차이로 추정한 것으로 소음원의 종류를 확정하지 않습니다."),
    ("소음원 판별 불가",
     "마이크 하나로는 소리가 위층에서 왔는지, 같은 집의 생활 소음(가전·TV·대화·반려동물)인지, 집 밖 소음(도로·공사)인지 구분할 수 없습니다. "
     "기록된 이벤트에는 거주자 본인이 낸 소리가 포함될 수 있습니다."),
    ("기록 누락과 해석",
     "폰 절전, 네트워크 단절, 프로그램 중단 시에는 기록이 없습니다. 이벤트가 0건이라는 것은 '소음이 없었다'가 아니라 '이 도구가 기준을 넘는 소리를 기록하지 않았다'는 뜻일 수 있으므로 "
     "일별 표의 '측정 시간'을 함께 보세요. 타임라인의 빗금은 측정이 절반 미만이었던 시간대입니다."),
    ("공식 측정 및 절차",
     "공동주택 층간소음의 공식 측정과 판단은 관련 법령·기준에 따라 정해진 방법과 장비로 이루어지며, 층간소음 이웃사이센터와 같은 층간소음 관리 기관, "
     "관리주체(관리사무소), 층간소음관리위원회 등이 상담·측정·조정을 담당하는 것으로 알려져 있습니다. 이 리포트는 그 절차를 대체하지 않는 참고 자료이며, "
     "법적 효력이나 증거능력을 보장하지 않습니다. 기준 수치·절차·기관 안내는 개정될 수 있으니 이용 전에 최신 공식 안내를 직접 확인하세요."),
    ("개인정보와 녹음",
     "이 도구는 기본적으로 소리 높낮이(레벨)만 기록하고 녹음 파일을 저장하지 않습니다. 짧은 클립 저장은 직접 켠 경우에만 동작하며, 다른 사람의 대화를 동의 없이 녹음하면 "
     "법적 문제가 될 수 있으므로 사용 전에 관련 법령과 공동주택 규약을 확인해야 합니다."),
    ("데이터 무결성",
     "이 리포트는 로컬 데이터베이스의 내용을 그대로 집계한 것이며, 위·변조를 막는 장치는 없습니다. 아래 '데이터 지문'은 이벤트 목록의 해시로, 같은 CSV인지 대조하는 참고용입니다."),
]


def _f(v: Optional[float], nd: int = 1) -> str:
    return "-" if v is None else ("%." + str(nd) + "f") % v


def _dur(sec: float) -> str:
    if sec >= 60:
        return "%d분 %02d초" % (int(sec // 60), int(sec % 60))
    return "%.1f초" % sec


def render_html(data: Dict[str, Any]) -> str:
    cal: Calibration = data["cal"]
    cfg = data["cfg"]
    nz = cfg["noise"]
    ev = data["events"]
    sd, ed = data["start_date"], data["end_date"]
    unit = "dB(A)상당" + (" · 보정 %+.1f dB" % cal.offset_db if cal.calibrated else " · 미보정 상대값")
    h = []
    h.append("<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
             "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
             "<title>층간소음 기록 리포트 %s ~ %s</title><style>%s</style></head><body><main>" % (
                 E(sd.isoformat()), E(ed.isoformat()), CSS))
    h.append("<h1>층간소음 기록 리포트 <small>(참고용)</small></h1>")
    h.append("<p class=\"sub\">기간 %s ~ %s · 생성 %s · sound-watch %s</p>" % (
        E(sd.isoformat()), E(ed.isoformat()), E(_fmt_dt(data["generated"])), E(__version__)))
    h.append("<div class=\"notice\"><b>읽기 전에</b><ul>"
             "<li>스마트폰 마이크로 측정한 <b>%s</b> 값입니다. 교정된 소음계 측정값이 아닙니다.</li>"
             "<li>공식 측정·판단은 층간소음 이웃사이센터 등 관리 기관의 절차를 따릅니다. 이 자료는 참고용입니다. 자세한 한계는 문서 하단 '측정의 한계' 참고.</li></ul></div>" % E(unit))

    mon_h = data["total_mon_s"] / 3600.0
    h.append("<h2>1. 요약</h2><div class=\"kpis\">")
    for val, lab in (("%d건" % data["total_events"], "기록된 소음 이벤트"),
                     ("%d건" % data["total_night"], "야간(%s–%s) 이벤트" % (data["night_start"], data["night_end"])),
                     (_f(data["max_peak"]) + (" dB" if data["max_peak"] is not None else ""), "최고 레벨 (%s)" % ("보정 적용" if cal.calibrated else "상대값")),
                     (_dur(data["total_dur"]), "이벤트 지속 시간 합계"),
                     ("%.1f 시간" % mon_h, "측정(기록) 시간")):
        h.append("<div class=\"kpi\"><b>%s</b><span>%s</span></div>" % (E(val), E(lab)))
    h.append("</div>")
    h.append("<div class=\"scroll\"><table><caption>측정 설정</caption><tbody>"
             "<tr><th class=\"l\">기록 기준</th><td class=\"l\">%s 이상이 %s ms 이상 지속 (내려올 때 -%s dB 히스테리시스), %s ms 이내 간격은 하나로 병합</td></tr>"
             "<tr><th class=\"l\">레벨 표기</th><td class=\"l\">%s</td></tr>"
             "<tr><th class=\"l\">보정 기록</th><td class=\"l\">%s</td></tr>"
             "<tr><th class=\"l\">야간 정의</th><td class=\"l\">%s–%s (이벤트 시작 시각 기준)</td></tr>"
             "<tr><th class=\"l\">시간대</th><td class=\"l\">UTC%+d:%02d</td></tr>"
             "<tr><th class=\"l\">데이터 지문</th><td class=\"l mono\">%s</td></tr></tbody></table></div>" % (
                 E("%g dB" % data["threshold_db"]), E(str(nz["min_duration_ms"])), E(str(nz["hysteresis_db"])),
                 E(str(nz["merge_gap_ms"])), E(cal.label()),
                 E(("%s 설정 · 메모: %s" % (cal.set_at, cal.note)) if cal.calibrated else "보정 안 함 (절대값으로 해석 금지)"),
                 E(data["night_start"]), E(data["night_end"]),
                 int(data["tz"].utcoffset(None).total_seconds() // 3600), abs(int(data["tz"].utcoffset(None).total_seconds() // 60)) % 60,
                 E(data["fingerprint"])))

    h.append("<h2>2. 일별 표</h2><div class=\"scroll\"><table><caption>날짜별 집계 (야간 열은 이벤트 시작 시각이 야간인 건수)</caption>"
             "<thead><tr><th class=\"l\">날짜</th><th>측정 시간</th><th>이벤트</th><th>야간</th><th>최고 레벨</th>"
             "<th>최대 1분 Leq</th><th>1분 Leq 중앙값</th><th>이벤트 지속 합계</th></tr></thead><tbody>")
    for r in data["days"]:
        d = r["date"]
        h.append("<tr><td class=\"l\">%s (%s)</td><td>%s</td><td>%d</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            E(d.isoformat()), WEEKDAYS[d.weekday()],
            ("%.1f h" % (r["mon_s"] / 3600.0)) if r["mon_s"] > 0 else "기록 없음",
            r["events"], r["night"], _f(r["max_peak"]), _f(r["max_leq1"]), _f(r["median_leq1"]), _dur(r["dur"])))
    h.append("</tbody></table></div>")

    h.append("<h2>3. 주별 표</h2><div class=\"scroll\"><table><caption>ISO 주(월–일) 단위 합계</caption>"
             "<thead><tr><th class=\"l\">주</th><th class=\"l\">기간</th><th>측정 일수</th><th>측정 시간</th><th>이벤트</th><th>야간</th><th>최고 레벨</th><th>이벤트 지속 합계</th></tr></thead><tbody>")
    for w in data["weeks"]:
        h.append("<tr><td class=\"l\">%s</td><td class=\"l\">%s ~ %s</td><td>%d</td><td>%.1f h</td><td>%d</td><td>%d</td><td>%s</td><td>%s</td></tr>" % (
            E(w["label"]), _fmt_tick_date(w["first"]), _fmt_tick_date(w["last"]), w["days_with_data"],
            w["mon_s"] / 3600.0, w["events"], w["night"], _f(w["max_peak"]), _dur(w["dur"])))
    h.append("</tbody></table></div>")

    legend = ('<div class="legend"><span><i style="background:var(--day)"></i>주간</span>'
              '<span><i style="background:var(--night)"></i>야간(%s–%s)</span>'
              '<span>빗금: 측정 절반 미만</span></div>') % (E(data["night_start"]), E(data["night_end"]))
    h.append("<h2>4. 타임라인</h2>" + legend + "<p class=\"sub\">행 = 날짜, 가로 = 하루 24시간, 막대 높이 = 이벤트 최고 레벨(기록 기준 ~ +30 dB).</p>")
    h.append(svg_timeline(data))
    h.append("<h2>5. 이벤트별 최고 레벨</h2>" + legend + svg_scatter(data))
    h.append("<h2>6. 시간대별 분포</h2>" + legend + svg_hourly(data))
    h.append("<h2>7. 일별 건수</h2>" + legend + svg_daily(data))

    h.append("<h2>8. 야간(%s–%s) 이벤트</h2>" % (E(data["night_start"]), E(data["night_end"])))
    h.append("<p>야간 이벤트 <b>%d건</b> / 전체 %d건. 아래는 야간 이벤트 중 최고 레벨 순 상위 목록입니다.</p>" % (data["total_night"], data["total_events"]))
    h.append(_events_table(sorted(data["night_events"], key=lambda e: -e["peak_db"])[:20], "야간 상위 이벤트", rank=True))

    h.append("<h2>9. 상위 이벤트</h2>")
    h.append(_events_table(data["top_peak"], "최고 레벨 상위", rank=True))
    h.append(_events_table(data["top_dur"], "지속 시간 상위", rank=True))

    h.append("<h2>10. 전체 이벤트 목록</h2>")
    cap = 500
    h.append(_events_table(ev[:cap], "시간순 (최대 %d건 표시, 전체는 CSV)" % cap, rank=False))
    if len(ev) > cap:
        h.append("<p class=\"sub\">%d건 중 앞의 %d건만 표시했습니다.</p>" % (len(ev), cap))

    h.append("<h2>11. 측정의 한계와 유의사항</h2><ol>")
    for title, body in LIMITATIONS:
        h.append("<li><b>%s</b> — %s</li>" % (E(title), E(body)))
    h.append("</ol>")
    h.append("<footer>sound-watch %s · %s 생성 · 데이터 지문 <span class=\"mono\">%s</span><br>"
             "이 문서는 외부 서버로 어떤 정보도 보내지 않는 단일 HTML 파일입니다.</footer>" % (
                 E(__version__), E(_fmt_dt(data["generated"])), E(data["fingerprint"])))
    h.append("</main></body></html>")
    return "".join(h)


def _events_table(rows: List[Dict[str, Any]], caption: str, rank: bool) -> str:
    out = ["<div class=\"scroll\"><table><caption>%s</caption><thead><tr>" % E(caption)]
    if rank:
        out.append("<th>#</th>")
    out.append("<th class=\"l\">시작</th><th>지속</th><th>최고</th><th>Leq</th><th class=\"l\">구분</th><th>야간</th><th class=\"l\">클립</th></tr></thead><tbody>")
    if not rows:
        out.append("<tr><td colspan=\"%d\" class=\"l\">해당 이벤트 없음</td></tr>" % (8 if rank else 7))
    for i, e in enumerate(rows, 1):
        out.append("<tr%s>" % (" class=\"night\"" if e["night"] else ""))
        if rank:
            out.append("<td>%d</td>" % i)
        out.append("<td class=\"l\">%s (%s)</td><td>%s</td><td>%s</td><td>%s</td><td class=\"l\">%s</td><td>%s</td><td class=\"l\">%s</td></tr>" % (
            E(_fmt_dt(e["local"])), WEEKDAYS[e["local"].weekday()], _dur(e["duration"]), _f(e["peak_db"]), _f(e["leq_db"]),
            E(KIND_LABEL.get(e["kind"], e["kind"] or "-")), "예" if e["night"] else "", E(e["clip"] or "-")))
    out.append("</tbody></table></div>")
    return "".join(out)
