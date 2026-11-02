"""세션 그룹핑 및 한국어 일기(Markdown) 생성."""
import os
import urllib.parse
from collections import Counter, OrderedDict
from datetime import datetime, timedelta

WEEKDAYS = "월화수목금토일"


def group_sessions(shots, gap_hours=2.0):
    """게임별로 정렬 후 촬영 간격 > gap_hours 면 새 세션. 시작 시각순 반환."""
    by_game = {}
    for s in shots:
        by_game.setdefault(s["game"], []).append(s)
    sessions = []
    gap = timedelta(hours=gap_hours)
    for game, items in by_game.items():
        items.sort(key=lambda s: s["ts"])
        cur = [items[0]]
        for s in items[1:]:
            if s["ts"] - cur[-1]["ts"] > gap:
                sessions.append({"game": game, "shots": cur})
                cur = []
            cur.append(s)
        sessions.append({"game": game, "shots": cur})
    for ses in sessions:
        ses["start"] = ses["shots"][0]["ts"]
        ses["end"] = ses["shots"][-1]["ts"]
    sessions.sort(key=lambda x: (x["start"], x["game"]))
    return sessions


def _spread(items, n):
    if len(items) <= n:
        return list(items)
    step = (len(items) - 1) / float(n - 1) if n > 1 else 0
    return [items[int(round(i * step))] for i in range(n)]


def pick_highlights(shots, lo=3, hi=5):
    """notable 우선 최대 hi장. notable 이 lo장 미만이면 나머지 장면으로 보충."""
    notable = [s for s in shots if s.get("notable")]
    picked = _spread(notable, hi)
    want = min(lo, len(shots))
    if len(picked) < want:
        ids = set(id(s) for s in picked)
        rest = [s for s in shots if id(s) not in ids and s.get("caption")] or \
               [s for s in shots if id(s) not in ids]
        picked += _spread(rest, want - len(picked))
    return sorted(picked, key=lambda s: s["ts"])


def fmt_duration(a, b):
    mins = int((b - a).total_seconds() // 60)
    if mins < 1:
        return "잠깐"
    h, m = divmod(mins, 60)
    return ("%d시간 " % h if h else "") + ("%d분" % m if m or not h else "").strip()


def template_narrative(ses):
    shots = ses["shots"]
    parts = ["%s 세션: %s부터 %s까지(%s) 스크린샷 %d장을 남겼다." % (
        ses["game"], ses["start"].strftime("%H:%M"), ses["end"].strftime("%H:%M"),
        fmt_duration(ses["start"], ses["end"]), len(shots))]
    moods = [s["mood"] for s in shots if s.get("mood")]
    if moods:
        parts.append("분위기는 주로 " + ", ".join(m for m, _ in Counter(moods).most_common(2)) + ".")
    tags = [t for s in shots for t in s.get("tags", [])]
    if tags:
        parts.append("주요 태그: " + ", ".join("#" + t for t, _ in Counter(tags).most_common(4)) + ".")
    hl = [s["caption"] for s in pick_highlights(shots) if s.get("caption")][:3]
    if hl:
        parts.append("기억에 남는 장면: " + " / ".join(hl))
    return " ".join(parts)


def llm_narrative(client, ses):
    caps = [s["caption"] for s in _spread([s for s in ses["shots"] if s.get("caption")], 30)]
    if not caps:
        return None
    prompt = ("다음은 게임 '%s' 플레이 세션(%s ~ %s)에서 찍은 스크린샷 캡션을 시간순으로 나열한 것입니다.\n"
              "이를 바탕으로 1인칭 일기체 한국어로 2~4문장 요약을 쓰세요. 캡션에 없는 사실은 지어내지 마세요.\n\n%s" % (
                  ses["game"], ses["start"].strftime("%H:%M"), ses["end"].strftime("%H:%M"),
                  "\n".join("- " + c for c in caps)))
    text = client.chat([{"role": "user", "content": prompt}], max_tokens=300, temperature=0.5)
    text = (text or "").strip()
    return text or None


def narrative(ses, client=None):
    if client is not None:
        try:
            t = llm_narrative(client, ses)
            if t:
                return t
        except Exception:
            pass
    return template_narrative(ses)


def image_ref(path, out_dir):
    if out_dir:
        try:
            rel = os.path.relpath(path, out_dir).replace(os.sep, "/")
            return urllib.parse.quote(rel, safe="/")
        except ValueError:
            pass
    import pathlib
    return pathlib.Path(os.path.abspath(path)).as_uri()


def _md_escape(s):
    return s.replace("\n", " ").replace("[", "\\[").replace("]", "\\]")


def week_range(day):
    mon = day - timedelta(days=day.weekday())
    return mon, mon + timedelta(days=6)


def render_diary(shots, start, end, client=None, out_dir=None, gap_hours=2.0, weekly=False):
    """start/end 는 date (포함). Markdown 문자열 반환."""
    sel = [s for s in shots if start <= s["ts"].date() <= end]
    if weekly:
        title = "주간 게임 일기 (%s ~ %s)" % (start.isoformat(), end.isoformat())
    else:
        title = "게임 일기 %s (%s)" % (start.isoformat(), WEEKDAYS[start.weekday()])
    lines = ["# " + title, ""]
    if not sel:
        lines.append("이 기간에는 기록된 스크린샷이 없습니다.")
        return "\n".join(lines) + "\n"
    sessions = group_sessions(sel, gap_hours)
    total = Counter()
    for ses in sessions:
        total[ses["game"]] += len(ses["shots"])
    lines.append("스크린샷 %d장, 세션 %d개, 게임 %d종 (%s)" % (
        len(sel), len(sessions), len(total),
        ", ".join("%s %d장" % (g, n) for g, n in total.most_common())))
    lines.append("")
    days = OrderedDict()
    for ses in sessions:
        days.setdefault(ses["start"].date(), []).append(ses)
    for day, sess in days.items():
        if weekly:
            lines += ["## %s (%s)" % (day.isoformat(), WEEKDAYS[day.weekday()]), ""]
        for ses in sess:
            h = "###" if weekly else "##"
            lines.append("%s %s · %s–%s (%d장)" % (
                h, ses["game"], ses["start"].strftime("%H:%M"), ses["end"].strftime("%H:%M"),
                len(ses["shots"])))
            lines += ["", narrative(ses, client), "", "**하이라이트**", ""]
            for s in pick_highlights(ses["shots"]):
                cap = _md_escape(s.get("caption") or "(캡션 없음)")
                lines.append("- %s %s" % (s["ts"].strftime("%H:%M"), cap))
                lines.append("")
                lines.append("  ![%s](%s)" % (cap, image_ref(s["path"], out_dir)))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
