"""윈도우 업데이트 신호등 🟢🟡🔴.

알려진 문제 목록(known_issues.json)은 사용자가 직접 관리합니다.
pc-doctor는 KB 번호를 지어내지 않으며, 예제 파일은 KB0000000 같은 가짜 번호만 씁니다.
"""

import datetime as _dt
import os
import re

from . import config as cfgmod
from .parsers import as_list

SIGNAL = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
RANK = {"green": 0, "yellow": 1, "red": 2}
# IUpdateHistoryEntry.ResultCode: 2=성공, 3=일부 성공(오류), 4=실패, 5=중단
FAILED_CODES = {4, 5}
KB_RE = re.compile(r"\bKB\d{6,8}\b", re.IGNORECASE)


def known_issues_path():
    return os.path.join(cfgmod.data_dir(), "known_issues.json")


def load_known_issues(path=None):
    path = path or known_issues_path()
    if not os.path.exists(path):
        return None
    data = cfgmod.read_json(path)
    issues = data.get("issues", []) if isinstance(data, dict) else data
    out = []
    for i in issues:
        level = str(i.get("level", "yellow")).lower()
        if level not in ("yellow", "red"):
            level = "yellow"
        out.append({
            "kb": (i.get("kb") or "").upper(),
            "title_contains": i.get("title_contains") or "",
            "level": level,
            "summary": i.get("summary", ""),
            "workaround": i.get("workaround", ""),
            "source": i.get("source", ""),
        })
    return out


def collect(system, include_pending=True):
    hot = []
    for h in as_list(system.ps_json("hotfixes")):
        hot.append({"kb": (h.get("HotFixID") or "").upper(), "description": h.get("Description") or "",
                    "installed_on": h.get("InstalledOn")})
    hist = []
    for h in as_list(system.ps_json("update_history")):
        title = h.get("Title") or ""
        hist.append({"title": title, "kbs": [k.upper() for k in KB_RE.findall(title)],
                     "date": h.get("Date"), "result": h.get("ResultCode")})
    pending = None
    if include_pending:
        raw = system.ps_json("pending_updates")
        if raw is not None:
            pending = [{"title": p.get("Title") or "", "kbs": [k.upper() for k in as_list(p.get("KB"))]} for p in as_list(raw)]
    return {"hotfixes": hot, "history": hist, "pending": pending}


def _match(issue, kbs, title):
    if issue["kb"] and issue["kb"] in kbs:
        return True
    return bool(issue["title_contains"]) and issue["title_contains"].lower() in (title or "").lower()


def _date(s):
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def evaluate(data, issues, today=None, recent_days=45, stale_days=60):
    today = today or _dt.date.today()
    level, reasons = "green", []

    def bump(lv, msg):
        nonlocal level
        if RANK[lv] > RANK[level]:
            level = lv
        reasons.append((lv, msg))

    issues = issues or []
    recent = [h for h in data["hotfixes"] if _date(h["installed_on"]) and (today - _date(h["installed_on"])).days <= recent_days]
    for h in recent:
        for i in issues:
            if _match(i, [h["kb"]], h["description"]):
                bump(i["level"], "설치됨 %s (%s): %s%s" % (h["kb"], h["installed_on"], i["summary"],
                                                         " → 해결: " + i["workaround"] if i["workaround"] else ""))
    for p in data["pending"] or []:
        for i in issues:
            if _match(i, p["kbs"], p["title"]):
                bump(i["level"], "대기 중 %s: %s — %s" % (", ".join(p["kbs"]) or p["title"], i["summary"],
                                                     "설치 보류 권장" if i["level"] == "red" else "주의해서 설치"))
    failed = [h for h in data["history"] if h["result"] in FAILED_CODES and _date(h["date"]) and (today - _date(h["date"])).days <= recent_days]
    if failed:
        bump("yellow", "최근 설치 실패 %d건: %s" % (len(failed), "; ".join(h["title"] for h in failed[:3])))
    dates = [d for d in (_date(h["installed_on"]) for h in data["hotfixes"]) if d]
    if dates and (today - max(dates)).days > stale_days:
        bump("yellow", "마지막 업데이트 설치가 %d일 전입니다 (보안 업데이트 확인 권장)" % (today - max(dates)).days)
    return {"level": level, "signal": SIGNAL[level], "reasons": reasons, "recent": recent}


def format_report(data, ev, issues_loaded):
    lines = ["%s 윈도우 업데이트 신호등: %s" % (ev["signal"], {"green": "설치해도 무난", "yellow": "주의",
                                                         "red": "보류 권장 / 문제 확인"}[ev["level"]])]
    for lv, msg in ev["reasons"]:
        lines.append("  %s %s" % (SIGNAL[lv], msg))
    if issues_loaded is None:
        lines.append("  ℹ️  알려진 문제 목록이 없습니다 → 실패 기록·업데이트 경과일만 봤습니다. (`pc-doctor init` 후 known_issues.json 편집)")
    lines.append("")
    lines.append("최근 설치된 업데이트:")
    for h in ev["recent"][:15] or []:
        lines.append("  - %s  %s  %s" % (h["installed_on"], h["kb"], h["description"]))
    if not ev["recent"]:
        lines.append("  (최근 설치 기록 없음)")
    lines.append("")
    if data["pending"] is None:
        lines.append("대기 중인 업데이트: 감지 불가 (Windows Update 서비스/네트워크/정책 문제일 수 있음)")
    else:
        lines.append("대기 중인 업데이트: %d개" % len(data["pending"]))
        for p in data["pending"]:
            lines.append("  - %s" % p["title"])
    return lines
