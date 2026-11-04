"""HTML 페이지 세 장(로그인, 런처, 사장님 책상). 외부 리소스 없음, 다크 모드 기본, 큰 버튼.

데이터는 JSON으로 심고(``<``는 이스케이프) 화면은 textContent로만 그린다(HTML 주입 없음).
"""
import json

CSS = """
:root{--bg:#0f1115;--card:#191c23;--card2:#21252e;--fg:#e9ecf1;--mut:#9aa3b2;--line:#2b303b;
--acc:#4f8cff;--ok:#2fbf71;--bad:#ff5a5f;--warn:#f5b041;--stamp:#e0393e;--paper:#f6f1e3;--ink:#23201a}
@media (prefers-color-scheme: light){:root{--bg:#f3f4f7;--card:#fff;--card2:#f0f2f6;--fg:#15181e;--mut:#5b6474;
--line:#dde1e8;--paper:#fffaf0}}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,-apple-system,"Segoe UI",
"Apple SD Gothic Neo","Malgun Gothic",sans-serif;-webkit-text-size-adjust:100%}
main{max-width:720px;margin:0 auto;padding:16px 16px 48px}
h1{font-size:24px;margin:8px 0 4px}
h2{font-size:18px;margin:24px 0 10px;color:var(--mut);font-weight:600}
.mut{color:var(--mut)}
.top{display:flex;align-items:center;justify-content:space-between;gap:8px}
button,.btn{appearance:none;border:0;border-radius:14px;background:var(--acc);color:#fff;font:inherit;
font-weight:700;min-height:52px;padding:12px 18px;cursor:pointer;text-decoration:none;display:inline-flex;
align-items:center;justify-content:center;gap:6px}
button.ghost,.btn.ghost{background:var(--card2);color:var(--fg)}
button.ok{background:var(--ok)}button.bad{background:var(--bad)}
button:disabled{opacity:.45;cursor:default}
input{font:inherit;min-height:52px;border-radius:14px;border:1px solid var(--line);background:var(--card2);
color:var(--fg);padding:10px 14px;width:100%}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media (min-width:560px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
.tile{position:relative;display:flex;flex-direction:column;gap:4px;background:var(--card);border:1px solid var(--line);
border-radius:18px;padding:16px;min-height:120px;color:inherit;text-decoration:none}
.tile .ic{font-size:34px;line-height:1}
.tile .t{font-weight:700;font-size:18px}
.tile .d{font-size:13px;color:var(--mut)}
.dot{position:absolute;top:14px;right:14px;width:14px;height:14px;border-radius:50%;background:var(--mut)}
.dot.up{background:var(--ok)}.dot.down{background:var(--bad)}.dot.auth,.dot.error{background:var(--warn)}
.tile.wide{grid-column:1/-1;flex-direction:row;align-items:center;gap:14px;min-height:84px}
.badge{display:inline-block;min-width:28px;padding:2px 9px;border-radius:999px;background:var(--bad);color:#fff;
font-weight:700;text-align:center}
.badge.zero{background:var(--card2);color:var(--mut)}
.quick{display:flex;flex-direction:column;gap:8px;margin-top:8px}.quick input{min-height:44px}.quick button{min-height:44px;padding:8px 12px}
.toast{position:fixed;left:16px;right:16px;bottom:16px;max-width:688px;margin:0 auto;background:var(--card2);
border:1px solid var(--line);border-radius:14px;padding:14px 16px;opacity:0;transform:translateY(10px);
transition:.2s;pointer-events:none}
.toast.show{opacity:1;transform:none}
.paper{position:relative;background:var(--paper);color:var(--ink);border-radius:6px;padding:16px 16px 14px;
margin:0 0 14px;box-shadow:0 2px 0 rgba(0,0,0,.25),0 8px 22px rgba(0,0,0,.25);overflow:hidden}
.pile .paper:nth-child(odd){transform:rotate(-.6deg)}.pile .paper:nth-child(even){transform:rotate(.5deg)}
.paper .k{font-size:12px;font-weight:700;letter-spacing:.04em;color:#7a6f57;text-transform:uppercase}
.paper .t{font-size:18px;font-weight:800;margin:2px 0 4px;word-break:keep-all;overflow-wrap:anywhere}
.paper .dt{white-space:pre-wrap;font-size:14px;overflow-wrap:anywhere}
.paper .due{position:absolute;top:12px;right:14px;font-weight:800;font-size:14px}
.paper .due.hot{color:var(--stamp)}
.paper .row{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.paper button,.paper .btn{min-height:48px}
.paper .atext{font-size:.85rem;opacity:.7;align-self:center}
.paper .btn.ghost,.paper button.ghost{background:rgba(0,0,0,.08);color:var(--ink)}
.paper.done{opacity:.72}
.stamp{position:absolute;right:18px;bottom:14px;width:96px;height:96px;border:5px solid var(--stamp);border-radius:50%;
color:var(--stamp);display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:900;
transform:rotate(-14deg);opacity:.85;pointer-events:none;animation:thud .35s cubic-bezier(.2,1.6,.4,1)}
.stamp.rej{border-style:double;border-width:7px}
@keyframes thud{0%{transform:scale(2.4) rotate(-30deg);opacity:0}100%{transform:scale(1) rotate(-14deg);opacity:.85}}
.appr{background:var(--card);border:1px solid var(--line);border-left:6px solid var(--warn);border-radius:14px;
padding:14px;margin:0 0 12px}
.appr .src{font-size:12px;font-weight:700;color:var(--mut)}
.appr .t{font-weight:800;font-size:17px;margin:2px 0}
.appr code{display:block;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--card2);border-radius:8px;
padding:8px;font-size:13px;margin-top:6px}
.appr .row{display:flex;gap:8px;margin-top:10px}.appr .row>*{flex:1}
.appr.risky{border-left-color:var(--bad)}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:14px;padding:22px;text-align:center;color:var(--mut)}
.warnline{color:var(--warn);font-size:14px;margin:4px 0}
"""


def _data(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028") \
        .replace("\u2029", "\\u2029")


def _page(title: str, body: str, script: str) -> str:
    return ("<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            "<meta name=\"color-scheme\" content=\"dark light\"><meta name=\"referrer\" content=\"no-referrer\">"
            "<title>%s</title><style>%s</style></head><body>%s<div id=\"toast\" class=\"toast\"></div>"
            "<script>%s</script></body></html>" % (title, CSS, body, script))


COMMON_JS = r"""
var $=function(id){return document.getElementById(id)};
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function toast(msg){var t=$("toast");t.textContent=msg;t.classList.add("show");clearTimeout(toast._t);
 toast._t=setTimeout(function(){t.classList.remove("show")},2600)}
function api(path,body){var o={method:body===undefined?"GET":"POST",credentials:"same-origin",headers:{}};
 if(body!==undefined){o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body)}
 return fetch(path,o).then(function(r){return r.json().catch(function(){return {}}).then(function(j){
  if(!r.ok){var e=new Error(j.error||("HTTP "+r.status));e.status=r.status;throw e}return j})})}
"""


def login_page() -> str:
    body = """<main><h1>📱 폰 홈</h1><p class="mut">토큰을 한 번 넣으면 이 폰에 로그인이 남습니다(쿠키, 30일).
주소 끝에 <b>#token=…</b>을 붙여 열어도 됩니다.</p>
<form id="f"><input id="tok" type="password" autocomplete="current-password" placeholder="phone-home 토큰"
 aria-label="토큰"><div style="height:12px"></div><button id="go" type="submit" style="width:100%">들어가기</button></form>
<p id="msg" class="warnline"></p></main>"""
    script = COMMON_JS + r"""
function login(tok){return api("/login",{token:tok}).then(function(){location.replace(location.pathname)},
 function(e){$("msg").textContent=e.status===429?"너무 많이 틀렸어요. 잠시 뒤에.":"토큰이 틀렸어요."})}
$("f").onsubmit=function(ev){ev.preventDefault();login($("tok").value.trim())};
(function(){var m=/[#&]token=([^&]+)/.exec(location.hash);
 if(m){history.replaceState(null,"",location.pathname);login(decodeURIComponent(m[1]))}})();
"""
    return _page("폰 홈 로그인", body, script)


def launcher_page(tiles: list[dict]) -> str:
    body = """<main><div class="top"><h1>📱 폰 홈</h1><button id="out" class="ghost" style="min-height:40px">로그아웃</button></div>
<p class="mut" id="sub">서비스 상태 확인 중…</p>
<div class="grid" id="grid">
 <a class="tile wide" href="/desk" id="deskTile"><span class="ic">🗂️</span><span style="flex:1">
 <span class="t" style="display:block">사장님 책상</span><span class="d">오늘의 서류와 결재 대기</span></span>
 <span class="badge zero" id="deskCount">0</span></a>
</div></main>"""
    script = ("var TILES=%s;" % _data(tiles)) + COMMON_JS + r"""
var dots={};
TILES.forEach(function(t){
 var a=el(t.link?"a":"div","tile");a.id="tile-"+t.name;a.dataset.state="unknown";if(t.link)a.href=t.link;
 var d=el("span","dot");d.title="확인 중";dots[t.name]={dot:d,tile:a};
 a.appendChild(d);a.appendChild(el("span","ic",t.icon||"🔗"));a.appendChild(el("span","t",t.title));
 if(t.desc)a.appendChild(el("span","d",t.desc));
 if(t.quick){var f=el("form","quick"),i=el("input");i.placeholder=t.quick.label||"입력";i.setAttribute("aria-label",i.placeholder);
  var b=el("button",null,"저장");f.appendChild(i);f.appendChild(b);
  f.onsubmit=function(ev){ev.preventDefault();var v=i.value.trim();if(!v)return;var o={};o[t.quick.field||"text"]=v;
   api("/s/"+t.name+t.quick.path,o).then(function(){i.value="";toast("저장했어요")},function(e){toast("실패: "+e.message)})};
  a.appendChild(f)}
 $("grid").appendChild(a)});
var LABEL={up:"살아 있음",down:"꺼져 있음",auth:"토큰 확인 필요",error:"오류",unknown:"확인 중"};
function health(){api("/api/health").then(function(j){var up=0,n=0;
 Object.keys(j.services||{}).forEach(function(k){var s=j.services[k],x=dots[k];if(!x)return;n++;if(s.ok)up++;
  x.dot.className="dot "+s.state;x.dot.title=LABEL[s.state]||s.state;x.tile.dataset.state=s.state});
 $("sub").textContent="서비스 "+n+"개 중 "+up+"개 살아 있음"},function(e){if(e.status===401)location.reload();
 $("sub").textContent="상태 확인 실패"})}
function desk(){api("/desk/api/items").then(function(j){
 var n=(j.approvals||[]).length+(j.cards||[]).filter(function(c){return !c.stamp}).length;
 var b=$("deskCount");b.textContent=n;b.className="badge"+(n?"":" zero")},function(){})}
$("out").onclick=function(){api("/logout",{}).then(function(){location.reload()})};
health();desk();setInterval(health,30000);setInterval(desk,30000);
"""
    return _page("폰 홈", body, script)


def desk_page(snapshot: dict) -> str:
    body = """<main><div class="top"><h1>🗂️ 사장님 책상</h1><a class="btn ghost" href="/" style="min-height:40px">← 홈</a></div>
<p class="mut" id="today"></p>
<h2>결재 대기</h2><div id="errs"></div><div id="appr"></div>
<h2>오늘의 서류 <span class="mut" style="font-weight:400;font-size:14px">마감 빠른 순</span></h2>
<div id="pile" class="pile"></div></main>"""
    script = ("var SNAP=%s;" % _data(snapshot)) + COMMON_JS + r"""
var STAMP={ok:"확인",reject:"반려"};
function today0(){var d=new Date();d.setHours(0,0,0,0);return d}
function dLabel(due){if(!due)return "";var m=/^(\d{4})-(\d{2})-(\d{2})/.exec(due);if(!m)return due;
 var d=new Date(+m[1],+m[2]-1,+m[3]),n=Math.round((d-today0())/86400000);
 return n===0?"오늘":(n>0?"D-"+n:"D+"+(-n))}
function hot(due){var l=dLabel(due);return l==="오늘"||/^D\+/.test(l)||l==="D-1"}
function renderAppr(s){var box=$("appr");box.textContent="";var errs=$("errs");errs.textContent="";
 Object.keys(s.errors||{}).forEach(function(k){errs.appendChild(el("p","warnline","⚠ "+k+" 연결 안 됨 ("+s.errors[k]+")"))});
 if(!(s.approvals||[]).length){box.appendChild(el("div","empty","기다리는 결재가 없습니다"));return}
 s.approvals.forEach(function(a){var c=el("div","appr"+(a.link_only?" risky":""));c.dataset.source=a.source;c.dataset.id=a.id;
  var src=a.source+(a.expires_in!=null?" · "+a.expires_in+"초 남음":"")+(a.by?" · "+a.by:"");
  c.appendChild(el("div","src",src));c.appendChild(el("div","t",a.title));if(a.detail)c.appendChild(el("code",null,a.detail));
  var row=el("div","row");
  if(a.link_only){c.appendChild(el("p","warnline","⚠ 위험 결재라 여기서는 누를 수 없어요. 서비스 화면에서 직접 보세요."));
   if(a.link){var l=el("a","btn ghost","↗ "+a.source+" 화면 열기");l.href=a.link;row.appendChild(l)}}
  else{var ok=el("button","ok","승인"),no=el("button","bad","반려");
   ok.onclick=function(){decide(a,"allow",row)};no.onclick=function(){decide(a,"deny",row)};
   row.appendChild(ok);row.appendChild(no)}
  c.appendChild(row);box.appendChild(c)})}
function decide(a,d,row){Array.prototype.forEach.call(row.children,function(b){b.disabled=true});
 api("/desk/api/decide",{source:a.source,id:a.id,decision:d}).then(function(){toast(d==="allow"?"승인했어요":"반려했어요");refresh()},
  function(e){toast("실패: "+e.message);refresh()})}
function stampEl(st){return el("div","stamp"+(st==="reject"?" rej":""),STAMP[st]||st)}
function renderPile(s){var box=$("pile");box.textContent="";
 if(!(s.cards||[]).length){box.appendChild(el("div","empty",s.cards_status==="broken"?"카드 파일이 깨져 있어요. morning-brief를 다시 돌려 주세요."
  :s.cards_status==="missing"?"아직 카드가 없어요 (morning-brief가 cards-latest.json을 만들면 여기에 쌓입니다)."
  :"오늘은 결재할 서류가 없습니다 ☕"));return}
 s.cards.forEach(function(c){var p=el("div","paper"+(c.stamp?" done":""));p.dataset.key=c.key;
  p.appendChild(el("div","k",c.kind));if(c.due){var du=el("div","due"+(hot(c.due)?" hot":""),dLabel(c.due));du.title=c.due;p.appendChild(du)}
  p.appendChild(el("div","t",c.title));if(c.detail)p.appendChild(el("div","dt",c.detail));
  var row=el("div","row");
  (c.actions||[]).forEach(function(a){if(a.url){var l=el("a","btn ghost",a.label);l.href=a.url;row.appendChild(l)}
   else if(a.runnable){var b=el("button","ghost","▶ "+a.label);b.onclick=function(){b.disabled=true;
    api("/desk/api/action",{action:a.id,key:c.key}).then(function(j){toast((j.ok?"완료: ":"실패: ")+(j.output||a.label))},
    function(e){toast("실패: "+e.message)}).then(function(){b.disabled=false})};row.appendChild(b)}
   else{var g=el("span","atext",a.label);g.title=a.text?"안내 문구":"허용 목록에 없는 action";row.appendChild(g)}});
  var ok=el("button","ok","확인"),no=el("button","bad","반려");
  ok.onclick=function(){stamp(c,"ok",p)};no.onclick=function(){stamp(c,"reject",p)};row.appendChild(ok);row.appendChild(no);
  p.appendChild(row);if(c.stamp)p.appendChild(stampEl(c.stamp));box.appendChild(p)})}
function stamp(c,st,p){api("/desk/api/stamp",{key:c.key,stamp:st}).then(function(){
  var old=p.querySelector(".stamp");if(old)old.remove();p.appendChild(stampEl(st));p.classList.add("done");c.stamp=st;
  if(navigator.vibrate)try{navigator.vibrate(30)}catch(_){}},function(e){toast("실패: "+e.message)})}
function render(s){renderAppr(s);renderPile(s)}
function refresh(){api("/desk/api/items").then(render,function(e){if(e.status===401)location.reload()})}
$("today").textContent=new Date().toLocaleDateString("ko-KR",{month:"long",day:"numeric",weekday:"long"});
render(SNAP);setInterval(refresh,15000);
"""
    return _page("사장님 책상", body, script)
