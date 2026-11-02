"""sound-watch 어댑터: 밤사이(기본 22:00~06:00) 소음 이벤트와 노크/초인종 등 감지 기록.

형식(sound-watch/soundwatch/store.py): events(start epoch, duration, peak_dbfs, kind 'impact'|'sustained'),
detections(ts epoch, kind 'template'|'knock'|'beep'|'melody', name, message). DB 는 <data_dir>/soundwatch.db,
같은 폴더 config.json 에 night.start/end, utc_offset_minutes, calibration.offset_db 가 있다.
표시 dB = peak_dbfs + 120 + offset_db (config.NOMINAL_OFFSET_DB). 보정 전에는 상대값이라 낭독하지 않는다.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter

from ..model import Context, Item, SourceMissing
from ..speech import ko_time
from ..sqlro import open_ro

NAME = "sound_watch"
SECTION = "밤사이 소리"
DEFAULTS = {"enabled": True, "data_dir": "", "min_events": 1, "ignore_kinds": ["beep"], "nominal_offset_db": 120.0}


def _hhmm(s: str, default: int) -> int:
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return default


def window(sw_cfg: dict, today: dt.date) -> tuple[float, float, dt.timezone]:
    tz = dt.timezone(dt.timedelta(minutes=int(sw_cfg.get("utc_offset_minutes", 540))))
    night = sw_cfg.get("night") or {}
    s, e = _hhmm(night.get("start", "22:00"), 1320), _hhmm(night.get("end", "06:00"), 360)
    day0 = dt.datetime(today.year, today.month, today.day, tzinfo=tz)
    if s > e:   # 자정을 넘는 밤: 어제 시작 ~ 오늘 끝
        a, b = day0 - dt.timedelta(days=1) + dt.timedelta(minutes=s), day0 + dt.timedelta(minutes=e)
    else:
        a, b = day0 + dt.timedelta(minutes=s), day0 + dt.timedelta(minutes=e)
    return a.timestamp(), b.timestamp(), tz


def collect(cfg: dict, ctx: Context) -> list[Item]:
    raw = cfg.get("data_dir") or ctx.env.get("SOUNDWATCH_HOME")
    if not raw:
        raise SourceMissing("sources.sound_watch.data_dir (또는 SOUNDWATCH_HOME)를 설정하세요")
    folder = ctx.path(raw)
    sw_cfg = {}
    cfg_path = folder / "config.json"
    if cfg_path.is_file():
        try:
            sw_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except ValueError:
            sw_cfg = {}
    t0, t1, tz = window(sw_cfg, ctx.today)
    cal = sw_cfg.get("calibration") or {}
    calibrated = bool(cal.get("calibrated"))
    offset = float(cfg["nominal_offset_db"]) + float(cal.get("offset_db") or 0.0)
    with open_ro(folder / "soundwatch.db", "sound-watch DB") as con:
        events = con.execute("SELECT start, duration, peak_dbfs, kind FROM events WHERE start>=? AND start<? "
                             "ORDER BY start", (t0, t1)).fetchall()
        dets = con.execute("SELECT ts, kind, name FROM detections WHERE ts>=? AND ts<? ORDER BY ts",
                           (t0, t1)).fetchall()
    items: list[Item] = []
    n = len(events)
    if n >= int(cfg["min_events"]):
        loud = max(events, key=lambda r: (r["peak_dbfs"], -r["start"]))
        at = dt.datetime.fromtimestamp(loud["start"], tz)
        db = loud["peak_dbfs"] + offset
        impact = sum(1 for r in events if r["kind"] == "impact")
        detail = "큰 소리 %d번 (충격성 추정 %d번). 가장 큰 소리 %s %s" % (n, impact, at.strftime("%H:%M"),
                                                                     ("%.0f dB" % db) if calibrated else "미보정 상대값 %.0f" % db)
        items.append(Item(NAME, "noise", "밤사이 소음 %d번" % n, detail, 36 + (10 if n >= 5 else 0) + (10 if n >= 10 else 0),
                          "밤사이 소음 %d번, 가장 큰 소리는 %s" % (n, ko_time(at.hour, at.minute)),
                          "밤사이 소음 %d번" % n, None,
                          ("층간소음 기록 확인: sound-watch 리포트",)))
    skip = set(cfg.get("ignore_kinds") or [])
    counts = Counter((r["kind"], r["name"]) for r in dets if r["kind"] not in skip)
    if counts:
        parts = ["%s %d번" % (name, c) for (_, name), c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        total = sum(counts.values())
        items.append(Item(NAME, "sound-detect", "밤사이 감지: " + ", ".join(parts), "", 42,
                          "밤사이 감지: " + ", ".join(parts[:3]), "밤사이 감지 %d건" % total, None, ()))
    return items
