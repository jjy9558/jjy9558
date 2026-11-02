"""최소 로컬 웹 UI (http.server, 단일 HTML)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .llm import LLMError

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>GM TRPG</title>
<style>
body{margin:0;font-family:system-ui,sans-serif;background:#16161d;color:#e8e8ee;display:flex;height:100vh}
#main{flex:1;display:flex;flex-direction:column;min-width:0}
#log{flex:1;overflow-y:auto;padding:16px}
.msg{margin:0 0 12px;white-space:pre-wrap;line-height:1.55}.me{color:#8fc7ff}.sys{color:#9a9ab0;font-size:.9em}
form{display:flex;gap:8px;padding:12px;border-top:1px solid #2c2c3a}
input{flex:1;padding:10px;border-radius:6px;border:1px solid #3a3a4c;background:#1e1e29;color:inherit;font-size:1em}
button{padding:10px 16px;border-radius:6px;border:0;background:#5a7dff;color:#fff;cursor:pointer}
#side{width:280px;border-left:1px solid #2c2c3a;padding:16px;overflow-y:auto;background:#1b1b25;font-size:.92em}
#side h3{margin:14px 0 4px;font-size:.95em;color:#9fb0ff}pre{margin:0;white-space:pre-wrap;font-family:inherit}
@media(max-width:700px){body{flex-direction:column}#side{width:auto;max-height:35vh;border-left:0;border-top:1px solid #2c2c3a}}
</style></head><body>
<div id="main"><div id="log"></div>
<form id="f"><input id="t" autocomplete="off" placeholder="행동을 입력하거나 /도움말" autofocus><button>전송</button></form></div>
<div id="side"></div>
<script>
const log=document.getElementById('log'),side=document.getElementById('side');
function add(cls,text){const d=document.createElement('div');d.className='msg '+cls;d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight}
function sec(t,v){const h=document.createElement('h3');h.textContent=t;const p=document.createElement('pre');p.textContent=v||'-';side.append(h,p)}
function render(s){side.textContent='';sec('세계',s.name+' (턴 '+s.turn+')');sec('플레이어',s.player+'  HP '+s.hp+'/'+s.max_hp);
sec('위치',s.location);sec('주변 NPC',s.npcs.join('\\n'));sec('소지품',s.inventory.join('\\n'));
sec('퀘스트',s.quests.join('\\n'));sec('지도',s.map)}
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});return r.json()}
document.getElementById('f').onsubmit=async e=>{e.preventDefault();const i=document.getElementById('t'),v=i.value.trim();if(!v)return;i.value='';
add('me','> '+v);try{const r=await api('/api/chat',{text:v});add(r.error?'sys':'',r.error||r.reply);if(r.state)render(r.state)}catch(x){add('sys','오류: '+x)}};
api('/api/state').then(render);
</script></body></html>"""


def make_handler(gm, lock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False))

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html; charset=utf-8")
            elif path == "/api/state":
                with lock:
                    self._json(200, gm.state_view())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            if path != "/api/chat":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 20000:
                    return self._json(413, {"error": "too large"})
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                text = body["text"].strip()
                if not text:
                    raise ValueError
            except (ValueError, KeyError, TypeError, AttributeError):
                return self._json(400, {"error": "text 필드가 필요합니다."})
            with lock:
                try:
                    reply, res = gm.handle(text)
                except LLMError as e:
                    return self._json(502, {"error": "LLM 오류: %s" % e})
                self._json(200, {"reply": reply, "is_turn": res is not None,
                                 "rolls": res.rolls if res else [],
                                 "state": gm.state_view()})
    return Handler


def make_server(gm, host="127.0.0.1", port=8766):
    return ThreadingHTTPServer((host, port), make_handler(gm, threading.Lock()))
