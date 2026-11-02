"""Work ledger (JSON): per-job media minutes, time spent, effective hourly wage, vs quote."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Optional


class LedgerError(RuntimeError):
    pass


def _load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError
        return data
    except ValueError as e:
        raise LedgerError("장부 파일이 손상되었습니다: " + path) from e


def _save(path: str, jobs: List[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _now(at: Optional[str]) -> datetime:
    return datetime.fromisoformat(at) if at else datetime.now().replace(microsecond=0)


def start(path: str, name: str, media_minutes: Optional[float] = None, quote: Optional[int] = None,
          est_hours: Optional[float] = None, at: Optional[str] = None) -> dict:
    jobs = _load(path)
    if any(j.get("end") is None for j in jobs):
        raise LedgerError("이미 진행 중인 작업이 있습니다: " + [j for j in jobs if j.get("end") is None][0]["name"])
    job = {"name": name, "start": _now(at).isoformat(), "end": None, "media_minutes": media_minutes,
           "quote": quote, "est_hours": est_hours}
    jobs.append(job)
    _save(path, jobs)
    return job


def stop(path: str, at: Optional[str] = None, media_minutes: Optional[float] = None,
         quote: Optional[int] = None) -> dict:
    jobs = _load(path)
    open_jobs = [j for j in jobs if j.get("end") is None]
    if not open_jobs:
        raise LedgerError("진행 중인 작업이 없습니다")
    j = open_jobs[-1]
    end = _now(at)
    if end < datetime.fromisoformat(j["start"]):
        raise LedgerError("종료 시각이 시작보다 빠릅니다")
    j["end"] = end.isoformat()
    if media_minutes is not None:
        j["media_minutes"] = media_minutes
    if quote is not None:
        j["quote"] = quote
    _save(path, jobs)
    return j


def hours_of(j: dict) -> float:
    return (datetime.fromisoformat(j["end"]) - datetime.fromisoformat(j["start"])).total_seconds() / 3600.0


def stats(j: dict) -> dict:
    h = hours_of(j)
    q, m, est = j.get("quote"), j.get("media_minutes"), j.get("est_hours")
    return {"name": j["name"], "hours": h, "media_minutes": m, "quote": q,
            "hourly": (q / h) if q is not None and h > 0 else None,
            "work_per_media_min": (h * 60 / m) if m and h > 0 else None,  # 작업 분 / 영상 분
            "est_hours": est, "vs_est": (h / est) if est else None}


def report(path: str, target_hourly: Optional[int] = None) -> str:
    jobs = _load(path)
    done = [stats(j) for j in jobs if j.get("end")]
    running = [j for j in jobs if not j.get("end")]
    out = ["# 작업 장부 리포트", "",
           "| 작업 | 분량(분) | 소요(시간) | 견적(원) | 실질 시급(원) | 영상 1분당 작업(분) | 견적 시간 대비 |",
           "|---|---|---|---|---|---|---|"]
    for s in done:
        out.append("| %s | %s | %.2f | %s | %s | %s | %s |" % (
            s["name"], "-" if s["media_minutes"] is None else "%g" % s["media_minutes"], s["hours"],
            "-" if s["quote"] is None else "{:,}".format(s["quote"]),
            "-" if s["hourly"] is None else "{:,}".format(int(round(s["hourly"]))),
            "-" if s["work_per_media_min"] is None else "%.1f" % s["work_per_media_min"],
            "-" if s["vs_est"] is None else "%d%%" % round(s["vs_est"] * 100)))
    tq = sum(s["quote"] for s in done if s["quote"] is not None)
    th = sum(s["hours"] for s in done if s["quote"] is not None)
    if th > 0:
        avg = tq / th
        line = "- 견적 있는 작업 합계: %s원 / %.2f시간 = 실질 시급 %s원" % (
            "{:,}".format(tq), th, "{:,}".format(int(round(avg))))
        if target_hourly:
            line += " (목표 %s원의 %d%%)" % ("{:,}".format(target_hourly), round(avg / target_hourly * 100))
        out += ["", line]
    if not done:
        out += ["", "- 완료된 작업이 없습니다"]
    for j in running:
        out.append("- 진행 중: %s (시작 %s)" % (j["name"], j["start"]))
    return "\n".join(out) + "\n"
