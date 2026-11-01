"""서비스 종료 D-day 경보."""
import json
import urllib.request
from dataclasses import dataclass

from . import classify as C


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
