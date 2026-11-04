"""서비스 종료 D-day 경보."""
import datetime as dt
import json
import urllib.request
from dataclasses import dataclass

from . import classify as C
from .contract import SCHEMA_VERSION
from .dates import KST


@dataclass
class Alert:
    entry: object
    days: int
    key: str

    @property
    def label(self):
        return "D-DAY" if self.days == 0 else "D-%d" % self.days

    @property
    def message(self):
        e = self.entry
        return "[%s] %s 서비스 종료 %s - %s" % (self.label, e.game, e.start_date_kst().isoformat(), e.title)


def dday_alerts(entries, today, thresholds=(30, 7, 1), sent=()):
    """일정이 확인된 서비스 종료 항목에 대해, 아직 보내지 않은 가장 가까운 임계값 경보를 반환.
    (실행을 놓쳐도 D-30 이내에 들어오면 한 번은 알림)"""
    out = []
    for e in entries:
        if e.category != C.SHUTDOWN or e.is_notice:
            continue
        days = (e.start_date_kst() - today).days
        if days < 0:
            continue
        if days == 0:
            key = "%s:D-0" % e.gid
        else:
            cands = [t for t in thresholds if t >= days]
            if not cands:
                continue
            key = "%s:D-%d" % (e.gid, min(cands))
        if key in sent:
            continue
        out.append(Alert(e, days, key))
    return out


def ntfy_send(server, topic, title, message, opener=None):
    """ntfy JSON publish (비ASCII 헤더 문제 회피)."""
    body = json.dumps({"topic": topic, "title": title, "message": message,
                       "tags": ["warning"], "priority": 4}).encode("utf-8")
    req = urllib.request.Request(server.rstrip("/") + "/", data=body,
                                 headers={"Content-Type": "application/json"})
    (opener or urllib.request.build_opener()).open(req, timeout=15).read()


# ---- `alerts --json` (다른 도구 연동용, 네트워크 사용 없음) ----

ITEM_KIND = {C.MAINT: "maintenance", C.EVENT: "event", C.SHUTDOWN: "shutdown", C.PATCH: "patch"}
JSON_WINDOW_DAYS = 14
SHUTDOWN_WINDOW_DAYS = 60


def _kst_start(e):
    if e.all_day:
        return dt.datetime(e.start.year, e.start.month, e.start.day, tzinfo=KST)
    return e.start.astimezone(KST)


def _kst_end(e):
    """all_day: 배타적 종료 (마지막 날 다음 날 00:00). 시각 일정: 종료 시각 또는 None."""
    if e.all_day:
        last = e.end or e.start
        return dt.datetime(last.year, last.month, last.day, tzinfo=KST) + dt.timedelta(days=1)
    return e.end.astimezone(KST) if e.end is not None else None


def _iso(d):
    return d.isoformat(timespec="seconds") if d is not None else None


def upcoming_items(entries, today, days=JSON_WINDOW_DAYS, shutdown_days=SHUTDOWN_WINDOW_DAYS):
    """일정이 확인된 항목을 시작 순으로 반환합니다.
    - 시작일이 today ~ today+N일 (서비스 종료는 today ~ today+shutdown_days)
    - 진행 중(ongoing): 시작일이 today보다 앞이고, 배타적 종료 시각이 오늘 00:00(KST)보다 뒤이며
      종료일이 today+N일 이내인 항목 (서비스 종료는 진행 중 규칙 적용 안 함)
    공지(is_notice)는 일정이 없으므로 제외합니다."""
    midnight = dt.datetime(today.year, today.month, today.day, tzinfo=KST)
    picked = []
    for e in entries:
        kind = ITEM_KIND.get(e.category)
        if kind is None or e.is_notice or e.start is None:
            continue
        left = (e.start_date_kst() - today).days
        end = _kst_end(e)
        ends_in = (end.date() - today).days if end is not None else None
        if e.category == C.SHUTDOWN:
            if not 0 <= left <= shutdown_days:
                continue
            ongoing = False
        elif left >= 0:
            if left > days:
                continue
            ongoing = False
        else:
            if end is None or end <= midnight or ends_in > days:
                continue
            ongoing = True
        picked.append((_kst_start(e), e, kind, left, ends_in, ongoing))
    picked.sort(key=lambda p: (p[0], p[1].game, p[1].title, p[1].gid))
    return [{"kind": kind, "game": e.game, "appid": int(e.appid), "title": e.title,
             "start": _iso(start), "end": _iso(_kst_end(e)), "all_day": bool(e.all_day),
             "d_day": left, "ends_in_days": ends_in, "ongoing": ongoing,
             "url": e.url or None}
            for start, e, kind, left, ends_in, ongoing in picked]


def alerts_payload(entries, today, days=JSON_WINDOW_DAYS, shutdown_days=SHUTDOWN_WINDOW_DAYS):
    return {"version": SCHEMA_VERSION, "generated_for": today.isoformat(),
            "items": upcoming_items(entries, today, days, shutdown_days)}
