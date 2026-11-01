import datetime as dt
import os

from . import classify as C
from .alerts import dday_alerts, ntfy_send
from .config import load_games
from .dates import KST
from .ics import build_ics
from .schedule import build_entry
from .state import load_state, save_state
from .steam import SteamNewsFetcher
from .summarize import Summarizer
from .models import Entry


def render_digest(new_entries, alerts, today):
    L = ["# 게임 캘린더 다이제스트 - %s" % today.isoformat(), ""]
    if alerts:
        L.append("## 서비스 종료 경보")
        L += ["- " + a.message for a in alerts]
        L.append("")
    L.append("새 소식 %d건" % len(new_entries))
    L.append("")
    by_game = {}
    for e in new_entries:
        by_game.setdefault((e.game, e.appid), []).append(e)
    for (game, appid), es in sorted(by_game.items()):
        L.append("## %s (%d)" % (game, appid))
        for e in sorted(es, key=lambda x: (C.ALL.index(x.category), -x.post_ts)):
            L.append("### [%s] %s" % (e.category, e.title))
            L.append("- 일정: %s%s" % (e.when_text(), " (공지, 게시일 기준)" if e.is_notice else ""))
            if e.url:
                L.append("- 링크: " + e.url)
            if e.summary:
                note = "한국어 요약" if e.summary_mode == "llm" else "원문 발췌, 번역 없음"
                L.append("- 요약 (%s):" % note)
                L += ["  - " + s for s in e.summary]
            L.append("")
    return "\n".join(L).rstrip() + "\n"


def run(cfg, fetch=None, summarizer=None, notify=None, now=None, dry_run=False, out=print):
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(KST).date()
    games = load_games(cfg)
    if not games:
        out("추적할 게임이 없습니다. `python -m gamecal add <appid>` 로 추가하세요.")
    fetch = fetch or SteamNewsFetcher(cfg["cache_dir"], cfg["request_delay_sec"], cfg["cache_ttl_hours"])
    if summarizer is None:
        l = cfg["llm"]
        summarizer = Summarizer(l["base_url"], l["model"], enabled=l["enabled"] and not dry_run,
                                timeout=l.get("timeout", 60), api_key=l.get("api_key"))
    state = load_state(cfg["state_file"])
    new = []
    for appid, name in games.items():
        try:
            items = fetch(appid, cfg["news_count"])
        except Exception as ex:
            out("[경고] %s(%d) 뉴스 가져오기 실패: %s" % (name, appid, ex))
            continue
        for item in items:
            gid = str(item.get("gid"))
            if gid in state["seen"]:
                continue
            e = build_entry(appid, name, item, summarizer, cfg["default_tz"])
            state["seen"][gid] = {"appid": appid, "first_seen": now.isoformat()}
            state["entries"][gid] = e.to_dict()
            new.append(e)

    all_entries = [Entry.from_dict(d) for d in state["entries"].values()]
    alerts = dday_alerts(all_entries, today, cfg["dday"], state["alerts_sent"])
    keep_from = today - dt.timedelta(days=cfg["ics_keep_days"])
    cal = [e for e in all_entries if e.start_date_kst() >= keep_from]

    out("새 소식 %d건, 경보 %d건" % (len(new), len(alerts)))
    for a in alerts:
        out(a.message)
    ics_text = build_ics(cal, cfg["calendar_name"])
    digest = render_digest(new, alerts, today) if (new or alerts) else None
    if dry_run:
        out("[dry-run] 파일/상태를 저장하지 않습니다. ics 이벤트 %d개" % len(cal))
        if digest:
            out(digest)
        return {"new": new, "alerts": alerts, "ics": ics_text, "digest": digest}

    os.makedirs(cfg["out_dir"], exist_ok=True)
    with open(os.path.join(cfg["out_dir"], "gamecal.ics"), "w", encoding="utf-8", newline="") as f:
        f.write(ics_text)
    if digest:
        p = os.path.join(cfg["out_dir"], today.isoformat() + ".md")
        if os.path.exists(p):
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n---\n\n" + digest)
        else:
            with open(p, "w", encoding="utf-8") as f:
                f.write(digest)
    topic = (cfg.get("ntfy") or {}).get("topic")
    for a in alerts:
        if topic:
            try:
                (notify or (lambda t, m: ntfy_send(cfg["ntfy"]["server"], topic, t, m)))(
                    "게임 서비스 종료 " + a.label, a.message)
            except Exception as ex:
                out("[경고] ntfy 전송 실패: %s" % ex)
                continue  # 다음 실행에서 재시도
        state["alerts_sent"][a.key] = now.isoformat()
    save_state(cfg["state_file"], state)
    return {"new": new, "alerts": alerts, "ics": ics_text, "digest": digest}
