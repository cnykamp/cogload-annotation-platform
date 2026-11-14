#!/usr/bin/env python3
"""ARC-CognitiveLoad multimodal annotation platform (working prototype).

Four annotation tasks, one canonical time base:
  /            completeness + QC dashboard      (modality/sensor validation)
  /sync/<sid>  N-pane synchronised player       (sync verification)
  /seg/<sid>   sub-session boundary editor      (segment timestamps)
  /tx/<sid>    transcript + speaker curation    (transcript curation)

Every annotation is written back as absolute nanoseconds on the master
timeline (ROS bag global start), never as "seconds into file X".
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file

HERE = Path(__file__).resolve().parent
STORE = HERE / "store"
INDEX_PATH = STORE / "index.json"
ANNOT_PATH = STORE / "annotations.json"

# Media can live either in the repo (data_synced/<P>/<S>/...) or in a flat
# demo mirror (<MEDIA_ROOT>/<S>/...). Try both so the same app runs anywhere.
REPO_ROOT = Path(os.environ.get("ARC_REPO", HERE.parent / "ARC-CognitiveLoad"))
MEDIA_ROOT = Path(os.environ.get("ARC_MEDIA", REPO_ROOT / "data_synced"))

app = Flask(__name__)
INDEX = json.loads(INDEX_PATH.read_text())
SESSIONS = {s["session_id"]: s for s in INDEX["sessions"]}


def load_annots() -> dict:
    if ANNOT_PATH.exists():
        try:
            return json.loads(ANNOT_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"sync_verdicts": {}, "segments": {}, "transcripts": {}, "stream_verdicts": {}, "modality_offsets": {}}


def save_annots(data: dict) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    tmp = ANNOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(ANNOT_PATH)


def media_path(sid: str, kind: str, name: str) -> Path | None:
    s = SESSIONS.get(sid)
    if not s:
        return None
    ext = "mp4" if kind == "video" else "wav"
    for cand in (
        MEDIA_ROOT / sid / kind / f"{name}.{ext}",
        MEDIA_ROOT / s["participant"] / s["dir_name"] / kind / f"{name}.{ext}",
        MEDIA_ROOT / s["participant"] / sid / kind / f"{name}.{ext}",
    ):
        if cand.exists():
            return cand
    return None


# ---------------------------------------------------------------- media ----
@app.route("/media/<sid>/<kind>/<name>")
def media(sid, kind, name):
    if kind not in ("video", "audio"):
        abort(404)
    p = media_path(sid, kind, name)
    if not p:
        abort(404)
    # conditional=True gives us HTTP Range, which browsers need to seek.
    return send_file(p, conditional=True)


@app.route("/api/index")
def api_index():
    return jsonify(INDEX)


@app.route("/api/session/<sid>")
def api_session(sid):
    s = SESSIONS.get(sid)
    if not s:
        abort(404)
    a = load_annots()
    return jsonify(
        {
            "session": s,
            "saved": {
                "sync": a["sync_verdicts"].get(sid),
                "segments": a["segments"].get(sid),
                "transcript": a["transcripts"].get(sid),
                "streams": a["stream_verdicts"].get(sid),
                "offsets": a.get("modality_offsets", {}).get(sid),
            },
        }
    )


@app.route("/api/save/<kind>/<sid>", methods=["POST"])
def api_save(kind, sid):
    key = {
        "sync": "sync_verdicts",
        "segments": "segments",
        "transcript": "transcripts",
        "streams": "stream_verdicts",
        "offsets": "modality_offsets",
    }.get(kind)
    if not key or sid not in SESSIONS:
        abort(404)
    payload = request.get_json(force=True)
    s = SESSIONS[sid]
    payload["_saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload["_master_start_ns"] = s["master_start_ns"]
    payload["_ros_start_offset_s"] = s["ros_start_offset_s"]
    # Promote every media-relative time to absolute ns on the master timeline.
    if kind == "segments" and s["master_start_ns"]:
        for row in payload.get("rows", []):
            for side in ("start", "end"):
                v = row.get(f"{side}_media_s")
                if v is not None:
                    row[f"{side}_master_ns"] = int(
                        s["master_start_ns"] + (float(v) + s["ros_start_offset_s"]) * 1e9
                    )
    a = load_annots()
    a[key][sid] = payload
    save_annots(a)
    return jsonify({"ok": True, "saved": payload.get("_saved_at")})


@app.route("/api/export")
def api_export():
    """Canonical ns-based export of everything a human has verified."""
    a = load_annots()
    rows = []
    for sid, payload in a["segments"].items():
        s = SESSIONS.get(sid, {})
        for r in payload.get("rows", []):
            rows.append(
                {
                    "participant_id": s.get("participant"),
                    "session_id": sid,
                    "segment": r.get("segment"),
                    "start_master_ns": r.get("start_master_ns"),
                    "end_master_ns": r.get("end_master_ns"),
                    "start_media_s": r.get("start_media_s"),
                    "end_media_s": r.get("end_media_s"),
                    "boundary_uncertainty_s": r.get("uncertainty_s", 1.0),
                    "verified_by": payload.get("annotator", ""),
                    "verified_at": payload.get("_saved_at"),
                }
            )
    return jsonify({"segments": rows, "sync_verdicts": a["sync_verdicts"],
                    "stream_verdicts": a["stream_verdicts"],
                    "modality_offsets": a.get("modality_offsets", {})})


# ----------------------------------------------------------------- html ----
SHELL = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
:root{--bg:#0f1115;--panel:#171a21;--line:#272c36;--fg:#e6e9ef;--dim:#9aa3b2;
--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--info:#60a5fa;--acc:#a78bfa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--info);text-decoration:none}a:hover{text-decoration:underline}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;
gap:18px;align-items:baseline;flex-wrap:wrap;background:var(--panel);
position:sticky;top:0;z-index:20}
header h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.2px}
header nav a{font-size:13px;color:var(--dim)}header nav a.on{color:var(--fg);font-weight:600}
main{padding:20px;max-width:1500px;margin:0 auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:16px;margin-bottom:16px}
h2{font-size:14px;margin:0 0 12px;font-weight:650}
h3{font-size:13px;margin:16px 0 8px;color:var(--dim);font-weight:600;
text-transform:uppercase;letter-spacing:.6px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:5px 8px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;
letter-spacing:.5px;position:sticky;top:0;background:var(--panel)}
.pill{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;
font-weight:600}
.ok{background:rgba(74,222,128,.15);color:var(--ok)}
.warn{background:rgba(251,191,36,.15);color:var(--warn)}
.bad{background:rgba(248,113,113,.15);color:var(--bad)}
.dimp{background:rgba(154,163,178,.13);color:var(--dim)}
.info{background:rgba(96,165,250,.15);color:var(--info)}
.dim{color:var(--dim)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
button{background:var(--acc);color:#12131a;border:0;padding:7px 14px;
border-radius:7px;font-weight:650;cursor:pointer;font-size:13px}
button:hover{filter:brightness(1.1)}button.sec{background:var(--line);color:var(--fg)}
input,select,textarea{background:#0c0e13;color:var(--fg);border:1px solid var(--line);
border-radius:6px;padding:5px 8px;font:inherit;font-size:12.5px}
.grid{display:grid;gap:10px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 14px;font-size:12.5px}
.kv div:nth-child(odd){color:var(--dim)}
.note{border-left:3px solid var(--warn);padding:9px 12px;background:rgba(251,191,36,.07);
border-radius:0 7px 7px 0;font-size:12.5px;margin:10px 0}
.note.b{border-color:var(--bad);background:rgba(248,113,113,.07)}
.note.g{border-color:var(--ok);background:rgba(74,222,128,.07)}
#toast{position:fixed;bottom:18px;right:18px;background:var(--ok);color:#0b0d11;
padding:9px 16px;border-radius:8px;font-weight:650;opacity:0;transition:.25s;z-index:99}
</style></head><body>
<header><h1>ARC&#8209;CognitiveLoad &middot; annotation platform</h1>
<nav id=nav></nav><span class=dim style="margin-left:auto" id=ctx></span></header>
<main id=app>__BODY__</main><div id=toast></div>
<script>
const $=(s,r=document)=>r.querySelector(s);const $$=(s,r=document)=>[...r.querySelectorAll(s)];
function toast(m){const t=$('#toast');t.textContent=m;t.style.opacity=1;
setTimeout(()=>t.style.opacity=0,1800)}
function fmt(s){if(s==null||isNaN(s))return '--';const n=Math.max(0,s);
const m=Math.floor(n/60),x=(n%60);return m+':'+(x<10?'0':'')+x.toFixed(2)}
async function save(kind,sid,body){const r=await fetch(`/api/save/${kind}/${sid}`,
{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
const j=await r.json();toast(j.ok?'Saved to canonical store':'Save failed');return j}
__SCRIPT__
</script></body></html>"""


def page(title: str, body: str, script: str = "") -> Response:
    html = SHELL.replace("__TITLE__", title).replace("__BODY__", body).replace("__SCRIPT__", script)
    return Response(html, mimetype="text/html")


NAV = """
document.getElementById('nav').innerHTML =
 `<a href="/" class="${location.pathname=='/'?'on':''}">1 &middot; Completeness</a> &nbsp;
  <a href="/sync" class="${location.pathname.startsWith('/sync')?'on':''}">2 &middot; Sync (cameras)</a> &nbsp;
  <a href="/timeline" class="${location.pathname.startsWith('/timeline')?'on':''}">2b &middot; All modalities</a> &nbsp;
  <a href="/seg" class="${location.pathname.startsWith('/seg')?'on':''}">3 &middot; Segments</a> &nbsp;
  <a href="/tx" class="${location.pathname.startsWith('/tx')?'on':''}">4 &middot; Transcripts</a> &nbsp;
  <a href="/api/export">export</a>`;
"""


# ------------------------------------------------- 1. completeness / QC ----
@app.route("/")
def view_dashboard():
    body = """
<div class=card>
 <h2>Task 1 &middot; Modality completeness &amp; failure modes</h2>
 <div class=dim style="font-size:12.5px">Status is computed per (session &times; stream) against the
 <b>rig configuration</b> the session was run on, so pilot-rig absences read as
 <span class="pill dimp">absent by design</span> rather than failures. Click any session to open its QC record.</div>
 <div id=summary class=grid style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin:14px 0"></div>
 <div id=notes></div>
</div>
<div class=card><h2>Completeness matrix</h2>
 <div style="overflow:auto;max-height:70vh"><table id=matrix></table></div></div>
"""
    script = NAV + """
(async()=>{
 const idx=await (await fetch('/api/index')).json();
 const S=idx.sessions, cols=[...idx.cameras,...idx.mics,...idx.signals];
 const cnt=(st)=>S.reduce((a,s)=>a+Object.values(s.streams).filter(v=>v.status==st).length,0);
 const unexpected=S.reduce((a,s)=>a+s.n_unexpected_absent,0);
 $('#summary').innerHTML=[
  ['Sessions synced',S.length],
  ['Participants',new Set(S.map(s=>s.participant)).size],
  ['Labeled sessions',S.filter(s=>s.labeled).length+' / '+S.length],
  ['Transcribed',S.filter(s=>s.transcript_available).length+' / '+S.length],
  ['Unexpected absences',unexpected],
  ['Orphaned (raw only)',idx.orphans.length]
 ].map(([k,v])=>`<div class=card style="margin:0;padding:11px">
   <div class=dim style="font-size:11px;text-transform:uppercase;letter-spacing:.5px">${k}</div>
   <div style="font-size:21px;font-weight:700;margin-top:2px">${v}</div></div>`).join('');

 const pilots=S.filter(s=>s.rig=='pilot_feb2026').map(s=>s.session_id);
 const noTobii=S.filter(s=>s.streams.tobii_gaze.status!='present_ok').map(s=>s.session_id);
 $('#notes').innerHTML=`
  <div class="note">Two rig configurations in this corpus. <b>${pilots.length}</b> sessions
   (${pilots.join(', ')||'--'}) ran the Feb&nbsp;2026 pilot rig: no d435, no brio0, no ch0 EEG,
   <span class=mono>video4_ir</span> in place of the wall devkit. Their absences are expected.</div>
  <div class="note b">Eye tracking missing on <b>${noTobii.length}</b> sessions
   (${noTobii.slice(0,8).join(', ')}${noTobii.length>8?' &hellip;':''}).
   Where Tobii <i>is</i> present it is aligned by a UTC wall-clock delta with
   <b>no content-based verification</b> &mdash; the weakest sync link in the corpus.</div>
  ${idx.orphans.length?`<div class="note b"><b>${idx.orphans.length}</b> raw sessions never entered the sync
   pipeline and are invisible to every downstream analysis: <span class=mono>${idx.orphans.join(', ')}</span>.</div>`:''}`;

 const head='<tr><th>Session</th><th>Rig</th><th>Dur</th>'+cols.map(c=>
   `<th style="writing-mode:vertical-rl;transform:rotate(180deg);height:118px;font-size:10px">${c.replace(/^cameras_|^audio_|_image_raw$|_audio_raw$/g,'')}</th>`).join('')+'</tr>';
 const cell=v=>{const m={present_ok:['ok','&#10003;'],absent_by_design:['dimp','&ndash;'],
   absent_unexpected:['bad','&#10007;'],present_degraded:['warn','!']}[v.status]||['dimp','?'];
   return `<td title="${v.status}${v.note?' - '+v.note:''}"><span class="pill ${m[0]}">${m[1]}</span></td>`};
 $('#matrix').innerHTML=head+S.map(s=>`<tr>
   <td><a href="/sync/${s.session_id}" class=mono>${s.session_id}</a></td>
   <td><span class="pill ${s.rig=='main'?'info':'warn'}">${s.rig=='main'?'main':'pilot'}</span></td>
   <td class=dim>${Math.round(s.duration_s)}s</td>
   ${cols.map(c=>cell(s.streams[c]||{status:'absent_unexpected'})).join('')}</tr>`).join('');
})();
"""
    return page("Completeness -- ARC CogLoad", body, script)


# ---------------------------------------------- 2. sync verification -------
@app.route("/sync")
@app.route("/sync/<sid>")
def view_sync(sid=None):
    if sid is None:
        return page("Sync -- pick a session", _picker("sync", "Task 2 &middot; Synchronisation verification"), NAV + _picker_js("sync"))
    if sid not in SESSIONS:
        abort(404)
    body = """
<div class=card>
 <h2>Task 2 &middot; Sync verification &mdash; <span class=mono id=sid></span></h2>
 <div id=meta class=kv style="margin-bottom:10px"></div>
 <div id=warn></div>
 <div style="display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:12px 0">
  <button id=play>Play / Pause</button>
  <button class=sec data-step="-0.033">&#9664; frame</button>
  <button class=sec data-step="0.033">frame &#9654;</button>
  <button class=sec data-step="-1">&minus;1s</button>
  <button class=sec data-step="1">+1s</button>
  <label class=dim>offsets <input type=checkbox id=applyoff checked></label>
  <span class="mono" id=clock style="margin-left:auto;font-size:17px;font-weight:700"></span>
 </div>
 <input type=range id=scrub min=0 max=1000 value=0 style="width:100%">
 <div id=panes class=grid style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr));margin-top:12px"></div>
</div>
<div class=card><h2>Verdict</h2>
 <div class=dim style="font-size:12.5px;margin-bottom:10px">Step to a blink or a sharp head movement and
 check it lands on the same frame in every pane. Thermal will not show blinks &mdash; use head motion there.</div>
 <div style="display:flex;gap:9px;align-items:center;flex-wrap:wrap">
  <select id=verdict>
   <option value="">-- verdict --</option>
   <option value=aligned>aligned (&lt;1 frame)</option>
   <option value=minor>minor drift (&lt; 100 ms)</option>
   <option value=off>visibly off</option>
   <option value=unverifiable>unverifiable (no usable anchor)</option>
  </select>
  <input id=annotator placeholder="your initials" style="width:120px">
  <input id=vnote placeholder="note (which pane, how far off)" style="flex:1;min-width:220px">
  <button id=savev>Save verdict</button>
 </div><div id=saved class=dim style="margin-top:8px;font-size:12px"></div>
</div>"""
    script = NAV + """
const SID=location.pathname.split('/').pop();
(async()=>{
 const d=await (await fetch('/api/session/'+SID)).json(); const s=d.session;
 $('#sid').textContent=SID; $('#ctx').textContent=s.participant+' / '+Math.round(s.duration_s)+'s';
 const sync=s.sync||{cameras:{}};
 $('#meta').innerHTML=[['clock stream',s.clock_stream],['fps',(s.fps||0).toFixed(3)],
  ['duration',Math.round(s.duration_s)+' s'],['ROS start offset',s.ros_start_offset_s.toFixed(6)+' s'],
  ['master start (ns)',s.master_start_ns],['fine-sync variant',sync.variant||'none']]
  .map(([k,v])=>`<div>${k}</div><div class=mono>${v}</div>`).join('');

 const cams=s.cameras; const off=c=>{const o=sync.cameras[c];return (o&&o.trusted)?o.offset_s:0};
 const untrusted=cams.filter(c=>{const o=sync.cameras[c];return o&&!o.trusted});
 const nofine=cams.filter(c=>!sync.cameras[c]);
 let w='';
 if(untrusted.length)w+=`<div class="note">Fine offset estimated but <b>not trusted</b> for
  ${untrusted.map(c=>'<span class=mono>'+c.replace('cameras_','')+'</span>').join(', ')}
  &mdash; status is not <span class=mono>ok</span>/<span class=mono>reference</span>, so the player
  leaves these at their coarse ROS alignment rather than pushing them further out.</div>`;
 if(nofine.length)w+=`<div class="note b">No fine-sync estimate at all for
  ${nofine.map(c=>'<span class=mono>'+c.replace('cameras_','')+'</span>').join(', ')}.
  These panes show <b>coarse ROS sync only</b>.</div>`;
 $('#warn').innerHTML=w;

 $('#panes').innerHTML=cams.map(c=>{const o=sync.cameras[c]||{};
  const cls=o.trusted?'ok':(o.status?'warn':'dimp');
  return `<div class=card style="margin:0;padding:9px">
   <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:6px">
    <span class=mono style="font-size:11.5px">${c.replace('cameras_','').replace('_image_raw','')}</span>
    <span class="pill ${cls}">${o.status||'coarse only'}${o.trusted?' '+o.offset_s.toFixed(2)+'s':''}</span></div>
   <video data-cam="${c}" src="/media/${SID}/video/${c}" preload=metadata muted
    style="width:100%;background:#000;border-radius:5px;aspect-ratio:4/3;object-fit:contain"></video>
   <div class=dim style="font-size:11px;margin-top:3px" id="st_${c}"></div></div>`}).join('');

 const vids=$$('#panes video'); let dur=s.duration_s||1, playing=false;
 const seek=t=>{t=Math.max(0,Math.min(dur,t));
  vids.forEach(v=>{const o=$('#applyoff').checked?off(v.dataset.cam):0;
   const lt=t-o; v.currentTime=Math.max(0,lt);
   const el=$('#st_'+v.dataset.cam); if(el)el.textContent='local '+lt.toFixed(3)+'s';});
  $('#clock').textContent=fmt(t); $('#scrub').value=(t/dur)*1000;};
 $('#scrub').oninput=e=>{if(playing){vids.forEach(v=>v.pause());playing=false}
  seek((e.target.value/1000)*dur)};
 $('#play').onclick=()=>{playing=!playing;
  if(playing){vids.forEach(v=>v.play().catch(()=>{}))}else{vids.forEach(v=>v.pause())}};
 $$('[data-step]').forEach(b=>b.onclick=()=>{if(playing){vids.forEach(v=>v.pause());playing=false}
  const o=$('#applyoff').checked?off(vids[0].dataset.cam):0;
  seek(vids[0].currentTime+o+parseFloat(b.dataset.step))});
 $('#applyoff').onchange=()=>seek(vids[0].currentTime+(($('#applyoff').checked)?0:off(vids[0].dataset.cam)));
 // master clock = first pane; keep the rest pinned to it while playing
 setInterval(()=>{if(!playing)return; const m=vids[0];
  const t=m.currentTime+($('#applyoff').checked?off(m.dataset.cam):0);
  $('#clock').textContent=fmt(t); $('#scrub').value=(t/dur)*1000;
  vids.slice(1).forEach(v=>{const o=$('#applyoff').checked?off(v.dataset.cam):0;
   if(Math.abs(v.currentTime-(t-o))>0.12)v.currentTime=Math.max(0,t-o)})},250);
 seek(0);

 if(d.saved.sync){$('#verdict').value=d.saved.sync.verdict||'';$('#vnote').value=d.saved.sync.note||'';
  $('#annotator').value=d.saved.sync.annotator||'';
  $('#saved').textContent='last saved '+d.saved.sync._saved_at;}
 $('#savev').onclick=async()=>{await save('sync',SID,{verdict:$('#verdict').value,
  note:$('#vnote').value,annotator:$('#annotator').value,
  offsets_applied:$('#applyoff').checked,
  trusted_cameras:cams.filter(c=>(sync.cameras[c]||{}).trusted)});
  $('#saved').textContent='saved just now';};
})();
"""
    return page(f"Sync {sid}", body, script)


# -------------------------------------------- 3. sub-session boundaries ----
@app.route("/seg")
@app.route("/seg/<sid>")
def view_seg(sid=None):
    if sid is None:
        return page("Segments -- pick a session", _picker("seg", "Task 3 &middot; Sub-session timestamps"), NAV + _picker_js("seg"))
    if sid not in SESSIONS:
        abort(404)
    body = """
<div class=card>
 <h2>Task 3 &middot; Sub-session boundaries &mdash; <span class=mono id=sid></span></h2>
 <div id=tb></div>
 <div id=timeline style="position:relative;height:56px;background:#0c0e13;border:1px solid var(--line);
  border-radius:7px;margin:14px 0;overflow:hidden;cursor:pointer"></div>
 <div class=dim style="font-size:11.5px;margin-bottom:12px">
  <span class="pill ok">labelled</span> <span class="pill bad">gap</span> &mdash; click the timeline to seek the reference video.</div>
 <video id=ref controls style="width:100%;max-width:620px;background:#000;border-radius:7px"></video>
</div>
<div class=card><h2>Boundaries <span class=dim style="font-weight:400">(media seconds; stored as master&nbsp;ns)</span></h2>
 <table id=segs></table>
 <div style="display:flex;gap:9px;margin-top:12px;flex-wrap:wrap;align-items:center">
  <input id=annotator placeholder="your initials" style="width:120px">
  <button id=savs>Save corrected boundaries</button>
  <span id=saved class=dim style="font-size:12px"></span></div>
</div>"""
    script = NAV + """
const SID=location.pathname.split('/').pop();
(async()=>{
 const d=await (await fetch('/api/session/'+SID)).json(); const s=d.session;
 $('#sid').textContent=SID; $('#ctx').textContent=s.participant;
 const dur=s.duration_s||1, off=s.ros_start_offset_s;
 const clipped=s.segments.filter(x=>x.clipped);
 $('#tb').innerHTML=`
  <div class="note g"><b>Time-base correction applied.</b> The sheet stores
   <span class=mono>start_timestamp_ns</span> against <b>ROS bag global start</b>, but synced media
   <span class=mono>t=0</span> is the clock video's first frame. This session's shift is
   <b>${off.toFixed(3)} s</b>; every boundary below is shown as
   <span class=mono>start_s &minus; ${off.toFixed(3)}</span>. Uncorrected, every label here sits
   ${off.toFixed(2)} s late against the video.</div>
  ${clipped.length?`<div class="note b"><b>${clipped.length}</b> boundary(ies) run past the end of the
   recording by up to ${Math.max(...clipped.map(c=>c.overruns_by_s)).toFixed(2)} s and were clipped
   to the clock duration.</div>`:''}
  <div class="note">Boundaries were read off <span class=mono>mm:ss</span>, so precision is
   <b>&plusmn;1 s</b>, and segments are butt-joined &mdash; no guard band for instruction reading or
   fNIRS haemodynamic lag. Set a per-boundary guard below.</div>`;

 const tl=$('#timeline'); const pct=t=>(t/dur)*100;
 tl.innerHTML=s.segments.map((x,i)=>`<div title="${x.segment}" style="position:absolute;left:${pct(x.start_media_s)}%;
   width:${pct(x.end_media_s-x.start_media_s)}%;top:0;bottom:0;background:${
   x.cognitive_load=='High'?'rgba(248,113,113,.4)':x.cognitive_load=='Low'?'rgba(251,191,36,.4)':'rgba(96,165,250,.4)'};
   border-right:1px solid var(--fg);font-size:10px;padding:3px;overflow:hidden">${x.segment.slice(0,22)}</div>`).join('')
  +s.gaps.map(g=>`<div title="unlabelled ${Math.round(g.end_s-g.start_s)}s" style="position:absolute;
   left:${pct(g.start_s)}%;width:${pct(g.end_s-g.start_s)}%;top:0;bottom:0;
   background:repeating-linear-gradient(45deg,rgba(248,113,113,.35) 0 6px,transparent 6px 12px)"></div>`).join('');

 const ref=$('#ref'); ref.src='/media/'+SID+'/video/'+(s.cameras[0]||'');
 tl.onclick=e=>{const r=tl.getBoundingClientRect();ref.currentTime=((e.clientX-r.left)/r.width)*dur};

 const rows=(d.saved.segments&&d.saved.segments.rows)||s.segments.map(x=>({segment:x.segment,
  start_media_s:x.start_media_s,end_media_s:x.end_media_s,uncertainty_s:1.0,
  cognitive_load:x.cognitive_load,gap_reason:''}));
 const render=()=>{$('#segs').innerHTML='<tr><th>Segment</th><th>Load</th><th>Start (media s)</th>'
  +'<th>End (media s)</th><th>Guard &plusmn;s</th><th>Orig mm:ss</th><th></th></tr>'
  +rows.map((r,i)=>`<tr>
   <td>${r.segment}</td><td><span class="pill ${r.cognitive_load=='High'?'bad':r.cognitive_load=='Low'?'warn':'info'}">${r.cognitive_load||'--'}</span></td>
   <td><input class=mono data-i=${i} data-f=start_media_s value="${r.start_media_s}" style="width:92px"></td>
   <td><input class=mono data-i=${i} data-f=end_media_s value="${r.end_media_s}" style="width:92px"></td>
   <td><input class=mono data-i=${i} data-f=uncertainty_s value="${r.uncertainty_s}" style="width:62px"></td>
   <td class=dim>${(s.segments[i]||{}).start_mm_ss||''}&ndash;${(s.segments[i]||{}).end_mm_ss||''}</td>
   <td><button class=sec data-go=${i} style="padding:3px 9px">go</button></td></tr>`).join('');
  $$('#segs input').forEach(el=>el.onchange=()=>{rows[el.dataset.i][el.dataset.f]=parseFloat(el.value)});
  $$('#segs [data-go]').forEach(b=>b.onclick=()=>{ref.currentTime=rows[b.dataset.go].start_media_s;ref.play()});};
 render();
 if(d.saved.segments){$('#annotator').value=d.saved.segments.annotator||'';
  $('#saved').textContent='last saved '+d.saved.segments._saved_at;}
 $('#savs').onclick=async()=>{await save('segments',SID,{rows,annotator:$('#annotator').value,
  anchor:'master_ros_global_start',source_precision_s:1.0});
  $('#saved').textContent='saved just now';};
})();
"""
    return page(f"Segments {sid}", body, script)


# ------------------------------------------------ 4. transcript curation ---
@app.route("/tx")
@app.route("/tx/<sid>")
def view_tx(sid=None):
    if sid is None:
        return page("Transcripts -- pick a session", _picker("tx", "Task 4 &middot; Transcript curation"), NAV + _picker_js("tx"))
    if sid not in SESSIONS:
        abort(404)
    body = """
<div class=card>
 <h2>Task 4 &middot; Transcript curation &mdash; <span class=mono id=sid></span></h2>
 <div id=tb></div>
 <audio id=au controls style="width:100%;margin-top:10px"></audio>
</div>
<div class=card><h2>Segments</h2>
 <div style="display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
  <input id=annotator placeholder="your initials" style="width:120px">
  <button id=savt>Save curated transcript</button>
  <span id=saved class=dim style="font-size:12px"></span>
  <span class=dim style="margin-left:auto;font-size:12px" id=stats></span></div>
 <div style="max-height:62vh;overflow:auto"><table id=tx></table></div>
</div>"""
    script = NAV + """
const SID=location.pathname.split('/').pop();
const HALL=/^(so|you|thank you\\.?|thanks|i love you\\.?|bye|okay\\.?|\\.|,)$/i;
(async()=>{
 const d=await (await fetch('/api/session/'+SID)).json(); const s=d.session;
 $('#sid').textContent=SID; $('#ctx').textContent=s.participant;
 const t=s.transcript;
 if(!t){$('#tb').innerHTML='<div class="note b">No transcript for this session.</div>';return}
 const roles=(t.roles||{}).speaker_roles||{};
 const segs=t.segments||[];
 const susp=segs.filter(x=>HALL.test((x.text||'').trim())||((x.end_s-x.start_s)>15&&(x.text||'').trim().split(/\\s+/).length<5));
 $('#tb').innerHTML=`
  <div class=kv style="margin-bottom:8px">
   <div>mic</div><div class=mono>${t.mic}</div>
   <div>source</div><div class=mono>${t.source}</div>
   <div>role method</div><div class=mono>${(t.roles||{}).method||'--'}</div>
   <div>segments</div><div class=mono>${segs.length} over ${Math.round(s.duration_s)}s
    (~${(s.duration_s/Math.max(1,segs.length)).toFixed(0)}s each)</div></div>
  <div class="note">Whisper output is <b>segment-level only</b> &mdash; no word timestamps &mdash; so speech
   cannot be aligned to sub-session boundaries at better than ~${(s.duration_s/Math.max(1,segs.length)).toFixed(0)}s.
   Re-extraction with word timestamps is the prerequisite for fine-grained curation.</div>
  ${susp.length?`<div class="note b"><b>${susp.length}</b> segment(s) auto-flagged as probable ASR
   hallucination over silence (stock filler text, or a long span with almost no words).
   These are pre-marked <span class="pill bad">drop</span> below &mdash; confirm or clear.</div>`:''}
  <div class="note">Speaker roles were assigned by the
   <span class=mono>${(t.roles||{}).method||'dominant duration'}</span> heuristic &mdash; whoever talks
   longer becomes the participant. Override per segment where it is wrong.</div>`;
 $('#au').src='/media/'+SID+'/audio/'+t.mic;

 const rows=(d.saved.transcript&&d.saved.transcript.rows)||segs.map(x=>({
  start_s:x.start_s,end_s:x.end_s,asr:x.text||'',text:x.text||'',
  speaker:x.speaker_label||x.speaker||'',
  role:(roles[x.speaker_label]||{}).role||x.speaker_role||'',
  drop:HALL.test((x.text||'').trim())||((x.end_s-x.start_s)>15&&(x.text||'').trim().split(/\\s+/).length<5),
  verified:false}));
 const render=()=>{
  $('#tx').innerHTML='<tr><th style="width:74px">Start</th><th style="width:88px">Role</th>'
   +'<th>Text (editable)</th><th style="width:52px">Drop</th><th style="width:40px"></th></tr>'
   +rows.map((r,i)=>`<tr style="${r.drop?'opacity:.45':''}">
    <td class=mono style="font-size:11.5px"><a href="#" data-go=${i}>${fmt(r.start_s)}</a></td>
    <td><select data-i=${i} data-f=role style="width:82px;font-size:11.5px">
      ${['','participant','experimenter','unknown'].map(o=>`<option ${o==r.role?'selected':''}>${o}</option>`).join('')}
     </select></td>
    <td><input data-i=${i} data-f=text value="${(r.text||'').replace(/"/g,'&quot;')}" style="width:100%">
     ${r.text!=r.asr?`<div class=dim style="font-size:10.5px">ASR: ${r.asr}</div>`:''}</td>
    <td style="text-align:center"><input type=checkbox data-i=${i} data-f=drop ${r.drop?'checked':''}></td>
    <td style="text-align:center"><input type=checkbox data-i=${i} data-f=verified ${r.verified?'checked':''} title=verified></td></tr>`).join('');
  $$('#tx [data-f]').forEach(el=>el.onchange=()=>{
   rows[el.dataset.i][el.dataset.f]=el.type=='checkbox'?el.checked:el.value; render()});
  $$('#tx [data-go]').forEach(a=>a.onclick=e=>{e.preventDefault();
   $('#au').currentTime=rows[a.dataset.go].start_s;$('#au').play()});
  $('#stats').textContent=`${rows.filter(r=>r.verified).length} verified / ${rows.filter(r=>r.drop).length} dropped / ${rows.length} total`;};
 render();
 if(d.saved.transcript){$('#annotator').value=d.saved.transcript.annotator||'';
  $('#saved').textContent='last saved '+d.saved.transcript._saved_at;}
 $('#savt').onclick=async()=>{await save('transcript',SID,{rows,annotator:$('#annotator').value,
  mic:t.mic,asr_model:'whisper-large-v3',diarizer:'pyannote-3.1'});
  $('#saved').textContent='saved just now';};
})();
"""
    return page(f"Transcript {sid}", body, script)



# ------------------------------------ 2b. all modalities on one clock ------
@app.route("/api/signals/<sid>")
def api_signals(sid):
    p = STORE / "signals" / f"{sid}.json"
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="application/json")


@app.route("/timeline")
@app.route("/timeline/<sid>")
def view_timeline(sid=None):
    if sid is None:
        return page("Timeline -- pick a session", _picker("timeline", "Task 2b &middot; All modalities on one clock"), NAV + _picker_js("timeline"))
    if sid not in SESSIONS:
        abort(404)
    body = """
<div class=card>
 <h2>Task 2b &middot; Every modality on the master clock &mdash; <span class=mono id=sid></span></h2>
 <div id=intro></div>
 <div style="display:grid;grid-template-columns:300px 1fr;gap:14px;margin-top:10px;align-items:start">
  <div>
   <video id=ref muted style="width:100%;background:#000;border-radius:7px;aspect-ratio:4/3;object-fit:contain"></video>
   <div class=dim style="font-size:11px;margin-top:4px" id=refname></div>
   <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
    <button id=play>Play / Pause</button>
    <button class=sec data-step="-0.1">&minus;0.1s</button><button class=sec data-step="0.1">+0.1s</button>
    <button class=sec data-step="-5">&minus;5s</button><button class=sec data-step="5">+5s</button>
   </div>
   <div style="margin-top:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
    <label class=dim>window <select id=win><option>10</option><option selected>30</option><option>60</option><option>120</option><option>600</option></select> s</label>
    <label class=dim><input type=checkbox id=applyoff checked> apply offsets</label>
   </div>
   <div class="mono" id=clock style="font-size:20px;font-weight:700;margin-top:8px"></div>
  </div>
  <div>
   <canvas id=cv style="width:100%;height:560px;background:#0c0e13;border:1px solid var(--line);border-radius:7px;cursor:crosshair"></canvas>
   <input type=range id=scrub min=0 max=10000 value=0 style="width:100%;margin-top:6px">
  </div>
 </div>
</div>
<div class=card><h2>Per-modality offsets <span class=dim style="font-weight:400">(seconds to add so the stream lands on the master clock)</span></h2>
 <div class=dim style="font-size:12.5px;margin-bottom:8px">Arrival delta = when the ROS bridge received the stream's first message (already applied by <span class=mono>data_synced</span>).
  Offset = the residual you set here after checking an event lands at the same instant in every lane. Drift and jitter are measured from the timestamps themselves.</div>
 <table id=offs></table>
 <div style="display:flex;gap:9px;margin-top:12px;flex-wrap:wrap;align-items:center">
  <input id=annotator placeholder="your initials" style="width:120px">
  <button id=saveo>Save modality offsets</button>
  <button class=sec id=reset>Reset to pipeline defaults</button>
  <span id=saved class=dim style="font-size:12px"></span></div>
</div>"""
    script = NAV + r"""
const SID=location.pathname.split('/').pop();
(async()=>{
 const [d,sig]=await Promise.all([fetch('/api/session/'+SID).then(r=>r.json()),
                                  fetch('/api/signals/'+SID).then(r=>r.ok?r.json():null)]);
 const s=d.session; $('#sid').textContent=SID; $('#ctx').textContent=s.participant;
 if(!sig){$('#intro').innerHTML='<div class="note b">No signal extract for this session yet (run <span class=mono>signals_build.py '+SID+'</span>).</div>';return}
 const m=sig.meta, fn=m.fnirs||{}, tb=m.tobii||{};
 $('#intro').innerHTML=`
  <div class="note g"><b>All ${sig.lanes.length} lanes below are placed by their own ROS timestamps</b> on one axis
   (master = ROS bag global start). If the pipeline is right, a blink, a word, a head turn and its haemodynamic
   response should line up across video, audio, biosensors and fNIRS. Drag the offset for any lane that does not.</div>
  <div class=kv style="margin-top:8px">
   <div>fNIRS LSL clock vs ROS clock</div><div class=mono>${fn.lsl_vs_ros_drift_ppm!=null?fn.lsl_vs_ros_drift_ppm+' ppm drift ('+(fn.lsl_vs_ros_drift_ppm*sig.duration_s/1e6*1000).toFixed(0)+' ms over the session), bridge lag '+fn.lsl_fit_lag_s+' s, chunk residual p99 '+fn.chunk_resid_p99_ms+' ms':'n/a'}</div>
   <div>fNIRS arrival</div><div class=mono>${fn.n_chunks||'?'} ROS chunks for ${fn.n_samples||'?'} samples &mdash; one timestamp per bundle, hence the two fNIRS lanes (arrival vs de-jittered)</div>
   <div>Tobii</div><div class=mono>${tb.status=='ok'?'UTC delta '+tb.utc_offset_s+' s, span '+tb.span_s+' s, '+tb.n_rows+' rows — unverified':tb.status}</div>
  </div>`;

 const ref=$('#ref'); ref.src='/media/'+SID+'/video/'+(sig.reference_camera||s.cameras[0]);
 $('#refname').textContent=(sig.reference_camera||'')+' (media t = master t − '+sig.clock_ros_start_offset_s.toFixed(3)+')';
 const coff=sig.clock_ros_start_offset_s, dur=sig.duration_s+coff, hz=sig.grid_hz;
 const lanes=sig.lanes; const groupCol={video:'#60a5fa',audio:'#fbbf24',fnirs:'#f87171',biosensor:'#4ade80',gaze:'#a78bfa'};
 // z-score each lane once for drawing
 lanes.forEach(l=>{const v=l.values.filter(x=>x!=null);const mu=v.reduce((a,b)=>a+b,0)/Math.max(1,v.length);
  const sd=Math.sqrt(v.reduce((a,b)=>a+(b-mu)*(b-mu),0)/Math.max(1,v.length))||1;
  l.z=l.values.map(x=>x==null?null:Math.max(-3,Math.min(3,(x-mu)/sd)));
  l.offset=(d.saved.offsets&&d.saved.offsets.rows&&(d.saved.offsets.rows.find(r=>r.id==l.id)||{}).offset_s);
  if(l.offset==null)l.offset=l.default_offset_s||0;});

 let T=0, playing=false;
 const cv=$('#cv'), ctx=cv.getContext('2d');
 function draw(){const W=cv.clientWidth,H=cv.clientHeight;if(cv.width!=W*2){cv.width=W*2;cv.height=H*2}
  ctx.setTransform(2,0,0,2,0,0);ctx.clearRect(0,0,W,H);
  const win=parseFloat($('#win').value), t0=Math.max(0,T-win/2), t1=t0+win, lh=H/lanes.length;
  const x=t=>((t-t0)/win)*W;
  // grid seconds
  ctx.strokeStyle='#1e232d';ctx.lineWidth=1;ctx.fillStyle='#5b6474';ctx.font='10px ui-monospace,monospace';
  const step=win<=10?1:win<=60?5:win<=120?10:60;
  for(let t=Math.ceil(t0/step)*step;t<=t1;t+=step){ctx.beginPath();ctx.moveTo(x(t),0);ctx.lineTo(x(t),H);ctx.stroke();ctx.fillText(fmt(t),x(t)+3,H-4)}
  lanes.forEach((l,i)=>{const y0=i*lh, ym=y0+lh/2, amp=lh*0.42, off=$('#applyoff').checked?l.offset:0;
   ctx.strokeStyle='#1e232d';ctx.beginPath();ctx.moveTo(0,y0+lh);ctx.lineTo(W,y0+lh);ctx.stroke();
   ctx.strokeStyle=groupCol[l.group]||'#ccc';ctx.lineWidth=1.2;ctx.beginPath();let pen=false;
   const i0=Math.max(0,Math.floor((t0-off)*hz)), i1=Math.min(l.z.length,Math.ceil((t1-off)*hz)+1);
   for(let k=i0;k<i1;k++){const z=l.z[k];if(z==null){pen=false;continue}
    const px=x(k/hz+off), py=ym-z/3*amp; if(!pen){ctx.moveTo(px,py);pen=true}else ctx.lineTo(px,py)}
   ctx.stroke();
   ctx.fillStyle='#cbd2dd';ctx.font='11px ui-sans-serif,system-ui';ctx.fillText(l.label+(l.offset?'  ('+(l.offset>0?'+':'')+l.offset.toFixed(3)+'s)':''),6,y0+13);});
  ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(x(T),0);ctx.lineTo(x(T),H);ctx.stroke();
  $('#clock').textContent=fmt(T)+'  master'; $('#scrub').value=(T/dur)*10000;}
 function seek(t){T=Math.max(0,Math.min(dur,t)); ref.currentTime=Math.max(0,T-coff); draw();}
 cv.onclick=e=>{const r=cv.getBoundingClientRect();const win=parseFloat($('#win').value);
  const t0=Math.max(0,T-win/2);seek(t0+((e.clientX-r.left)/r.width)*win)};
 $('#scrub').oninput=e=>seek((e.target.value/10000)*dur);
 $('#win').onchange=draw; $('#applyoff').onchange=draw;
 $$('[data-step]').forEach(b=>b.onclick=()=>seek(T+parseFloat(b.dataset.step)));
 $('#play').onclick=()=>{playing=!playing;playing?ref.play():ref.pause()};
 setInterval(()=>{if(playing){T=ref.currentTime+coff;draw()}},100);
 window.addEventListener('resize',draw);

 function table(){$('#offs').innerHTML='<tr><th>Lane</th><th>Group</th><th>Time base</th><th>Arrival &Delta; (s)</th><th>Jitter / drift</th><th>Offset to add (s)</th></tr>'
  +lanes.map((l,i)=>`<tr><td>${l.label}</td><td><span class=pill style="background:${groupCol[l.group]}22;color:${groupCol[l.group]}">${l.group}</span></td>
   <td class=dim style="font-size:11.5px">${l.timebase||''}${l.note?'<br>'+l.note:''}</td>
   <td class=mono>${l.arrival_delta_s!=null?(+l.arrival_delta_s).toFixed(3):'--'}</td>
   <td class=mono style="font-size:11px">${l.jitter?('dt '+l.jitter.median_dt_ms+'ms, p99 '+l.jitter.p99_dt_ms+'ms'):(l.group=='fnirs'&&fn.lsl_vs_ros_drift_ppm!=null?fn.lsl_vs_ros_drift_ppm+' ppm':(l.fine_status||''))}</td>
   <td><input class=mono data-i=${i} type=number step=0.01 value="${l.offset.toFixed(3)}" style="width:92px"></td></tr>`).join('');
  $$('#offs input').forEach(el=>el.onchange=()=>{lanes[el.dataset.i].offset=parseFloat(el.value)||0;draw()});}
 table(); seek(0);
 $('#reset').onclick=()=>{lanes.forEach(l=>l.offset=l.default_offset_s||0);table();draw()};
 if(d.saved.offsets){$('#annotator').value=d.saved.offsets.annotator||'';$('#saved').textContent='last saved '+d.saved.offsets._saved_at}
 $('#saveo').onclick=async()=>{await save('offsets',SID,{annotator:$('#annotator').value,
   rows:lanes.map(l=>({id:l.id,group:l.group,offset_s:l.offset,default_offset_s:l.default_offset_s||0,arrival_delta_s:l.arrival_delta_s}))});
   $('#saved').textContent='saved just now'};
})();
"""
    return page(f"Timeline {sid}", body, script)

# --------------------------------------------------------------- picker ----
def _picker(kind: str, title: str) -> str:
    return f"""<div class=card><h2>{title}</h2>
 <div class=dim style="font-size:12.5px">Pick a session. Sessions whose media is present on this host
 are playable; the rest open with metadata only.</div>
 <div id=list class=grid style="grid-template-columns:repeat(auto-fill,minmax(210px,1fr));margin-top:14px"></div></div>"""


def _picker_js(kind: str) -> str:
    return """
(async()=>{const idx=await (await fetch('/api/index')).json();
 $('#list').innerHTML=idx.sessions.map(s=>{
  const ok=s.__KIND__=='tx'?s.transcript_available:(s.__KIND__=='seg'?s.labeled:true);
  const trusted=Object.values((s.sync||{cameras:{}}).cameras).filter(c=>c.trusted).length;
  return `<a href="/__KIND__/${s.session_id}" class=card style="margin:0;padding:11px;display:block">
   <div class=mono style="font-weight:650">${s.session_id}</div>
   <div class=dim style="font-size:11.5px;margin-top:3px">
    ${Math.round(s.duration_s)}s &middot; ${s.cameras.length} cams &middot; ${s.segments.length} segs</div>
   <div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap">
    <span class="pill ${s.rig=='main'?'info':'warn'}">${s.rig=='main'?'main':'pilot'}</span>
    ${trusted?`<span class="pill ok">${trusted} fine-synced</span>`:'<span class="pill dimp">coarse only</span>'}
    ${ok?'':'<span class="pill bad">no data</span>'}</div></a>`}).join('');})();
""".replace("__KIND__", kind)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"index: {len(SESSIONS)} sessions | media root: {MEDIA_ROOT}")
    print(f"serving on http://{a.host}:{a.port}")
    app.run(host=a.host, port=a.port, threaded=True, debug=False)
