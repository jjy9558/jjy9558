"""전달: --speak 는 s21-sidekick /api/say, --push 는 ntfy. 실패해도 예외 대신 결과 문자열."""
from __future__ import annotations

import datetime as dt

from ._jjkit import notify
from .config import secret
from .model import Context
from .speech import ko_date


def speak(brief: dict, cfg: dict, ctx: Context) -> str:
    sc = cfg["notify"]["sidekick"]
    token = secret(sc, dict(ctx.env))
    if not token:
        return "건너뜀: sidekick 토큰이 없어요 (notify.sidekick.token_env=%s)" % sc.get("token_env")
    try:
        notify.sidekick_say(brief["speak_text"], token=token, url=sc["url"], module=sc.get("module", "morning-brief"),
                            opener=ctx.opener)
    except notify.NotifyError as e:
        return "오류: %s" % e
    return "ok"


def push_message(brief: dict, limit: int = 900) -> str:
    lines = []
    for sec in brief["sections"]:
        for it in sec["items"]:
            if it["spoken"] or it["priority"] >= 40:
                lines.append("- " + it["text"])
    out, total = [], 0
    for ln in lines:
        if total + len(ln) + 1 > limit:
            out.append("…")
            break
        out.append(ln)
        total += len(ln) + 1
    return "\n".join(out) or brief["speak_text"]


def push(brief: dict, cfg: dict, ctx: Context) -> str:
    nc = cfg["notify"]["ntfy"]
    if not nc.get("topic"):
        return "건너뜀: ntfy topic 이 없어요 (notify.ntfy.topic)"
    title = "아침 브리핑 " + ko_date(dt.date.fromisoformat(brief["date"]))
    try:
        notify.ntfy(nc["topic"], push_message(brief), title=title, priority=nc.get("priority") or None,
                    tags=["sunrise"], server=nc.get("server", "https://ntfy.sh"),
                    token=secret(nc, dict(ctx.env)), opener=ctx.opener)
    except notify.NotifyError as e:
        return "오류: %s" % e
    return "ok"
