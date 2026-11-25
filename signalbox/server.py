"""Tiny zero-dependency web UI: ranked inbox + one-click labeling (which grows your eval set)."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .loaders import load_json, save_json
from .scorer import MODES, Triager

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signal — what actually needs you</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#6b6a66;--line:#e4e2dc;--hot:#c2410c;--warm:#a16207;--ok:#15803d}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1e1c;--ink:#f1efe8;--mute:#a19f97;--line:#33322e;--hot:#fb923c;--warm:#facc15;--ok:#4ade80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}.sub{color:var(--mute);margin:0 0 24px}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:28px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.stat b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.stat span{color:var(--mute);font-size:12px}
select{margin-left:auto;padding:8px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);margin:28px 0 10px}
.msg{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:10px}
.needs_you{border-left-color:var(--hot)}.worth_a_look{border-left-color:var(--warm)}
.top{display:flex;gap:10px;align-items:baseline}.from{font-weight:600}.subj{color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.score{font-variant-numeric:tabular-nums;color:var(--mute);font-size:12px}
.body{margin:6px 0;white-space:pre-wrap}.why{font-size:13px;color:var(--mute)}
.sig{display:flex;gap:14px;margin-top:8px;font-size:12px;color:var(--mute)}
.meter{display:inline-block;width:46px;height:6px;border-radius:3px;background:var(--line);vertical-align:middle;margin-left:4px;overflow:hidden}
.meter i{display:block;height:100%;background:var(--ink)}
.lab{margin-left:auto;display:flex;gap:6px}.lab button{border:1px solid var(--line);background:none;color:var(--mute);border-radius:6px;padding:2px 8px;cursor:pointer}
.lab button.on{background:var(--ink);color:var(--bg)}
details summary{cursor:pointer;color:var(--mute);margin:28px 0 10px}
.low{padding:8px 14px}.low .body{display:none}
.msg.sel{outline:2px solid var(--ink);outline-offset:2px}
input[type=search]{flex:1;min-width:160px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
.keys{color:var(--mute);font-size:12px;margin:-16px 0 20px}kbd{border:1px solid var(--line);border-radius:4px;padding:0 4px;font-size:11px}
</style></head><body><main>
<h1>Signal</h1><p class="sub">The few messages that need <em>you</em> — everything automated is out of the way.</p>
<div class="bar" id="stats"></div>
<p class="keys"><kbd>/</kbd> search · <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>y</kbd> needs me · <kbd>n</kbd> doesn't</p>
<div id="list"></div>
</main>
<script>
const $=s=>document.querySelector(s);
const esc=s=>(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let mode=new URLSearchParams(location.search).get("mode")||"full";
let q="",sel=0,data=null;
function meter(label,v){return `<span>${label}<span class="meter"><i style="width:${Math.round(v*100)}%"></i></span></span>`}
function card(t){const m=t.message,s=t.signals;
 const lab=m.label===true?"yes":m.label===false?"no":"";
 return `<div class="msg ${t.tier}" data-id="${esc(m.id)}">
 <div class="top"><span class="from">${esc(m.sender_name||m.sender)}</span><span class="subj">${esc(m.subject)}</span>
 <span class="score">${t.score.toFixed(2)}</span></div>
 <div class="body">${esc(m.body)}</div><div class="why">${esc(t.why)}</div>
 <div class="sig">${meter("automated",s.automation.score)}${meter("relationship",s.relationship.score)}${meter("asks you",s.ask.score)}
 <span class="lab" title="Label it — labels feed the eval">
 <button class="${lab==="yes"?"on":""}" onclick="label('${esc(m.id)}',true)">needs me</button>
 <button class="${lab==="no"?"on":""}" onclick="label('${esc(m.id)}',false)">doesn't</button></span></div></div>`}
async function load(){data=await (await fetch("/api/triage?mode="+mode)).json();render()}
function render(){
 const d=data,needle=q.toLowerCase();
 const hit=t=>!needle||[t.message.sender_name,t.message.sender,t.message.subject,t.message.body].join(" ").toLowerCase().includes(needle);
 const g=k=>d.results.filter(t=>t.tier===k&&hit(t));
 const auto=d.results.filter(t=>t.signals.automation.score>=.5).length;
 $("#stats").innerHTML=`<div class="stat"><b>${d.results.length}</b><span>inbound</span></div>
 <div class="stat"><b>${g("needs_you").length}</b><span>need you</span></div>
 <div class="stat"><b>${auto}</b><span>automated / agents</span></div>
 <input type="search" id="q" placeholder="Search…" value="${esc(q)}" oninput="q=this.value;sel=0;render();this.focus()">
 <select onchange="mode=this.value;load()">${d.modes.map(x=>`<option ${x===mode?"selected":""}>${x}</option>`).join("")}</select>`;
 $("#list").innerHTML=`<h2>Needs you</h2>${g("needs_you").map(card).join("")||"<p>Nothing. Enjoy.</p>"}
 <h2>Worth a look</h2>${g("worth_a_look").map(card).join("")}
 <details ${needle?"open":""}><summary>Filtered out (${g("low").length})</summary><div class="lowwrap">${g("low").map(t=>card(t).replace('class="msg low','class="msg low')).join("")}</div></details>`;
 document.querySelectorAll(".msg.low").forEach(e=>e.classList.add("low"));
 const q2=$("#q");if(document.activeElement!==q2&&needle){q2.focus();q2.setSelectionRange(q.length,q.length)}
 highlight();
}
function visible(){return [...document.querySelectorAll(".msg")].filter(e=>e.offsetParent!==null)}
function highlight(scroll){const v=visible();sel=Math.max(0,Math.min(sel,v.length-1));
 document.querySelectorAll(".msg.sel").forEach(e=>e.classList.remove("sel"));
 if(v[sel]){v[sel].classList.add("sel");if(scroll)v[sel].scrollIntoView({block:"nearest"})}}
document.addEventListener("keydown",e=>{
 if(e.target.tagName==="INPUT"){if(e.key==="Escape")e.target.blur();return}
 if(e.key==="/"){e.preventDefault();$("#q").focus();return}
 if(e.key==="j"){sel++;highlight(true)}else if(e.key==="k"){sel--;highlight(true)}
 else if(e.key==="y"||e.key==="n"){const c=visible()[sel];if(c)label(c.dataset.id,e.key==="y")}
});
async function label(id,v){await fetch("/api/label",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({id,label:v})});load()}
load();
</script></body></html>"""


def serve(path: str | Path, host: str = "127.0.0.1", port: int = 8000, ask_detector=None) -> None:
    path = Path(path)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/triage":
                mode = parse_qs(url.query).get("mode", ["full"])[0]
                me, msgs = load_json(path)
                results = Triager(me, msgs, mode=mode, ask_detector=ask_detector).triage()
                self._json({"modes": list(MODES), "results": [t.to_dict() for t in results]})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/api/label":
                return self._json({"error": "not found"}, 404)
            req = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
            me, msgs = load_json(path)
            for m in msgs:
                if m.id == req["id"]:
                    m.label = req["label"]
            save_json(path, me, msgs)
            self._json({"ok": True})

        def log_message(self, *args):
            pass

    print(f"Signal running on http://{host}:{port}  (data: {path})")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
