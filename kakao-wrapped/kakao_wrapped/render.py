"""Render the statistics as a single self-contained HTML file (inline CSS/JS)."""

from __future__ import annotations

import math
from datetime import date
from html import escape as _esc
from typing import Any, Dict, List, Optional, Sequence

from . import __version__

WEEKDAYS = "월화수목금토일"
MEDALS = ["🥇", "🥈", "🥉", "4", "5", "6", "7", "8", "9", "10"]


def e(x: Any) -> str:
    return _esc(str(x), quote=True)


def num(n: Any) -> str:
    try:
        return "{:,}".format(int(round(float(n))))
    except (TypeError, ValueError):
        return e(n)


def fmt_dur(sec: float) -> str:
    """Human friendly Korean duration. Exports have minute precision."""
    sec = int(round(sec))
    if sec < 60:
        return "1분 미만"
    if sec < 3600:
        m, s = divmod(sec, 60)
        return "%d분 %d초" % (m, s) if s else "%d분" % m
    if sec < 2 * 86400:
        h, rem = divmod(sec, 3600)
        m = rem // 60
        return "%d시간 %d분" % (h, m) if m else "%d시간" % h
    d, rem = divmod(sec, 86400)
    h = rem // 3600
    return "%d일 %d시간" % (d, h) if h else "%d일" % d


def fmt_date(iso: str, with_weekday: bool = True) -> str:
    d = date.fromisoformat(iso[:10])
    s = "%d월 %d일" % (d.month, d.day)
    return s + " %s요일" % WEEKDAYS[d.weekday()] if with_weekday else s


def fmt_dt(ts: str) -> str:
    d = date.fromisoformat(ts[:10])
    return "%d월 %d일 %s" % (d.month, d.day, ts[11:16])


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------

def bars(rows: Sequence[Dict[str, Any]], vmax: Optional[float] = None, wide: bool = False) -> str:
    """rows: dicts with label, value (float), text (display), optional top (bool)."""
    if not rows:
        return ""
    vmax = vmax if vmax is not None else max(r["value"] for r in rows) or 1
    out = ['<div class="bars%s">' % (" wide" if wide else "")]
    for i, r in enumerate(rows):
        w = max(2.0, 100.0 * r["value"] / vmax) if r["value"] > 0 else 0
        medal = MEDALS[i] if i < 3 else ""
        out.append(
            '<div class="bar%s reveal" style="--d:%dms"><span class="lb"><em>%s</em>%s</span>'
            '<span class="tr"><i style="--w:%.1f%%"></i></span><span class="vl">%s</span></div>'
            % (" first" if i == 0 else "", i * 70, medal, e(r["label"]), w, e(r["text"]))
        )
    out.append("</div>")
    return "".join(out)


def chips(words: Sequence[Sequence[Any]]) -> str:
    if not words:
        return '<p class="muted">아직 말버릇이 없어요</p>'
    vmax = max(c for _, c in words) or 1
    out = ['<div class="chips">']
    for w, c in words:
        size = 0.95 + 0.55 * (c / vmax)
        out.append('<span class="chip" style="font-size:%.2frem">%s<small>%s</small></span>' % (size, e(w), num(c)))
    out.append("</div>")
    return "".join(out)


def month_chart(months: Sequence[int], best: int) -> str:
    vmax = max(months) or 1
    out = ['<div class="months" role="img" aria-label="월별 메시지 수">']
    for i, v in enumerate(months, 1):
        out.append(
            '<div class="mcol%s"><span class="mv">%s</span><i style="--ht:%.1f%%"></i><b>%d</b></div>'
            % (" best" if i == best else "", num(v) if i == best else "", 100.0 * v / vmax, i)
        )
    out.append("</div>")
    return "".join(out)


def heatmap(heat: Sequence[Sequence[int]]) -> str:
    cell, gap, left, top = 13, 2, 18, 4
    vmax = max(max(r) for r in heat) or 1
    width = left + 24 * (cell + gap)
    height = top + 7 * (cell + gap) + 16
    parts = [
        '<svg class="heat" viewBox="0 0 %d %d" role="img" aria-label="요일별 시간대별 메시지 히트맵">' % (width, height)
    ]
    for r in range(7):
        y = top + r * (cell + gap)
        parts.append('<text x="%d" y="%d" class="tx" text-anchor="middle">%s</text>' % (left / 2 - 1, y + cell - 3, WEEKDAYS[r]))
        for h in range(24):
            v = heat[r][h]
            x = left + h * (cell + gap)
            if v == 0:
                parts.append('<rect x="%d" y="%d" width="%d" height="%d" rx="3" class="z"></rect>' % (x, y, cell, cell))
            else:
                op = 0.18 + 0.82 * math.sqrt(v / vmax)
                parts.append(
                    '<rect x="%d" y="%d" width="%d" height="%d" rx="3" class="hc" fill-opacity="%.2f">'
                    "<title>%s요일 %d시 · %s개</title></rect>" % (x, y, cell, cell, op, WEEKDAYS[r], h, num(v))
                )
    for h in (0, 3, 6, 9, 12, 15, 18, 21):
        x = left + h * (cell + gap) + cell / 2
        parts.append('<text x="%.1f" y="%d" class="tx" text-anchor="middle">%d</text>' % (x, height - 3, h))
    parts.append("</svg>")
    return "".join(parts)


def stat_row(items: Sequence[Sequence[str]]) -> str:
    out = ['<div class="stats">']
    for label, value in items:
        out.append('<div class="stat reveal"><b>%s</b><span>%s</span></div>' % (e(value), e(label)))
    out.append("</div>")
    return "".join(out)


def card(hue: int, kicker: str, title: str, body: str, emoji: str = "", ident: str = "") -> str:
    return (
        '<section class="card" id="%s" style="--h:%d;--h2:%d">'
        '<div class="inner"><p class="kicker reveal">%s%s</p><h2 class="reveal">%s</h2>%s</div></section>'
        % (e(ident), hue, (hue + 38) % 360, '<span class="emo">%s</span>' % emoji if emoji else "", e(kicker), title, body)
    )


def big_name(name: str, sub: str = "") -> str:
    return '<p class="big reveal">%s</p>%s' % (e(name), '<p class="sub reveal">%s</p>' % sub if sub else "")


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

def render_html(stats: Dict[str, Any]) -> str:
    meta = stats["meta"]
    year = meta["year"]
    anon = meta["anonymized"]
    cards: List[str] = []

    if stats.get("empty"):
        cards.append(card(260, "%d" % year, "이 해의 대화가 없어요", '<p class="sub">선택한 연도에 메시지가 없습니다.</p>', "🫥"))
        return _page(year, cards)

    tot = stats["totals"]
    members = stats["members"]
    top = meta["top"]
    aw = stats["awards"]

    # 1. cover
    title_line = ""
    if meta.get("title") and not anon:
        title_line = '<p class="room reveal">“%s”</p>' % e(meta["title"])
    cards.append(card(
        265, "YEAR-END WRAPPED", "%d<br>우리 단톡방 연말결산" % year,
        title_line
        + '<p class="sub reveal">올 한 해도 쉬지 않고 떠든 우리,<br>숫자로 돌아볼까요?</p>'
        + stat_row([("메시지", num(tot["messages"])), ("대화한 날", num(tot["active_days"]) + "일"), ("멤버", num(tot["members"]) + "명")])
        + '<p class="muted hint reveal">%s ~ %s · 아래로 스크롤 ↓</p>' % (e(fmt_date(tot["first_day"], False)), e(fmt_date(tot["last_day"], False))),
        "🎁", "cover"))

    # 2. share
    shown = members[:top]
    rest = members[top:]
    rows = [{"label": m["name"], "value": m["messages"], "text": "%s · %s%%" % (num(m["messages"]), m["share"])} for m in shown]
    if rest:
        rm = sum(m["messages"] for m in rest)
        rows.append({"label": "그 외 %d명" % len(rest), "value": rm, "text": "%s · %s%%" % (num(rm), round(100.0 * rm / tot["messages"], 1))})
    champ = members[0]
    cards.append(card(
        330, "수다 지분율", "올해 우리가 나눈 말, <span class=\"hl\">%s개</span>" % num(tot["messages"]),
        big_name("👑 " + champ["name"], "수다왕 · 전체의 %s%%를 혼자 말했어요" % champ["share"])
        + bars(rows)
        + '<p class="muted reveal">대화한 날 기준 하루 평균 %s개 · 글자 수로는 %s자예요</p>' % (num(tot["avg_per_active_day"]), num(tot["chars"])),
        "💬", "share"))

    # 3. busiest day / month
    bd = stats["busiest_day"]
    bm = stats["busiest_month"]
    cards.append(card(
        20, "가장 시끄러웠던 날", "%s,<br>단톡방이 불탔다" % e(fmt_date(bd["date"])),
        stat_row([("그날의 메시지", num(bd["count"]) + "개"), ("그날의 수다왕", bd["top_name"])])
        + '<p class="sub reveal">분위기 주도: <b>%s</b> (%s개) 🔥</p>' % (e(bd["top_name"]), num(bd["top_count"]))
        + month_chart(stats["months"], bm["month"])
        + '<p class="muted reveal">가장 뜨거웠던 달은 <b>%d월</b> (%s개)</p>' % (bm["month"], num(bm["count"])),
        "🔥", "busiest"))

    # 4. heatmap
    wd = stats["weekdays"]
    best_wd = max(range(7), key=lambda i: (wd[i], -i))
    cards.append(card(
        200, "우리의 생체 리듬", "우리는 <span class=\"hl\">%s요일 %d시</span> 즈음<br>제일 활발해요" % (WEEKDAYS[best_wd], stats["peak_hour"]),
        '<div class="reveal">' + heatmap(stats["heatmap"]) + "</div>"
        + '<p class="muted reveal">진할수록 메시지가 많은 시간대예요 (세로: 요일, 가로: 0~23시)</p>',
        "🕒", "rhythm"))

    # 5. night owl
    if aw.get("night_owl"):
        w = aw["night_owl"]["winner"]
        rows = [{"label": r["name"], "value": r["value"], "text": "%s개 · %s%%" % (num(r["value"]), r["night_ratio"])} for r in aw["night_owl"]["ranking"]]
        cards.append(card(
            250, "새벽형 인간 어워드", "다들 자는 시간에<br>혼자 깨어 있던 사람",
            big_name("🌙 " + w["name"], "새벽 0~5시에 %s개 · 자기 메시지의 %s%%" % (num(w["value"]), w["night_ratio"]))
            + bars(rows) + '<p class="muted reveal">잠은 언제 자는 거예요…? 건강 챙겨요 💤</p>',
            "🦉", "night"))

    # 6. fast replier
    if aw.get("fast_replier"):
        w = aw["fast_replier"]["winner"]
        rows = [{"label": r["name"], "value": 1.0 / (r["value"] + 30), "text": fmt_dur(r["value"])} for r in aw["fast_replier"]["ranking"]]
        slow = aw.get("slow_replier")
        slow_html = ""
        if slow:
            s = slow["winner"]
            slow_html = '<p class="sub reveal">🐢 느긋왕은 <b>%s</b> · 보통 %s 뒤에 답해요</p>' % (e(s["name"]), e(fmt_dur(s["value"])))
        cards.append(card(
            45, "칼답왕", "답장 속도<br>전광석화",
            big_name("⚡ " + w["name"], "답장 시간 중앙값 %s" % e(fmt_dur(w["value"])))
            + bars(rows) + slow_html
            + '<p class="muted reveal">다른 사람 말에 1시간 안에 답한 경우만 계산했어요. 카톡 내보내기는 분 단위라 "1분 미만"이 최소예요.</p>',
            "⚡", "fast"))

    # 7. lurker
    lk = aw.get("lurker")
    if lk:
        tail = "아직도 잠수 중일지도요 🫧" if lk["ongoing"] else "돌아와줘서 고마워요 🤗"
        cards.append(card(
            170, "잠수왕", "조용히 지켜보던<br>관찰자",
            big_name("🫥 " + lk["name"], "%s 동안 말이 없었어요" % e(fmt_dur(lk["seconds"])))
            + '<p class="sub reveal">%s ~ %s<br>그 사이 다른 친구들은 %s개의 메시지를 나눴어요</p>' % (e(fmt_dt(lk["from"])), e(fmt_dt(lk["to"])), num(lk["others_messages"]))
            + '<p class="muted reveal">읽씹은 아니고… 다들 바빴을 뿐이에요! %s</p>' % tail,
            "🫧", "lurker"))

    # 8. photo / emoticon
    pk, ek = aw.get("photo_king"), aw.get("emoticon_king")
    if pk or ek:
        body = ""
        if pk:
            body += big_name("📸 " + pk["winner"]["name"], "사진왕 · 올해 사진 %s장" % num(pk["winner"]["value"]))
            body += bars([{"label": r["name"], "value": r["value"], "text": num(r["value"]) + "장"} for r in pk["ranking"]])
        if ek:
            body += big_name("😆 " + ek["winner"]["name"], "이모티콘왕 · %s번 보냈어요" % num(ek["winner"]["value"]))
            body += bars([{"label": r["name"], "value": r["value"], "text": num(r["value"]) + "개"} for r in ek["ranking"]])
        body += stat_row([("사진", num(tot["photos"])), ("동영상", num(tot["videos"])), ("이모티콘", num(tot["emoticons"])), ("파일", num(tot["files"]))])
        cards.append(card(300, "사진 · 이모티콘 어워드", "말 대신<br>사진과 이모티콘", body, "🖼️", "media"))

    # 9. kkk
    kk = aw.get("kkk_king")
    if kk:
        w = kk["winner"]
        streak = aw.get("kkk_streak")
        streak_html = ""
        if streak:
            streak_html = (
                '<div class="kkk reveal"><p class="muted">최장 ㅋ 연속 기록 · %s (%d연속)</p><p class="kk">%s</p></div>'
                % (e(streak["name"]), streak["length"], "ㅋ" * min(streak["length"], 60))
            )
        rows = [{"label": r["name"], "value": r["value"], "text": "%.2f" % r["value"]} for r in kk["ranking"]]
        cards.append(card(
            55, "ㅋㅋ 지수", "웃음 발전소<br>가동률 1위",
            big_name("🤣 " + w["name"], "메시지 1개당 ㅋ %.2f개 (단톡방 평균 %.2f)" % (w["value"], aw["kkk_group_avg"]))
            + bars(rows) + streak_html,
            "😂", "kkk"))

    # 10. mini awards
    mini = []
    sk = aw.get("starter_king")
    if sk:
        mini.append(("🚀", "선톡왕", sk["winner"]["name"], "6시간 넘은 침묵을 깬 횟수 %s번" % num(sk["winner"]["value"])))
    lw = aw.get("long_writer")
    if lw:
        mini.append(("📜", "장문왕", lw["winner"]["name"], "메시지 1개당 평균 %.1f자" % lw["winner"]["value"]))
    ck = aw.get("cry_king")
    if ck:
        mini.append(("🥹", "눈물 지수왕", ck["winner"]["name"], "ㅠ/ㅜ 메시지당 %.2f개" % ck["winner"]["value"]))
    ts = stats["links"].get("top_sharer")
    if ts:
        mini.append(("🔗", "링크 장인", ts["name"], "링크 %s개 공유" % num(ts["count"])))
    st = stats["streak"]
    if st["days"] > 1:
        mini.append(("📆", "최장 연속 대화", "%d일 연속" % st["days"], "%s ~ %s" % (fmt_date(st["start"], False), fmt_date(st["end"], False))))
    if mini:
        body = '<div class="mini">' + "".join(
            '<div class="mrow reveal" style="--d:%dms"><span class="mi">%s</span><div><small>%s</small><b>%s</b><span>%s</span></div></div>'
            % (i * 70, ic, e(t), e(n), e(d)) for i, (ic, t, n, d) in enumerate(mini)
        ) + "</div>"
        cards.append(card(140, "소소하지만 확실한 어워드", "빠질 수 없는<br>보너스 트로피", body, "🏆", "mini"))

    # 11. words
    wm = [m for m in members[:top] if m["top_words"]]
    if wm or stats["top_words"]:
        body = '<p class="muted reveal">올해의 단어</p>' + chips(stats["top_words"][:10])
        for m in wm:
            body += '<div class="wrow reveal"><b>%s</b>%s</div>' % (e(m["name"]), chips(m["top_words"]))
        cards.append(card(
            90, "말버릇 리포트", "우리가 제일 많이 한 말",
            body + '<p class="muted reveal">조사·어미를 대충 떼어낸 간단한 분석이라 조금 어색한 단어가 있을 수 있어요.</p>',
            "🗣️", "words"))

    # 12. links
    lks = stats["links"]
    if lks["total"]:
        rows = [{"label": d, "value": c, "text": num(c) + "회"} for d, c in lks["domains"]]
        cards.append(card(
            215, "링크 사이트 순위", "올해 우리가 공유한<br>링크 %s개" % num(lks["total"]),
            bars(rows, wide=True) + '<p class="muted reveal">도메인만 집계했어요. 링크 주소 자체는 저장하지 않아요.</p>',
            "🔗", "links"))

    # 13. quote of the year
    q = stats.get("quote_of_year")
    if q:
        if q["text"]:
            qhtml = '<blockquote class="reveal">“%s”</blockquote>' % e(q["text"])
        else:
            qhtml = '<blockquote class="reveal secret">(익명화 모드라 문장은 비공개예요)</blockquote>'
        cards.append(card(
            340, "올해의 한마디", "이 한마디 뒤로<br>대화가 터졌다",
            qhtml + '<p class="sub reveal">— %s · %s</p>' % (e(q["name"]), e(fmt_dt(q["ts"])))
            + '<p class="muted reveal">이 말 뒤에 이어진 다른 사람의 메시지 %s개 (%d명 참여)<br>'
              '※ 3분 안에 이어진 대화 중 뒤따른 반응이 가장 많은 메시지를 골랐어요</p>' % (num(q["followers"]), q["distinct_people"]),
            "✨", "quote"))

    # 14. first / last
    fm, lm = stats["first_message"], stats["last_message"]

    def bubble(label: str, msg: Dict[str, Any]) -> str:
        txt = msg["text"] if msg["text"] else "(익명화 모드라 내용은 비공개)"
        return (
            '<div class="fl reveal"><small>%s · %s</small><div class="bub"><b>%s</b><p>%s</p></div></div>'
            % (e(label), e(fmt_dt(msg["ts"])), e(msg["name"]), e(txt))
        )

    cards.append(card(
        10, "처음과 마지막", "%d년의 첫 마디와<br>마지막 마디" % year,
        bubble("첫 메시지", fm) + bubble("마지막 메시지", lm), "🔖", "firstlast"))

    # 15. outro
    cards.append(card(
        265, "THE END", "내년에도 잘 부탁해 🥂",
        '<p class="sub reveal">%d년, 같이 떠들어줘서 고마워요.</p>' % year
        + '<div class="note reveal"><b>공유하기 전에</b> 친구들에게 동의를 받아주세요.<br>'
          '이 파일은 내 컴퓨터에서만 만들어졌고, 어디에도 업로드되지 않았어요.'
          + ("<br>익명화 모드로 만들어져 이름과 메시지 원문은 들어 있지 않아요." if anon else "<br>이름과 일부 메시지 문장이 들어 있어요. (전화번호·이메일은 가렸어요)")
          + "</div>"
        + '<p class="muted reveal">kakao-wrapped v%s</p>' % __version__,
        "🎄", "outro"))

    return _page(year, cards)


def _page(year: int, cards: List[str]) -> str:
    return PAGE_TEMPLATE.replace("%%YEAR%%", str(year)).replace("%%CARDS%%", "\n".join(cards))


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex">
<title>%%YEAR%% 단톡방 연말결산</title>
<style>
:root{--bg:#f3f0ff;--fg:#1e1b2e;--muted:#6a6680;--track:rgba(30,27,46,.09);--cs:85%;--l1:95%;--l2:90%;--ac-l:52%;--bubble:#fff;--shadow:0 8px 24px rgba(30,27,46,.12)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f0d1a;--fg:#f1eefc;--muted:#a49fbd;--track:rgba(255,255,255,.12);--cs:45%;--l1:15%;--l2:11%;--ac-l:64%;--bubble:rgba(255,255,255,.08);--shadow:0 8px 24px rgba(0,0,0,.4)}}
:root[data-theme=dark]{--bg:#0f0d1a;--fg:#f1eefc;--muted:#a49fbd;--track:rgba(255,255,255,.12);--cs:45%;--l1:15%;--l2:11%;--ac-l:64%;--bubble:rgba(255,255,255,.08);--shadow:0 8px 24px rgba(0,0,0,.4)}
*{box-sizing:border-box}
html{scroll-snap-type:y proximity;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font-family:Pretendard,"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic","WenQuanYi Zen Hei",system-ui,sans-serif;line-height:1.5;word-break:keep-all;overflow-wrap:anywhere}
.hdr{position:fixed;top:0;left:0;right:0;z-index:10;padding:env(safe-area-inset-top) 0 0}
.progress{height:4px;background:var(--track)}
.progress i{display:block;height:100%;width:0;background:hsl(var(--cur-h,265) 80% var(--ac-l))}
.tools{display:flex;justify-content:space-between;align-items:center;padding:6px 12px;font-size:.78rem;color:var(--muted)}
#theme{border:0;background:var(--track);color:var(--fg);border-radius:999px;padding:4px 10px;font-size:.9rem;cursor:pointer}
.card{--ac:hsl(var(--h) 80% var(--ac-l));min-height:100vh;min-height:100svh;scroll-snap-align:start;display:flex;align-items:center;justify-content:center;padding:64px 20px 44px;background:linear-gradient(160deg,hsl(var(--h) var(--cs) var(--l1)),hsl(var(--h2) var(--cs) var(--l2)))}
.inner{width:100%;max-width:460px}
.kicker{margin:0 0 6px;font-size:.82rem;font-weight:700;letter-spacing:.04em;color:var(--ac);text-transform:uppercase}
.emo{font-size:1.4rem;margin-right:6px;vertical-align:-2px}
h2{margin:0 0 18px;font-size:clamp(1.55rem,7.2vw,2.15rem);line-height:1.22;font-weight:800;letter-spacing:-.02em}
.hl{color:var(--ac)}
.big{margin:4px 0 0;font-size:clamp(2.1rem,11vw,3.3rem);font-weight:900;line-height:1.1;letter-spacing:-.03em;color:var(--ac)}
.sub{margin:6px 0 16px;font-size:1.02rem}
.muted{color:var(--muted);font-size:.84rem;margin:12px 0 0}
.room{font-size:1.15rem;font-weight:700;margin:0 0 6px;color:var(--ac)}
.hint{text-align:center;margin-top:28px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(72px,1fr));gap:10px;margin:14px 0}
.stat{background:var(--bubble);border-radius:16px;padding:12px 10px;text-align:center;box-shadow:var(--shadow)}
.stat b{display:block;font-size:1.25rem;font-weight:800;color:var(--ac);overflow-wrap:anywhere}
.stat span{font-size:.76rem;color:var(--muted)}
.bars{margin:12px 0}
.bar{display:grid;grid-template-columns:minmax(4.5rem,32%) 1fr auto;gap:8px;align-items:center;margin:9px 0;font-size:.9rem}
.bars.wide .bar{grid-template-columns:minmax(6rem,48%) 1fr auto}
.bar .lb{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar .lb em{font-style:normal;display:inline-block;min-width:1.5em}
.bar .tr{height:14px;border-radius:999px;background:var(--track);overflow:hidden}
.bar .tr i{display:block;height:100%;width:var(--w);border-radius:999px;background:linear-gradient(90deg,hsl(var(--h) 80% var(--ac-l)),hsl(var(--h2) 80% var(--ac-l)));transition:width .9s cubic-bezier(.2,.7,.2,1) var(--d,0ms)}
.bar.first .lb{font-weight:800}
.bar .vl{font-variant-numeric:tabular-nums;color:var(--muted);font-size:.82rem;text-align:right}
.months{display:flex;gap:4px;align-items:flex-end;height:130px;margin-top:18px}
.mcol{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;position:relative}
.mcol i{display:block;width:100%;height:var(--ht);min-height:3px;border-radius:6px 6px 2px 2px;background:hsl(var(--h) 60% var(--ac-l) / .3);transition:height .8s ease}
.mcol.best i{background:var(--ac)}
.mcol b{font-size:.68rem;color:var(--muted);margin-top:4px;font-weight:600}
.mcol .mv{font-size:.68rem;font-weight:800;color:var(--ac);white-space:nowrap}
.heat{width:100%;height:auto;display:block;margin:6px 0}
.heat .z{fill:var(--track)}
.heat .hc{fill:var(--ac)}
.heat .tx{font-size:9px;fill:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.chip{background:var(--bubble);border-radius:999px;padding:3px 11px;font-weight:700;box-shadow:var(--shadow)}
.chip small{color:var(--muted);font-weight:500;margin-left:5px;font-size:.7rem}
.wrow{margin:10px 0}
.wrow>b{display:block;color:var(--ac);font-size:.92rem}
.mini{display:grid;gap:10px}
.mrow{display:flex;gap:12px;align-items:center;background:var(--bubble);border-radius:16px;padding:12px 14px;box-shadow:var(--shadow)}
.mrow .mi{font-size:1.7rem}
.mrow small{display:block;color:var(--ac);font-weight:700}
.mrow b{font-size:1.1rem;margin-right:8px}
.mrow span:last-child{color:var(--muted);font-size:.82rem}
.kkk{margin-top:14px}
.kk{margin:6px 0 0;padding:12px;background:var(--bubble);border-radius:14px;color:var(--ac);font-weight:800;font-size:1.1rem;word-break:break-all;line-height:1.3}
blockquote{margin:6px 0 12px;padding:18px 18px;background:var(--bubble);border-left:5px solid var(--ac);border-radius:6px 18px 18px 6px;font-size:1.3rem;font-weight:700;line-height:1.4;box-shadow:var(--shadow)}
blockquote.secret{font-size:1rem;color:var(--muted);font-weight:500}
.fl{margin:14px 0}
.fl small{color:var(--muted)}
.bub{background:var(--bubble);border-radius:6px 18px 18px 18px;padding:10px 14px;margin-top:5px;box-shadow:var(--shadow)}
.bub b{color:var(--ac);font-size:.85rem}
.bub p{margin:2px 0 0;font-size:1.05rem;white-space:pre-wrap}
.note{background:var(--bubble);border-radius:16px;padding:14px 16px;font-size:.92rem;margin:14px 0;box-shadow:var(--shadow)}
.js .card:not(.in) .reveal{opacity:0;transform:translateY(14px)}
.reveal{transition:opacity .6s ease,transform .6s ease}
.js .card:not(.in) .bar .tr i{width:0}
.js .card:not(.in) .mcol i{height:3px}
@media (prefers-reduced-motion:reduce){.js .card:not(.in) .reveal{opacity:1;transform:none}.js .card:not(.in) .bar .tr i{width:var(--w)}.js .card:not(.in) .mcol i{height:var(--ht)}*{transition:none!important}}
@media print{.hdr{display:none}.card{min-height:auto;break-inside:avoid}}
</style>
</head>
<body>
<div class="hdr"><div class="progress"><i></i></div><div class="tools"><span id="pos"></span><button id="theme" type="button" aria-label="밝은/어두운 모드 전환">🌓</button></div></div>
<main>
%%CARDS%%
</main>
<script>
(function(){
var root=document.documentElement;root.classList.add('js');
var cards=[].slice.call(document.querySelectorAll('.card'));
var bar=document.querySelector('.progress i'),pos=document.getElementById('pos');
function upd(){
  var h=root.scrollHeight-window.innerHeight,y=window.pageYOffset;
  bar.style.width=(h>0?Math.min(100,y/h*100):0)+'%';
  var cur=0,mid=window.innerHeight*0.5;
  cards.forEach(function(c,i){if(c.getBoundingClientRect().top<=mid)cur=i;});
  pos.textContent=(cur+1)+' / '+cards.length;
  root.style.setProperty('--cur-h',getComputedStyle(cards[cur]).getPropertyValue('--h'));
}
window.addEventListener('scroll',upd,{passive:true});window.addEventListener('resize',upd);upd();
if('IntersectionObserver' in window){
  var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target);}});},{threshold:0.2});
  cards.forEach(function(c){io.observe(c);});
}else{cards.forEach(function(c){c.classList.add('in');});}
document.getElementById('theme').addEventListener('click',function(){
  var dark=root.getAttribute('data-theme')?root.getAttribute('data-theme')==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;
  root.setAttribute('data-theme',dark?'light':'dark');
});
document.addEventListener('keydown',function(ev){
  var k=ev.key;if(k!=='ArrowDown'&&k!=='ArrowUp'&&k!==' '&&k!=='PageDown'&&k!=='PageUp')return;
  var cur=0;cards.forEach(function(c,i){if(c.getBoundingClientRect().top<=window.innerHeight*0.4)cur=i;});
  var next=(k==='ArrowUp'||k==='PageUp')?cur-1:cur+1;
  if(next>=0&&next<cards.length){ev.preventDefault();cards[next].scrollIntoView({behavior:'smooth'});}
});
})();
</script>
</body>
</html>
"""
