"""Review project (JSON) <-> self-contained HTML <-> final SRT."""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import List, Optional

from .qa import Limits, cps_of
from .srt import Cue
from .translate import Glossary

FLAG_LABELS = {"cps": "읽기속도", "long": "줄길이", "untranslated": "미번역",
               "number": "숫자", "name": "고유명사?", "glossary": "용어집"}

_NUM = re.compile(r"\d+(?:\.\d+)?")
_NAME = re.compile(r"[A-Z][A-Za-z0-9\-']+")


def extract_numbers(s: str) -> List[str]:
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)
    return list(dict.fromkeys(_NUM.findall(s)))


def extract_names(s: str) -> List[str]:
    """Capitalised words not at sentence start (possible proper nouns)."""
    names: List[str] = []
    for m in _NAME.finditer(s):
        before = s[:m.start()].rstrip()
        if not before or before[-1] in ".!?\"'“‘(":
            continue
        w = m.group(0).rstrip("'-")
        if w.endswith("'s"):
            w = w[:-2]
        if w and w != "I" and w not in names:
            names.append(w)
    return names


def compute_flags(entry: dict, limits: Limits) -> List[str]:
    """Must stay in sync with flags() in the HTML template."""
    flags: List[str] = []
    d = entry["draft"]
    if not d.strip():
        return ["untranslated"]
    if d.strip() == entry["src"].strip() and any(ch.isalpha() for ch in d):
        flags.append("untranslated")
    dur = entry["end_ms"] - entry["start_ms"]
    if cps_of(d, dur) > limits.cps:
        flags.append("cps")
    lines = d.split("\n")
    if len(lines) > limits.max_lines or any(len(l) > limits.max_chars for l in lines):
        flags.append("long")
    dnums = extract_numbers(d)
    if any(n not in dnums for n in entry["numbers"]):
        flags.append("number")
    low = d.lower()
    if any(n.lower() not in low for n in entry["names"]):
        flags.append("name")
    if any(k not in d for _, k in entry["gloss"]):
        flags.append("glossary")
    return flags


def build_doc(cues: List[Cue], drafts: List[Optional[str]], limits: Limits, glossary: Optional[Glossary] = None,
              meta: Optional[dict] = None) -> dict:
    glossary = glossary or Glossary()
    entries = []
    for c, d in zip(cues, drafts):
        src = c.text.replace("\n", " ")
        gl = glossary.relevant(src)
        gterms = [t.lower() for t, _ in gl]
        e = {"index": c.index, "start_ms": c.start, "end_ms": c.end, "src": c.text,
             "draft": d or "", "orig": d or "", "reviewed": False,
             "numbers": extract_numbers(src),
             "names": [n for n in extract_names(src) if not any(n.lower() in t for t in gterms)],
             "gloss": [list(p) for p in gl]}
        e["flags"] = compute_flags(e, limits)
        entries.append(e)
    m = dict(meta or {})
    m.update({"cps": limits.cps, "max_chars": limits.max_chars, "max_lines": limits.max_lines})
    m["doc_id"] = hashlib.sha1(json.dumps([(e["src"], e["orig"]) for e in entries],
                                          ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return {"version": 1, "meta": m, "entries": entries}


def apply_review(doc: dict, src_cues: Optional[List[Cue]] = None, allow_empty: bool = False) -> List[Cue]:
    """Edited review JSON -> final cues (1:1 with the source; timing untouched)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        raise ValueError("review JSON 형식이 아닙니다 (entries 목록 없음)")
    entries = sorted(doc["entries"], key=lambda e: e["index"])
    if src_cues is not None:
        if len(src_cues) != len(entries):
            raise ValueError("줄 수 불일치: 원문 %d줄 / 검수 파일 %d줄" % (len(src_cues), len(entries)))
        for c, e in zip(src_cues, entries):
            if c.start != e["start_ms"] or c.end != e["end_ms"]:
                raise ValueError("타임코드 불일치: #%d" % c.index)
    empties = [e["index"] for e in entries if not str(e.get("draft", "")).strip()]
    if empties and not allow_empty:
        raise ValueError("번역이 비어 있는 줄이 있습니다: %s (--allow-empty 로 무시 가능)" % empties[:20])
    cues = []
    for n, e in enumerate(entries, 1):
        text = "\n".join(l.strip() for l in str(e.get("draft", "")).strip().split("\n"))
        cues.append(Cue(n, int(e["start_ms"]), int(e["end_ms"]), text))
    return cues


def _json_for_script(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    for a, b in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"), (" ", "\\u2029")):
        s = s.replace(a, b)
    return s


def render_html(doc: dict) -> str:
    title = html.escape(str(doc.get("meta", {}).get("title", "subtitle review")))
    vals = {"LABELS": _json_for_script(FLAG_LABELS), "DATA": _json_for_script(doc), "TITLE": title}
    return re.sub(r"__(LABELS|DATA|TITLE)__", lambda m: vals[m.group(1)], _TEMPLATE)  # single pass


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ - 검수</title>
<style>
body{font:14px/1.5 -apple-system,"Malgun Gothic","Noto Sans KR",sans-serif;margin:0;background:#f5f5f7;color:#222}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:8px 14px;z-index:5;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header h1{font-size:15px;margin:0}
button{padding:5px 10px;border:1px solid #888;background:#fafafa;border-radius:4px;cursor:pointer}
table{border-collapse:collapse;width:100%}
th{text-align:left;background:#eee;padding:4px 8px}
td{vertical-align:top;padding:6px 8px;border-bottom:1px solid #e2e2e2;background:#fff}
td.tc{white-space:nowrap;font:12px/1.4 monospace;width:150px}
td.src{white-space:pre-wrap;width:36%}
textarea{width:100%;box-sizing:border-box;font:inherit;min-height:3.4em;resize:vertical}
tr.flagged td{background:#fff8e6}
tr.done td{opacity:.6}
.badge{display:inline-block;margin:0 3px 3px 0;padding:0 6px;border-radius:9px;background:#d9534f;color:#fff;font-size:11px}
.badge.name{background:#999}
.meta{font-size:11px;color:#666}
</style></head><body>
<header><h1>__TITLE__</h1>
<span id="stat"></span>
<label><input type="checkbox" id="onlyflag"> 플래그 있는 줄만</label>
<label><input type="checkbox" id="hidedone"> 검수 완료 숨김</label>
<button id="dl-json">편집본 JSON 내려받기</button>
<button id="dl-srt">SRT 내려받기</button>
<span class="meta">Alt+↓/↑ 다음/이전 줄 · 편집은 브라우저에 자동 저장됨</span></header>
<table><thead><tr><th>#</th><th>타임코드</th><th>원문</th><th>초안 (수정 가능)</th><th>검수</th></tr></thead><tbody id="rows"></tbody></table>
<script type="application/json" id="data">__DATA__</script>
<script>
(function(){'use strict';
var D=JSON.parse(document.getElementById('data').textContent),M=D.meta,E=D.entries;
var LABEL=__LABELS__,KEY='subpipe:'+(M.doc_id||'doc');
function pad(n,w){n=String(n);while(n.length<w)n='0'+n;return n;}
function tc(ms,sep){var h=Math.floor(ms/3600000),m=Math.floor(ms%3600000/60000),s=Math.floor(ms%60000/1000);return pad(h,2)+':'+pad(m,2)+':'+pad(s,2)+sep+pad(ms%1000,3);}
function flags(e){var f=[],t=e.draft;
 if(!t.trim())return['untranslated'];
 if(t.trim()===e.src.trim()&&/[A-Za-zÀ-ɏ぀-ヿ一-鿿가-힯]/.test(t))f.push('untranslated');
 var dur=(e.end_ms-e.start_ms)/1000;
 if(t.replace(/\n/g,'').length/Math.max(dur,0.001)>M.cps)f.push('cps');
 var ls=t.split('\n');if(ls.length>M.max_lines||ls.some(function(l){return l.length>M.max_chars;}))f.push('long');
 var dn=t.replace(/(\d),(?=\d{3})/g,'$1').match(/\d+(?:\.\d+)?/g)||[];
 if(e.numbers.some(function(n){return dn.indexOf(n)<0;}))f.push('number');
 var low=t.toLowerCase();if(e.names.some(function(n){return low.indexOf(n.toLowerCase())<0;}))f.push('name');
 if(e.gloss.some(function(g){return t.indexOf(g[1])<0;}))f.push('glossary');
 return f;}
var saved={};try{saved=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(x){}
function persist(){var o={};E.forEach(function(e){o[e.index]={d:e.draft,r:e.reviewed};});try{localStorage.setItem(KEY,JSON.stringify(o));}catch(x){}}
var tb=document.getElementById('rows'),rows=[];
function el(t,c,x){var n=document.createElement(t);if(c)n.className=c;if(x!==undefined)n.textContent=x;return n;}
E.forEach(function(e,k){
 var s=saved[e.index];if(s){e.draft=s.d;e.reviewed=s.r;}
 var tr=el('tr'),a=el('td','',e.index),b=el('td','tc'),c=el('td','src',e.src),d=el('td'),r=el('td');
 b.appendChild(document.createTextNode(tc(e.start_ms,',')));b.appendChild(el('br'));
 b.appendChild(document.createTextNode(tc(e.end_ms,',')));b.appendChild(el('br'));
 b.appendChild(el('span','meta',((e.end_ms-e.start_ms)/1000).toFixed(2)+'s'));
 var ta=el('textarea');ta.value=e.draft;var bd=el('div'),info=el('div','meta');
 d.appendChild(ta);d.appendChild(bd);d.appendChild(info);
 var cb=el('input');cb.type='checkbox';cb.checked=!!e.reviewed;r.appendChild(cb);
 tr.appendChild(a);tr.appendChild(b);tr.appendChild(c);tr.appendChild(d);tr.appendChild(r);tb.appendChild(tr);
 function refresh(){var f=flags(e);bd.textContent='';
  f.forEach(function(x){bd.appendChild(el('span','badge '+x,LABEL[x]||x));});
  var dur=Math.max((e.end_ms-e.start_ms)/1000,0.001),n=e.draft.replace(/\n/g,'').length;
  info.textContent=n+'자 · '+(n/dur).toFixed(1)+' CPS (한도 '+M.cps+')'+(e.draft!==e.orig?' · 수정됨':'');
  tr.className=(f.length?'flagged ':'')+(e.reviewed?'done':'');e._f=f;vis();stat();}
 ta.addEventListener('input',function(){e.draft=ta.value;persist();refresh();});
 ta.addEventListener('keydown',function(ev){if(ev.altKey&&(ev.key==='ArrowDown'||ev.key==='ArrowUp')){var n=rows[k+(ev.key==='ArrowDown'?1:-1)];if(n){n.ta.focus();n.tr.scrollIntoView({block:'center'});}ev.preventDefault();}});
 cb.addEventListener('change',function(){e.reviewed=cb.checked;persist();refresh();});
 rows.push({tr:tr,ta:ta,refresh:refresh,e:e});
});
function vis(){var of=document.getElementById('onlyflag').checked,hd=document.getElementById('hidedone').checked;
 rows.forEach(function(x){x.tr.style.display=((of&&!(x.e._f&&x.e._f.length))||(hd&&x.e.reviewed))?'none':'';});}
function stat(){var f=0,r=0;E.forEach(function(e){if(e._f&&e._f.length)f++;if(e.reviewed)r++;});
 document.getElementById('stat').textContent='검수 '+r+'/'+E.length+' · 플래그 '+f+'줄';}
document.getElementById('onlyflag').addEventListener('change',vis);
document.getElementById('hidedone').addEventListener('change',vis);
rows.forEach(function(x){x.refresh();});
function dl(name,text,type){var u=URL.createObjectURL(new Blob([text],{type:type+';charset=utf-8'}));var a=document.createElement('a');a.href=u;a.download=name;document.body.appendChild(a);a.click();setTimeout(function(){URL.revokeObjectURL(u);a.remove();},500);}
var base=(M.base||'subtitles');
document.getElementById('dl-json').addEventListener('click',function(){
 dl(base+'.edited.json',JSON.stringify({version:1,meta:M,entries:E.map(function(e){return{index:e.index,start_ms:e.start_ms,end_ms:e.end_ms,src:e.src,draft:e.draft,reviewed:!!e.reviewed};})},null,1),'application/json');});
document.getElementById('dl-srt').addEventListener('click',function(){
 var empty=E.filter(function(e){return !e.draft.trim();}).length;
 if(empty&&!confirm('번역이 비어 있는 줄이 '+empty+'개 있습니다. 그래도 내려받을까요?'))return;
 dl(base+'.srt',E.map(function(e,i){return(i+1)+'\n'+tc(e.start_ms,',')+' --> '+tc(e.end_ms,',')+'\n'+e.draft.trim()+'\n';}).join('\n'),'application/x-subrip');});
})();
</script></body></html>
"""
