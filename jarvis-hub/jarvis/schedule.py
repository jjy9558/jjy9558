"""오늘 일정: JSON 또는 아주 단순한 .ics(VEVENT의 DTSTART/SUMMARY)에서 읽는다.

JSON 형식: {"events": [{"date": "2026-11-02", "time": "14:00", "title": "치과"}, ...]} 또는 목록 그대로.
date가 없으면 매일 반복으로 본다. (구글 캘린더는 '비공개 iCal 주소'를 미니PC가 주기적으로 받아 두면 됨)
"""
import datetime as _dt
import json
import os
import re


def _ics_events(text):
    text = re.sub(r"\r?\n[ \t]", "", text)  # 줄 접기 해제
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        start = re.search(r"^DTSTART[^:\n]*:(\d{8})(?:T(\d{2})(\d{2}))?", block, re.M)
        summ = re.search(r"^SUMMARY[^:\n]*:(.*)$", block, re.M)
        if not start:
            continue
        d = start.group(1)
        out.append({"date": "%s-%s-%s" % (d[:4], d[4:6], d[6:8]),
                    "time": "%s:%s" % (start.group(2), start.group(3)) if start.group(2) else "",
                    "title": (summ.group(1).strip() if summ else "(제목 없음)").replace("\\,", ",")})
    return out


def load_events(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if path.lower().endswith(".ics"):
        return _ics_events(text)
    data = json.loads(text or "[]")
    if isinstance(data, dict):
        data = data.get("events", [])
    return [e for e in data if isinstance(e, dict) and e.get("title")]


def today_events(path, today=None):
    """None = 설정 안 됨/파일 없음, [] = 오늘 일정 없음."""
    events = load_events(path)
    if events is None:
        return None
    day = (today or _dt.date.today()).isoformat()
    items = [e for e in events if not e.get("date") or e.get("date") == day]
    return sorted(items, key=lambda e: e.get("time") or "99:99")


def describe(events):
    if events is None:
        return "일정 파일이 설정되지 않았어요."
    if not events:
        return "오늘 일정은 없어요."
    parts = []
    for e in events[:5]:
        t = e.get("time") or ""
        parts.append(("%s %s" % (t, e["title"])).strip())
    more = " 외 %d개" % (len(events) - 5) if len(events) > 5 else ""
    return "오늘 일정 %d개: %s%s." % (len(events), ", ".join(parts), more)
