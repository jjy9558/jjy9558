"""자기완결 HTML/SVG 출력: 취향 지도, 연말결산 카드, 게임 경력 증명서.

외부 파일, 외부 글꼴, 스크립트가 없다. 어두운 화면은 prefers-color-scheme 으로 처리한다."""
import hashlib
import math
from datetime import datetime
from html import escape

from ._jjkit import textwidth

PALETTE_LIGHT = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#B8860B", "#56B4E9", "#7A5CFA", "#6B7280"]
PALETTE_DARK = ["#5AB0F0", "#FF8A4C", "#34D399", "#F09AC8", "#E0B340", "#8CCBF5", "#A99BFF", "#9CA3AF"]

BASE_CSS = """
:root{--bg:#fafaf7;--card:#fff;--ink:#1d1d1b;--mute:#615f58;--line:#d9d6cc;--accent:#0072B2;%(light)s}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#201f1d;--ink:#ecebe6;--mute:#a9a69c;--line:#3a3935;--accent:#5AB0F0;%(dark)s}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%%}
body{margin:0;word-break:keep-all;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,"Segoe UI","Noto Sans KR","Malgun Gothic",sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.5rem;line-height:1.3;margin:.2em 0 .1em}
h2{font-size:1.05rem;margin:1.6em 0 .5em}
p,li{overflow-wrap:anywhere}
.sub{color:var(--mute);margin:0 0 1em;font-size:.92rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
"""


def _palette_css():
    return (";".join(f"--c{i}:{c}" for i, c in enumerate(PALETTE_LIGHT)),
            ";".join(f"--c{i}:{c}" for i, c in enumerate(PALETTE_DARK)))


def _page(title, css, body, lang="ko"):
    light, dark = _palette_css()
    return ('<!doctype html>\n<html lang="%s"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s%s</style></head><body>%s</body></html>\n'
            % (lang, escape(title), BASE_CSS % {"light": light, "dark": dark}, css, body))


# ------------------------------------------------------------------ 취향 지도
MAP_CSS = """
.prof{margin:.4em 0 0;padding-left:1.3em}
.prof li{margin:.25em 0}
figure{margin:1.2em 0 0}
svg.map{width:100%;max-width:560px;margin:0 auto;height:auto;display:block;background:var(--card);border:1px solid var(--line);border-radius:14px}
svg.map text{font:12px system-ui,"Noto Sans KR","Malgun Gothic",sans-serif;fill:var(--ink);paint-order:stroke;stroke:var(--card);stroke-width:3px;stroke-linejoin:round}
svg.map .axis{stroke:var(--line);stroke-width:1;stroke-dasharray:3 4}
svg.map .pt{stroke:var(--card);stroke-width:1.5}
/*CLS*/
figcaption{color:var(--mute);font-size:.85rem;margin-top:.5em}
.legend{list-style:none;margin:.5em 0 0;padding:0;display:grid;gap:8px}
.legend li{display:flex;gap:10px;align-items:flex-start}
.sw{flex:none;width:14px;height:14px;border-radius:4px;margin-top:5px}
.legend b{font-weight:600}
.legend small{display:block;color:var(--mute);line-height:1.4}
.kinds{display:flex;gap:16px;color:var(--mute);font-size:.85rem;margin:.6em 0 0;flex-wrap:wrap}
.kinds span::before{content:"";display:inline-block;width:10px;height:10px;background:var(--mute);margin-right:6px;vertical-align:-1px}
.kinds .g::before{border-radius:50%}
.kinds .v::before{border-radius:2px}
"""


def render_map(m, profile, tagdict, title="내 취향 지도", cap=None):
    units, labels, coords = m["units"], m["labels"], m["coords"]
    W, H, PAD = 420, 440, 28
    xs = [c[0] for c in coords] or [0.0]
    ys = [c[1] for c in coords] or [0.0]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sx = (W - 2 * PAD) / (x1 - x0) if x1 - x0 > 1e-9 else 0.0
    sy = (H - 2 * PAD) / (y1 - y0) if y1 - y0 > 1e-9 else 0.0
    pts = []
    for u, l, (cx, cy) in zip(units, labels, coords):
        px = PAD + (cx - x0) * sx if sx else W / 2
        py = PAD + (cy - y0) * sy if sy else H / 2
        r = (5 + min(9, math.sqrt(max(u.hours, 0)) * 1.1)) if u.kind == "game" else (4 + min(3, math.log2(1 + u.count)))
        pts.append([u, l, px, py, r])
    _dodge(pts, W, H)
    parts = [f'<svg class="map" viewBox="0 0 {W} {H}" role="img" aria-label="{escape(title)}: 취향이 비슷한 항목이 가까이 모인 지도">',
             f'<line class="axis" x1="{W / 2}" y1="10" x2="{W / 2}" y2="{H - 10}"/>',
             f'<line class="axis" x1="10" y1="{H / 2}" x2="{W - 10}" y2="{H / 2}"/>']
    for u, l, px, py, r in pts:
        tip = f"{u.label} [{_cluster_name(m, l, tagdict)}]"
        if u.kind == "game" and u.hours:
            tip += f" {u.hours:.0f}시간"
        if u.kind == "video":
            tip += f" 시청 {u.count}회"
        if u.rating:
            tip += f" 만족도 {u.rating}/5"
        if u.kind == "game":
            shape = f'<circle class="pt c{l % 8}" cx="{px:.1f}" cy="{py:.1f}" r="{r:.1f}">'
        else:
            shape = (f'<rect class="pt c{l % 8}" x="{px - r:.1f}" y="{py - r:.1f}" width="{2 * r:.1f}" '
                     f'height="{2 * r:.1f}" rx="2">')
        parts.append(f"{shape}<title>{escape(tip)}</title>{'</circle>' if u.kind == 'game' else '</rect>'}")
    # 글자는 중요한 순서로, 겹치면 건너뛴다
    boxes = []
    for u, l, px, py, r in sorted(pts, key=lambda p: (-(abs(p[0].aff) + (p[0].hours / 100 if p[0].kind == "game" else 0)), p[0].key))[:12]:
        text = textwidth.truncate(u.label, 18)
        w = textwidth.width(text) * 6.6
        x = min(max(px - w / 2, 4), W - w - 4)
        y = py - r - 5
        if y < 12:
            y = py + r + 13
        box = (x, y - 11, x + w, y + 3)
        if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in boxes):
            continue
        boxes.append(box)
        parts.append(f'<text x="{x:.1f}" y="{y:.1f}">{escape(text)}</text>')
    parts.append("</svg>")
    cls = "\n".join(f".c{i}{{fill:var(--c{i})}}.sw.c{i}{{background:var(--c{i})}}" for i in range(8))
    legend = []
    by_key = {u.key: u for u in units}
    for c in m["clusters"]:
        names = [by_key[k].label for k in c["members"]]
        shown = ", ".join(escape(textwidth.truncate(n, 24)) for n in names[:5])
        more = f" 외 {len(names) - 5}개" if len(names) > 5 else ""
        legend.append(f'<li><span class="sw c{c["id"] % 8}"></span><div><b>{c["id"] + 1}. '
                      f'{escape(_tag_names(c["top"], tagdict))}</b> ({c["size"]}개)<small>{shown}{more}</small></div></li>')
    notes = []
    if m["untagged"]:
        notes.append(f"태그를 알 수 없는 {len(m['untagged'])}개는 지도에서 빠졌어요(tags edit 로 태그를 붙이면 들어와요).")
    if m["hidden_videos"] > 0:
        notes.append(f"영상은 많이 본 순으로 {sum(1 for u in units if u.kind == 'video')}개만 올렸고 {m['hidden_videos']}개는 뺐어요.")
    v = m["variance"]
    notes.append(f"가까울수록 태그가 비슷해요. 두 축이 담은 정보량은 {int(round((v[0] + v[1]) * 100))}%예요. "
                 f"군집 {m['k']}개, 시드 {m['seed']}.")
    body = (f'<main><h1>{escape(title)}</h1>'
            f'<p class="sub">{len([u for u in units if u.kind == "game"])}개 게임 · '
            f'{len([u for u in units if u.kind == "video"])}개 영상 · 이 파일은 혼자 열립니다(외부 연결 없음)</p>'
            f'<h2>나는 이런 걸 좋아한다</h2><div class="card"><ol class="prof">'
            + "".join(f"<li>{escape(s)}</li>" for s in profile)
            + f'</ol></div><figure>{"".join(parts)}'
            f'<figcaption>{escape(" ".join(notes))}</figcaption></figure>'
            f'<div class="kinds"><span class="g">게임(크기는 플레이 시간)</span><span class="v">영상</span></div>'
            f'<h2>군집</h2><ul class="legend">{"".join(legend)}</ul></main>')
    return _page(title, MAP_CSS.replace("/*CLS*/", cls), body)


def _tag_names(tids, tagdict):
    return "·".join(tagdict.label(t) for t in tids[:2]) or "기타"


def _cluster_name(m, l, tagdict):
    for c in m["clusters"]:
        if c["id"] == l:
            return _tag_names(c["top"], tagdict)
    return ""


def _dodge(pts, W, H):
    """같은 자리에 겹친 점을 황금각 나선으로 살짝 벌린다(키 순서라 결정적)."""
    placed = []
    for p in sorted(pts, key=lambda p: p[0].key):
        px, py, r = p[2], p[3], p[4]
        for i in range(60):
            ang = i * 2.399963
            d = 0 if i == 0 else 2.2 * r * math.sqrt(i) * 0.6
            cx, cy = px + d * math.cos(ang), py + d * math.sin(ang)
            cx, cy = min(max(cx, r + 2), W - r - 2), min(max(cy, r + 2), H - r - 2)
            if all((cx - q[0]) ** 2 + (cy - q[1]) ** 2 >= (r + q[2] + 1) ** 2 * 0.64 for q in placed):
                break
        p[2], p[3] = cx, cy
        placed.append((cx, cy, r))


# ------------------------------------------------------------------ 연말결산
WRAP_CSS = """
.hero{text-align:center;padding:28px 16px}
.big{font-size:3rem;line-height:1.1;font-weight:700;color:var(--accent);letter-spacing:-.02em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:12px}
.k{color:var(--mute);font-size:.85rem}
.v{font-size:1.25rem;font-weight:650;overflow-wrap:anywhere}
.note{color:var(--mute);font-size:.85rem;margin-top:1.4em}
"""


def _won(n):
    return f"{n:,.0f}원"


def render_wrapped(s, title=None):
    y = s["year"]
    title = title or (f"{y} 게임 결산" if y else "게임 결산")
    scope = f"{y}년에 산 게임 {s['count']}개 기준" if y else f"기록된 게임 {s['count']}개 기준"
    cards = []

    def card(k, v, sub=""):
        cards.append(f'<div class="card"><div class="k">{escape(k)}</div><div class="v">{escape(v)}</div>'
                     + (f'<div class="k">{escape(sub)}</div>' if sub else "") + "</div>")

    card("쓴 돈", _won(s["spend"]), f"게임당 평균 {_won(s['spend'] / s['count'])}" if s["count"] else "")
    if s["best"]:
        card("시간당 비용이 가장 낮은 게임", s["best"]["name"],
             f"시간당 {_won(s['best']['per_hour'])} ({s['best']['hours']:.1f}시간)")
    if s["worst"] and s["worst"] is not s["best"]:
        card("시간당 비용이 가장 높은 게임", s["worst"]["name"],
             f"시간당 {_won(s['worst']['per_hour'])} ({s['worst']['hours']:.1f}시간)")
    if s["shelved"]:
        card("가장 오래 묵힌 게임", s["shelved"]["name"], f"산 지 {s['shelved']['days']}일, 아직 안 켜 봤어요")
    elif s["count"]:
        card("가장 오래 묵힌 게임", "없음", "산 게임을 전부 한 번은 켜 봤어요")
    if s["top"]:
        card("가장 오래 한 게임", s["top"]["name"], f"{s['top']['hours']:.1f}시간")
    if s["avg_rating"] is not None:
        card("평균 만족도", f"{s['avg_rating']:.1f} / 5", f"{s['rated']}개에 점수를 줬어요")
    card("안 켜 본 게임", f"{s['unplayed']}개", "구매 후 플레이 6분 미만")
    if s.get("videos_watched") is not None:
        card("유튜브 시청", f"{s['videos_watched']}회", f"서로 다른 영상 {s['videos_distinct']}개")
    body = (f'<main><h1>{escape(title)}</h1><p class="sub">{escape(scope)}</p>'
            f'<div class="card hero"><div class="k">플레이 시간 합계</div>'
            f'<div class="big">{s["hours"]:,.1f}시간</div>'
            f'<div class="k">{s["minutes"]:,}분</div></div>'
            f'<div class="grid">{"".join(cards)}</div>'
            f'<p class="note">플레이 시간은 steam-buddy에 기록된 누적값이라, 그해에 산 게임의 지금까지 시간이에요. '
            f'환불한 게임은 뺐어요. 이 파일은 혼자 열립니다.</p></main>')
    return _page(title, WRAP_CSS, body)


# ------------------------------------------------------------------ 게임 경력 증명서
RANKS = [(10, "견습"), (100, "인턴"), (500, "사원"), (1500, "대리"), (3000, "과장"), (6000, "차장"),
         (10000, "부장"), (float("inf"), "이사")]


def rank_for(hours):
    for lim, name in RANKS:
        if hours < lim:
            return name
    return RANKS[-1][1]


CAREER_CSS = """
body{background:var(--bg)}
.paper{background:var(--card);border:2px solid var(--ink);border-radius:4px;padding:24px 14px;position:relative}
.paper h1{text-align:center;letter-spacing:.35em;font-size:1.7rem;margin:.2em 0 .6em;text-indent:.35em}
.no{text-align:right;color:var(--mute);font-size:.85rem}
table.t{width:100%;border-collapse:collapse;margin:.6em 0}
table.t th,table.t td{border:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
table.t th{width:6em;white-space:nowrap;background:color-mix(in srgb,var(--line) 35%,transparent);font-weight:600}
.proj{margin:.2em 0 0;padding-left:1.2em}
.pledge{text-align:center;margin:1.4em 0 .4em}
.issue{text-align:center;color:var(--mute)}
.stamp{width:84px;height:84px;margin:.6em auto 0;display:block}
.fun{color:var(--mute);font-size:.8rem;text-align:center;margin-top:1em}
@media print{body{background:#fff;color:#000}.paper{border-color:#000;box-shadow:none}main{padding:0}}
"""


def render_career(c):
    projects = "".join(
        f"<li>{escape(p['name'])} ({p['hours']:.0f}시간{', 만족도 ' + str(p['rating']) + '/5' if p['rating'] else ''})</li>"
        for p in c["projects"]) or "<li>기록된 프로젝트 없음</li>"
    field = ", ".join(escape(f) for f in c["fields"]) or "미정 (태그 기록 부족)"
    period = escape(c["period"])
    doc_id = "TM-" + hashlib.sha1(repr((c["name"], c["hours"], c["titles"], c["period"], c["issued"])).encode("utf-8")).hexdigest()[:8].upper()
    body = (f'<main><div class="paper"><div class="no">문서번호 {doc_id}</div>'
            f'<h1>경 력 증 명 서</h1>'
            f'<table class="t"><tr><th>성명</th><td>{escape(c["name"])}</td></tr>'
            f'<tr><th>소속</th><td>게임 생활 주식회사 (재미용)</td></tr>'
            f'<tr><th>경력 기간</th><td>{period}</td></tr>'
            f'<tr><th>직급</th><td>{escape(c["rank"])} (누적 {c["hours"]:,.0f}시간 근무)</td></tr>'
            f'<tr><th>보유 타이틀</th><td>{c["titles"]}개</td></tr>'
            f'<tr><th>전문 분야</th><td>{field}</td></tr>'
            f'<tr><th>주요 프로젝트</th><td><ol class="proj">{projects}</ol></td></tr>'
            f'<tr><th>표창</th><td>만족도 5점 타이틀 {c["five_star"]}건</td></tr></table>'
            f'<p class="pledge">위 사람은 위와 같이 게임에 성실히 근무하였음을 증명합니다.</p>'
            f'<p class="issue">{escape(c["issued"])}<br>취향 지도 인사팀</p>'
            f'<svg class="stamp" viewBox="0 0 84 84" role="img" aria-label="재미용 도장">'
            f'<circle cx="42" cy="42" r="38" fill="none" stroke="#C0392B" stroke-width="3"/>'
            f'<text x="42" y="40" text-anchor="middle" font-size="15" font-weight="700" fill="#C0392B">인사팀</text>'
            f'<text x="42" y="58" text-anchor="middle" font-size="12" fill="#C0392B">확인</text></svg>'
            f'<p class="fun">이 증명서는 재미로 만든 것이며 어떤 공식 효력도 없습니다. '
            f'닉네임과 집계 숫자만 들어 있고 계정 정보는 없습니다.</p></div></main>')
    return _page("게임 경력 증명서", CAREER_CSS, body)
