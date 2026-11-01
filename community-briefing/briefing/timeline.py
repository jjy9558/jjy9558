"""이슈 타임라인: 키워드/토픽별 JSON 누적 저장 + 자체 완결형 HTML 뷰어(out/timeline.html).

저장: <timeline_dir>/<키워드>.json        {"kind": "keyword", "keyword": ..., "events": [...]}
      <timeline_dir>/topic__<토픽>.json   {"kind": "topic",   "keyword": <토픽>, "events": [...]}
event: {date, title, url, sources, summary, topic}
"""
import json
import os
import re
from datetime import date, datetime, timedelta

_SAFE_URL = re.compile(r"^https?://", re.I)


def _slug(keyword):
    return re.sub(r"[^\w.\-]+", "_", keyword.strip().lower()).strip("_") or "keyword"


def _load(path, name, kind):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            data.setdefault("kind", kind)
            return data
    except (ValueError, OSError):
        pass  # corrupt file: start over rather than crash
    return {"kind": kind, "keyword": name, "events": []}


def _add(path, name, kind, items, date_str):
    data = _load(path, name, kind)
    known = {e.get("url") for e in data["events"]}
    for item in items:
        if item.url not in known:
            known.add(item.url)
            data["events"].append({"date": date_str, "title": item.title, "url": item.url,
                                   "sources": item.sources, "summary": item.brief, "topic": item.topic})
    data["events"].sort(key=lambda e: e.get("date", ""))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def append(items, timeline_dir, date_str):
    """오늘 항목을 매칭된 키워드별·토픽별 파일에 추가 (URL 중복 무시). -> 갱신한 파일 경로 목록."""
    os.makedirs(timeline_dir, exist_ok=True)
    touched = []
    for kw in sorted({k for item in items for k in item.matched}):
        touched.append(_add(os.path.join(timeline_dir, _slug(kw) + ".json"), kw, "keyword",
                            [i for i in items if kw in i.matched], date_str))
    for topic in sorted({i.topic for i in items if i.topic}):
        touched.append(_add(os.path.join(timeline_dir, "topic__" + _slug(topic) + ".json"), topic, "topic",
                            [i for i in items if i.topic == topic], date_str))
    return touched


def load_all(timeline_dir, days=None, today=None):
    """-> [{"kind","name","events"}] (이벤트는 최근 days일만, 최신순). 비어 있는 항목은 제외."""
    out = []
    if not os.path.isdir(timeline_dir):
        return out
    cutoff = None
    if days:
        cutoff = ((today or date.today()) - timedelta(days=int(days))).isoformat()
    for fn in sorted(os.listdir(timeline_dir)):
        if not fn.endswith(".json"):
            continue
        kind = "topic" if fn.startswith("topic__") else "keyword"
        data = _load(os.path.join(timeline_dir, fn), fn[:-5], kind)
        events = []
        for e in data["events"]:
            if not isinstance(e, dict) or (cutoff and e.get("date", "") < cutoff):
                continue
            url = e.get("url") or ""
            events.append({"date": str(e.get("date", "")), "title": str(e.get("title", "")),
                           "url": url if _SAFE_URL.match(url) else "",
                           "sources": [str(s) for s in e.get("sources", [])],
                           "summary": str(e.get("summary", ""))})
        events.sort(key=lambda e: e["date"], reverse=True)
        if events:
            out.append({"kind": data.get("kind", kind), "name": str(data.get("keyword", fn[:-5])),
                        "events": events})
    out.sort(key=lambda t: (t["kind"] != "topic", -len(t["events"]), t["name"]))
    return out


def find(timelines, topic):
    t = topic.strip().lower()
    return [x for x in timelines if x["name"].lower() == t] or [x for x in timelines if t in x["name"].lower()]


def render_text(entries):
    lines = []
    for tl in entries:
        lines.append("== %s (%s, %d) ==" % (tl["name"], "토픽" if tl["kind"] == "topic" else "키워드", len(tl["events"])))
        for e in tl["events"]:
            lines.append("%s  %s  [%s]" % (e["date"], e["title"], ", ".join(e["sources"])))
            if e["url"]:
                lines.append("            " + e["url"])
    return "\n".join(lines)


def _json_for_script(obj):
    # "<" -> \u003c so "</script>" in a title can never close the data block
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace(
        "&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build_html(timelines, generated_at=None, days=None):
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = _json_for_script({"timelines": timelines, "generated": generated_at, "days": days})
    return HTML_TEMPLATE.replace("__DATA__", payload)


def write_html(timeline_dir, html_path, days=30, today=None):
    timelines = load_all(timeline_dir, days, today)
    os.makedirs(os.path.dirname(os.path.abspath(html_path)), exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_html(timelines, days=days))
    return html_path


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>이슈 타임라인</title>
<style>
:root{--bg:#f6f7f9;--fg:#1c1f24;--muted:#6b7380;--card:#fff;--line:#d5d9e0;--accent:#2f6fed;--bd:#e4e7ec}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e6e8ec;--muted:#98a0ad;--card:#1d2026;--line:#3a3f48;--accent:#7aa2ff;--bd:#2c3038}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Malgun Gothic","Apple SD Gothic Neo",sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);padding:12px 14px;border-bottom:1px solid var(--bd)}
h1{font-size:1.1rem;margin:0 0 8px}
select{width:100%;padding:10px;font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:8px}
.meta{color:var(--muted);font-size:.8rem;margin-top:6px}
main{max-width:720px;margin:0 auto;padding:12px 14px 40px}
.day{margin:18px 0 6px;font-weight:700;font-size:.95rem;color:var(--muted)}
.tl{position:relative;margin:0 0 0 6px;padding:0 0 0 18px;border-left:2px solid var(--line);list-style:none}
.tl li{position:relative;background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:10px 12px;margin:0 0 10px;overflow-wrap:anywhere}
.tl li::before{content:"";position:absolute;left:-26px;top:16px;width:10px;height:10px;border-radius:50%;background:var(--accent);border:2px solid var(--bg)}
.tl a{color:var(--fg);font-weight:600;text-decoration:none}
.tl a:hover{color:var(--accent);text-decoration:underline}
.sum{margin:4px 0 0;color:var(--muted);font-size:.9rem}
.badge{display:inline-block;font-size:.72rem;padding:1px 8px;margin:6px 6px 0 0;border-radius:999px;color:#fff;background:hsl(var(--h),45%,40%)}
.empty{color:var(--muted);padding:30px 0;text-align:center}
</style>
</head>
<body>
<header>
<h1>이슈 타임라인</h1>
<select id="sel" aria-label="토픽 선택"></select>
<div class="meta" id="meta"></div>
</header>
<main id="main"></main>
<noscript><main><p>이 페이지는 JavaScript가 필요합니다.</p></main></noscript>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
var D=JSON.parse(document.getElementById("data").textContent),T=D.timelines;
var sel=document.getElementById("sel"),main=document.getElementById("main"),meta=document.getElementById("meta");
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e}
function hue(s){var h=0;for(var i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))%360;return h}
function opt(parent,i){var o=document.createElement("option");o.value=i;o.textContent=T[i].name+" ("+T[i].events.length+")";parent.appendChild(o)}
if(!T.length){main.appendChild(el("p","empty","아직 기록된 타임라인이 없습니다."));sel.disabled=true}
else{
 [["topic","토픽"],["keyword","키워드"]].forEach(function(g){
  var gr=document.createElement("optgroup");gr.label=g[1];
  T.forEach(function(t,i){if(t.kind===g[0])opt(gr,i)});
  if(gr.children.length)sel.appendChild(gr)});
 var h=decodeURIComponent(location.hash.slice(1)),start=0;
 T.forEach(function(t,i){if(t.name===h)start=i});
 if(!h)start=+sel.options[0].value;
 sel.value=start;show(start);
 sel.onchange=function(){location.hash=encodeURIComponent(T[sel.value].name);show(+sel.value)};
}
function show(i){
 var t=T[i];main.textContent="";
 meta.textContent=t.events.length+"건"+(D.days?" · 최근 "+D.days+"일":"")+" · 생성 "+D.generated;
 var day=null,ul=null;
 t.events.forEach(function(e){
  if(e.date!==day){day=e.date;main.appendChild(el("div","day",day));ul=el("ul","tl");main.appendChild(ul)}
  var li=el("li");
  var title=e.url?el("a"):el("span","",e.title);
  if(e.url){title.textContent=e.title;title.href=e.url;title.target="_blank";title.rel="noopener noreferrer"}
  li.appendChild(title);
  if(e.summary&&e.summary!==e.title)li.appendChild(el("p","sum",e.summary));
  var row=el("div");
  e.sources.forEach(function(s){var b=el("span","badge",s);b.style.setProperty("--h",hue(s));row.appendChild(b)});
  li.appendChild(row);ul.appendChild(li)});
}
})();
</script>
</body>
</html>
"""
