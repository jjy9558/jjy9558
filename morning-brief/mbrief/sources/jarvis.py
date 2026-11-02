"""jarvis-hub 어댑터: GET /api/status 로 승인 대기, 밤사이 실패/완료 작업, 지켜볼 기기의 오프라인 여부.

형식(jarvis-hub/jarvis/hub.py ``Hub.status``): {now(epoch), counts{waiting_approval, queued, leased, done,
failed, cancelled}, devices[{id, label, worker, online}], jobs[{id, type, status, title, finished_at(epoch)}],
events[]}. 인증 헤더 X-Jarvis-Device / X-Jarvis-Token. 네트워크를 쓰므로 --dry-run 에서는 건너뛴다.
"""
from __future__ import annotations

from .._jjkit.netutil import NetError, request_json
from ..config import secret
from ..model import Context, Item, SourceMissing, SourceSkip
from ..speech import shorten

NAME = "jarvis"
SECTION = "자비스"
DEFAULTS = {"enabled": True, "url": "http://127.0.0.1:8800", "device": "", "token": "", "token_env": "JARVIS_TOKEN",
            "timeout": 5, "hours": 12, "exclude_types": ["notify.say"], "watch_devices": []}


def collect(cfg: dict, ctx: Context) -> list[Item]:
    if ctx.dry_run:
        raise SourceSkip("dry-run: 네트워크 호출을 하지 않아요")
    token = secret(cfg, dict(ctx.env))
    if not cfg.get("device") or not token:
        raise SourceMissing("sources.jarvis.device 와 토큰(JARVIS_TOKEN)을 설정하세요")
    try:
        _, data = request_json(cfg["url"].rstrip("/") + "/api/status",
                               headers={"X-Jarvis-Device": cfg["device"], "X-Jarvis-Token": token},
                               opener=ctx.opener, timeout=float(cfg["timeout"]))
    except NetError as e:
        raise RuntimeError("허브 호출 실패: %s" % e)
    if not isinstance(data, dict):
        raise ValueError("허브 응답이 JSON 객체가 아니에요")

    hub_now = data.get("now") if isinstance(data.get("now"), (int, float)) else ctx.now.timestamp()
    since = hub_now - float(cfg["hours"]) * 3600
    skip = set(cfg.get("exclude_types") or [])
    jobs = [j for j in data.get("jobs") or [] if isinstance(j, dict) and j.get("type") not in skip]
    counts = data.get("counts") or {}
    items: list[Item] = []

    waiting = [j for j in jobs if j.get("status") == "waiting_approval"]
    n_wait = max(int(counts.get("waiting_approval") or 0), len(waiting))
    if n_wait:
        titles = "\n".join("- " + ctx.red(shorten(str(j.get("title") or j.get("type")), 60)) for j in waiting)
        items.append(Item(NAME, "job-approval", "자비스 승인 대기 %d건" % n_wait, titles, 72,
                          "자비스에서 승인을 기다리는 작업이 %d건 있습니다" % n_wait, "자비스 승인 대기 %d건" % n_wait,
                          None, ("자비스 대시보드에서 승인 또는 거절",)))

    def recent(status):
        return [j for j in jobs if j.get("status") == status and (j.get("finished_at") or 0) >= since]

    failed, done = recent("failed"), recent("done")
    if failed:
        titles = "\n".join("- " + ctx.red(shorten(str(j.get("title") or j.get("type")), 60)) for j in failed)
        items.append(Item(NAME, "job-failed", "밤사이 자비스 작업 %d건 실패" % len(failed), titles, 58,
                          "밤사이 자비스 작업 %d건이 실패했습니다" % len(failed), "자비스 작업 %d건 실패" % len(failed),
                          None, ("실패 원인 확인: 자비스 대시보드",)))
    if done:
        titles = "\n".join("- " + ctx.red(shorten(str(j.get("title") or j.get("type")), 60)) for j in done[:8])
        items.append(Item(NAME, "jobs-done", "밤사이 자비스 작업 %d건 완료" % len(done), titles, 38,
                          "밤사이 자비스 작업 %d건 완료" % len(done), "", None, ()))
    watch = {str(w) for w in cfg.get("watch_devices") or []}
    for d in data.get("devices") or []:
        if isinstance(d, dict) and not d.get("online") and (d.get("id") in watch or d.get("label") in watch):
            label = ctx.red(d.get("label") or d.get("id"))
            items.append(Item(NAME, "device-offline", "%s 오프라인" % label, "", 28,
                              "기기 오프라인: %s" % label, "", None, ()))
    return items
