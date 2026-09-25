"""
pwa.py -- build the mobile-first PWA I add to my iPhone home screen.

Two builds from one template:

  app/     the PUBLIC shell -- markup, styles and script ONLY. Zero pool data.
           This is what GitHub Pages serves. On my phone it pulls the readings
           straight from the PRIVATE data repo with a token I paste in once
           (stored only in that browser), so nothing is ever published.

  out/     the LOCAL copy with today's data inlined -- open it straight off disk
           when I'm at the PC, no server and no token needed.

Four tabs: Today (checklist, panel, upload, quick log), History (chart +
timeline), Opening (the sequential spring wizard), Settings.

Writes never touch the JSONL files from the browser. The app CREATES one small
file per action (inbox/*.json, inbox/*-reading.jpg) which the workflow files
away -- so the phone and the cron job can never clobber each other.

THE MIRROR: the script re-implements part of the engine in JavaScript so the app
moves the instant I tap something instead of a minute later. What is mirrored,
exactly, and nothing more:

    * panel status  (a value vs. its target band)      -- engine/checklist.py
    * the days-since chips                             -- engine/state.py
    * supersede marking (I just did it -> item goes stale) -- engine/checklist.py
    * the four dose formulas                           -- engine/chem.py

What is NOT mirrored is the rule set itself -- which items exist, the ORP
gating, the acid/shock gap. Those only ever come from the cloud. Duplicating
arithmetic is cheap and safe; duplicating safety rules means two places to get
them wrong, so the phone shows the cloud's list and only recomputes the numbers
inside it.

Usage:  py engine/pwa.py
"""
from __future__ import annotations
import json
import math
import os
import struct
import sys
import zlib
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chem      # noqa: E402
import poolcfg   # noqa: E402
import store     # noqa: E402

ACCENT = (64, 196, 208)       # pool aqua
BG = (13, 20, 26)             # near-black deep water


# --------------------------------------------------------------------- icons

def _png(size, bg, fg):
    """A tiny dependency-free PNG: a water drop over a ring on a dark square."""
    cx = cy = (size - 1) / 2.0
    r_out, r_in = size * 0.40, size * 0.28
    rows = bytearray()
    for y in range(size):
        rows.append(0)                                   # filter type 0
        for x in range(size):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            # a ring, plus a wave line through the middle
            wave = abs((y - cy) - size * 0.09 * math.sin(
                (x / size) * 6.28318)) < size * 0.045
            if (r_in <= d <= r_out) or (wave and d < r_in):
                rows += bytes(fg)
            else:
                rows += bytes(bg)
    raw = zlib.compress(bytes(rows), 9)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw)
            + chunk(b"IEND", b""))


# ------------------------------------------------------------------ template

TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="__TITLE__">
<meta name="theme-color" content="#0d141a">
<meta name="robots" content="noindex,nofollow">
<title>__TITLE__</title>
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icon-192.png">
<style>
:root{
  --bg:#0d141a; --card:#161f27; --card2:#1d2832; --line:#2a3945;
  --ink:#eef5f8; --dim:#9fb2bf; --faint:#66798a;
  --aqua:#40c4d0; --aqua-soft:#8fdfe6; --green:#5ec98a; --amber:#e8b45e;
  --red:#e8706e; --blue:#7aa7f2;
  --radius:18px;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--ink); font:16px/1.55 -apple-system,BlinkMacSystemFont,
  "SF Pro Text","Segoe UI",Roboto,sans-serif; -webkit-font-smoothing:antialiased;
  padding:max(12px,env(safe-area-inset-top)) 14px calc(96px + env(safe-area-inset-bottom));
  max-width:560px; margin:0 auto;
}
h1{font-size:19px;margin:0;letter-spacing:.2px}
h2{font-size:15px;margin:0 0 10px;letter-spacing:.4px;text-transform:uppercase;color:var(--aqua-soft)}
p{margin:0 0 10px}
p:last-child{margin-bottom:0}
button,input,select,textarea{font:inherit;color:inherit}
.top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px}
.sub{color:var(--dim);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px;margin-bottom:12px}
.hero{background:linear-gradient(160deg,#18242e,#131c24)}
.headline{font-size:20px;font-weight:650;line-height:1.3;letter-spacing:-.2px}
.summary{font-size:15px;line-height:1.5;margin-top:10px;color:#dce7ed}
.chip{display:inline-block;font-size:11px;letter-spacing:.6px;text-transform:uppercase;
  border:1px solid var(--line);border-radius:999px;padding:3px 9px;color:var(--dim);white-space:nowrap}
.chip.ok{color:var(--green);border-color:#2f5c43}
.chip.warn{color:var(--amber);border-color:#5c4a2c}
.chip.bad{color:var(--red);border-color:#6e3436}
.chip.none{color:var(--faint)}
.iconbtn{background:var(--card2);border:1px solid var(--line);border-radius:12px;width:38px;
  height:38px;font-size:17px;cursor:pointer;flex:none}
.iconbtn:active{opacity:.6}
.iconbtn.spin{animation:sp 1s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row.tight{gap:6px}

/* ---- water panel ---- */
.panel{display:grid;grid-template-columns:1fr 1fr;gap:9px}
.pv{background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:11px 12px;
  position:relative;overflow:hidden}
.pv .k{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--faint)}
.pv .v{font-size:23px;font-weight:650;margin-top:3px;line-height:1.1}
.pv .t{font-size:11.5px;color:var(--faint);margin-top:3px}
.pv .bar{height:4px;border-radius:3px;background:#2b3946;margin-top:9px;position:relative}
.pv .bar i{position:absolute;top:-2px;width:8px;height:8px;border-radius:50%;background:var(--aqua);
  margin-left:-4px;transition:left .25s}
.pv .bar u{position:absolute;top:0;bottom:0;background:#2f5c43;border-radius:3px;opacity:.55}
.pv.low .v{color:var(--amber)} .pv.high .v{color:var(--amber)}
.pv.low .bar i,.pv.high .bar i{background:var(--amber)}
.pv.unknown .v{color:var(--faint);font-size:16px;padding-top:6px}
.pv.unknown .bar{display:none}

/* ---- days-since chips ---- */
.chips{display:flex;gap:7px;overflow-x:auto;padding:2px 0 4px;-webkit-overflow-scrolling:touch}
.dchip{flex:none;background:var(--card2);border:1px solid var(--line);border-radius:13px;
  padding:8px 11px;min-width:76px;text-align:center}
.dchip .n{font-size:19px;font-weight:650;line-height:1.1}
.dchip .l{font-size:10.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--faint);margin-top:2px}
.dchip .u{font-size:10px;color:var(--faint)}
.dchip.due{border-color:#5c4a2c}
.dchip.due .n{color:var(--amber)}
.dchip.never .n{color:var(--faint);font-size:15px;padding-top:3px}

/* ---- checklist ---- */
.item{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:14px 15px;margin-bottom:10px;border-left:3px solid var(--line)}
.item.p0{border-left-color:var(--red)}
.item.p1{border-left-color:var(--aqua)}
.item.p2{border-left-color:var(--amber)}
.item.p3{border-left-color:var(--line)}
.item.stale{opacity:.82;border-left-style:dashed}
.item .ih{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.item .it{font-size:16.5px;font-weight:600;line-height:1.3}
.dose{display:inline-block;margin-top:9px;background:#14303a;border:1px solid #275a66;
  border-radius:12px;padding:9px 12px;font-size:15px;font-weight:600;color:var(--aqua-soft);
  line-height:1.35}
.gap{display:block;margin-top:8px;font-size:12.5px;color:var(--amber);font-weight:600}
.why{font-size:14.5px;line-height:1.5;color:#d7e3ea;margin-top:10px}
.how{font-size:14px;line-height:1.5;color:var(--dim);margin-top:9px;padding-top:9px;
  border-top:1px solid var(--line)}
.how b{color:var(--aqua-soft);font-weight:600;letter-spacing:.5px;font-size:11px;
  text-transform:uppercase;display:block;margin-bottom:3px}
.itembtns{display:flex;gap:8px;margin-top:11px}

/* ---- confirm card ---- */
.confirm{border:1px solid #5c4a2c;background:#241f18}
.fields{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:4px}
.field label{display:block;font-size:10.5px;letter-spacing:.9px;text-transform:uppercase;
  color:var(--faint);margin-bottom:4px}
.field input{width:100%}
label{display:block;font-size:12.5px;color:var(--dim);margin:12px 0 5px}
input,textarea,select{
  width:100%;background:var(--card2);border:1px solid var(--line);border-radius:12px;
  padding:11px 12px;outline:none}
input:focus,textarea:focus,select:focus{border-color:var(--aqua)}
textarea{min-height:64px;resize:vertical}
input[type=file]{padding:9px;font-size:13px}
.sym{background:var(--card2);border:1px solid var(--line);border-radius:999px;padding:8px 13px;
  font-size:13.5px;cursor:pointer}
.sym.on{background:#14303a;border-color:var(--aqua);color:var(--aqua-soft)}
.btn{width:100%;background:var(--aqua);color:#08181c;border:none;border-radius:14px;
  padding:14px;font-weight:650;font-size:15px;cursor:pointer;margin-top:14px}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--ink);font-weight:500}
.btn.sm{padding:10px;font-size:14px;margin-top:8px}
.btn.tiny{padding:8px 12px;font-size:13px;margin-top:0;width:auto}
.btn:active{opacity:.85}
.btn:disabled{opacity:.4;cursor:default}
.half{display:flex;gap:8px}
.half .btn{flex:1}

/* ---- chart ---- */
.rangebtns{display:flex;gap:7px;margin:0 0 12px}
.rangebtns button{flex:1;background:var(--card2);border:1px solid var(--line);border-radius:11px;
  padding:9px 0;font-size:13px;cursor:pointer;color:var(--dim)}
.rangebtns button.on{background:var(--aqua);border-color:var(--aqua);color:#08181c;font-weight:650}
#chart svg{display:block;width:100%;height:auto}
.chartnote{font-size:11.5px;color:var(--faint);margin-top:8px;line-height:1.5}

/* ---- timeline ---- */
.tl{display:flex;flex-direction:column}
.tlrow{display:grid;grid-template-columns:54px 1fr;gap:11px;padding:11px 0;
  border-bottom:1px solid var(--line)}
.tlrow:last-child{border-bottom:none}
.tlrow .when{font-size:11.5px;color:var(--faint);line-height:1.35;padding-top:2px}
.tlrow .what{font-size:14.5px;line-height:1.4}
.tlrow .vals{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.vpill{font-size:11.5px;border:1px solid var(--line);border-radius:8px;padding:2px 7px;color:var(--dim)}
.vpill.low,.vpill.high{color:var(--amber);border-color:#5c4a2c}
.tlrow .nt{font-size:12.5px;color:var(--faint);margin-top:5px;font-style:italic}
.tlrow.unconfirmed .what{color:var(--amber)}

/* ---- opening wizard ---- */
.step{display:grid;grid-template-columns:34px 1fr;gap:12px;padding:14px 0;
  border-bottom:1px solid var(--line)}
.step:last-child{border-bottom:none}
.step .n{width:30px;height:30px;border-radius:50%;border:1px solid var(--line);display:flex;
  align-items:center;justify-content:center;font-size:14px;font-weight:650;color:var(--faint);flex:none}
.step.done .n{background:var(--green);border-color:var(--green);color:#08181c}
.step.current .n{border-color:var(--aqua);color:var(--aqua)}
.step .st{font-size:16px;font-weight:600}
.step.locked .st,.step.locked .sd{color:var(--faint)}
.step .sd{font-size:14px;color:var(--dim);line-height:1.5;margin-top:5px}
.step.locked .sd{display:none}
.step .smsg{font-size:12px;color:var(--green);margin-top:7px}
.progress{height:6px;border-radius:4px;background:#2b3946;overflow:hidden;margin:4px 0 14px}
.progress span{display:block;height:100%;background:linear-gradient(90deg,#2d7f8a,var(--aqua));
  border-radius:4px;transition:width .3s}

.note{font-size:12px;color:var(--faint);margin-top:10px;line-height:1.5}
.rules{margin:0;padding-left:18px;font-size:13px;color:var(--dim);line-height:1.6}
.rules li{margin:6px 0}
.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(86px + env(safe-area-inset-bottom));
  background:#1f2b35;border:1px solid var(--line);border-radius:12px;padding:11px 16px;font-size:14px;
  opacity:0;pointer-events:none;transition:opacity .25s;max-width:90vw;text-align:center;z-index:9}
.toast.show{opacity:1}
details summary{cursor:pointer;color:var(--dim);font-size:13px;list-style:none}
details summary::-webkit-details-marker{display:none}
details[open] summary{margin-bottom:10px}
.foot{color:var(--faint);font-size:11.5px;line-height:1.6;text-align:center;margin:18px 4px 0}
.hide{display:none}
nav{position:fixed;left:0;right:0;bottom:0;z-index:8;background:rgba(13,20,26,.94);
  backdrop-filter:blur(12px);border-top:1px solid var(--line);
  padding:7px 0 calc(7px + env(safe-area-inset-bottom));display:flex}
nav button{flex:1;background:none;border:none;color:var(--faint);font-size:10.5px;
  letter-spacing:.4px;cursor:pointer;padding:3px 0;display:flex;flex-direction:column;
  align-items:center;gap:3px}
nav button .ic{font-size:19px;line-height:1}
nav button.on{color:var(--aqua-soft)}
</style>
</head>
<body>

<div class="top">
  <div>
    <h1>__TITLE__</h1>
    <div class="sub" id="today">--</div>
  </div>
  <div class="row tight" style="align-items:center">
    <span class="chip" id="sync">loading</span>
    <button class="iconbtn" id="hrefresh" title="Refresh">&#8635;</button>
  </div>
</div>

<!-- ------------------------------------------------------------- TODAY -->
<section id="tab-today">

  <div class="card hero">
    <div class="row tight" style="justify-content:space-between;align-items:flex-start">
      <div class="headline" id="headline">--</div>
    </div>
    <div class="summary" id="summary"></div>
    <div class="row tight" style="margin-top:12px" id="heroChips"></div>
  </div>

  <div class="card confirm hide" id="confirmCard">
    <h2 style="color:var(--amber)">Check this reading</h2>
    <div class="sub" id="confirmLead">--</div>
    <div class="fields" id="confirmFields"></div>
    <div class="note" id="confirmNote"></div>
    <div class="half">
      <button class="btn" id="cf_ok">Looks right</button>
      <button class="btn ghost" id="cf_skip">Not now</button>
    </div>
  </div>

  <div class="card">
    <h2>Water</h2>
    <div class="panel" id="panel"></div>
    <div class="note" id="panelAge"></div>
  </div>

  <div class="chips" id="chips"></div>

  <h2 style="margin:16px 0 10px">Today's list</h2>
  <div id="items"></div>

  <div class="card">
    <h2>New reading</h2>
    <div class="sub">Upload the ICO screenshot and it gets read in the cloud, or type the numbers
      in yourself.</div>
    <label for="shot">ICO screenshot</label>
    <input type="file" id="shot" accept="image/*" capture="environment">
    <button class="btn" id="shot_send" disabled>Upload screenshot</button>
    <details style="margin-top:14px">
      <summary>Type a reading in by hand</summary>
      <div class="fields" id="manualFields"></div>
      <button class="btn ghost sm" id="man_save">Save reading</button>
    </details>
  </div>

  <div class="card">
    <h2>Log something else</h2>
    <div class="row tight" id="actionChips"></div>
    <div class="half" style="margin-top:12px">
      <div style="flex:1"><label for="a_amount">Amount</label>
        <input type="number" id="a_amount" inputmode="decimal" step="any" placeholder="optional"></div>
      <div style="flex:1"><label for="a_unit">Unit</label>
        <input type="text" id="a_unit" placeholder="lb / gal / fl oz"></div>
    </div>
    <label for="a_date">When</label>
    <input type="date" id="a_date">
    <label for="a_note">Note</label>
    <textarea id="a_note" placeholder="anything worth remembering"></textarea>
    <button class="btn" id="a_save">Log it</button>
  </div>

  <div class="card">
    <h2>Safety rules</h2>
    <ol class="rules" id="safety"></ol>
  </div>
</section>

<!-- ----------------------------------------------------------- HISTORY -->
<section id="tab-hist" class="hide">
  <div class="card">
    <h2>Trend</h2>
    <div class="rangebtns" id="metricbtns"></div>
    <div id="chart"></div>
    <div class="chartnote" id="chartnote"></div>
  </div>
  <div class="card">
    <h2>Everything that's happened</h2>
    <div class="tl" id="timeline"></div>
  </div>
</section>

<!-- ----------------------------------------------------------- OPENING -->
<section id="tab-open" class="hide">
  <div class="card">
    <h2>Spring opening</h2>
    <div class="sub" id="openLead">--</div>
    <div class="progress"><span id="openBar" style="width:0%"></span></div>
    <div id="steps"></div>
  </div>
  <div class="card">
    <div class="note">Each step unlocks the next one. That order isn't fussiness: every step
      assumes the one before it actually happened, and salting a pool that's still green is an
      expensive way to salt a swamp.</div>
    <button class="btn ghost sm" id="open_reset">Start a new season</button>
  </div>
</section>

<!-- ---------------------------------------------------------- SETTINGS -->
<section id="tab-set" class="hide">
  <div class="card">
    <h2>Settings &amp; connection</h2>
    <label for="s_repo">Private data repo (owner/name)</label>
    <input type="text" id="s_repo" placeholder="yourname/pool-pilot-data" autocapitalize="off" autocorrect="off">
    <label for="s_token">GitHub token (fine-grained, Contents: read &amp; write on that repo)</label>
    <input type="password" id="s_token" placeholder="github_pat_..." autocapitalize="off" autocorrect="off">
    <div class="note">Stored only in this browser's local storage on this device. It never leaves
      your phone except in requests to github.com. Nothing about your pool is ever stored in the
      public app repo.</div>
    <button class="btn" id="s_save">Save &amp; sync</button>
    <button class="btn ghost" id="s_refresh">Refresh from cloud</button>
    <button class="btn ghost" id="s_rebuild">Force a cloud rebuild</button>
    <div class="note" id="s_status"></div>
  </div>
  <div class="card">
    <h2>This pool</h2>
    <div class="note" id="s_pool"></div>
  </div>
  <div class="card">
    <h2>Worth knowing by heart</h2>
    <div class="note" id="s_ref"></div>
  </div>
  <div class="card">
    <h2>This device</h2>
    <div class="note" id="s_queue"></div>
    <div class="note" id="s_built"></div>
  </div>
</section>

<div class="foot" id="foot"></div>
<div class="toast" id="toast"></div>

<nav>
  <button data-tab="today" class="on"><span class="ic">&#128167;</span>Today</button>
  <button data-tab="hist"><span class="ic">&#128200;</span>History</button>
  <button data-tab="open"><span class="ic">&#127774;</span>Opening</button>
  <button data-tab="set"><span class="ic">&#9881;</span>Settings</button>
</nav>

<script>
const EMBED = __DATA__;
const DEFAULT_REPO = "__REPO__";
const BUILT = "__BUILT__";
const CHEM = __CHEM__;                /* kept in step with engine/chem.py at build time */
const LOCAL_API = "http://127.0.0.1:8778";

const $ = function(id){ return document.getElementById(id); };
const ls = {
  get: function(k, d){ try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); }
                       catch(e){ return d; } },
  set: function(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch(e){} }
};

let STATE = null, LIVE = null;
let chartMetric = null, shotFile = null, pickedAction = null, confirmRow = null;

function toast(msg, ms){
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(function(){ t.classList.remove("show"); }, ms || 2800);
}
function esc(s){ return String(s == null ? "" : s).replace(/[&<>"]/g, function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]; }); }
function today(){ return new Date().toLocaleDateString("en-CA"); }
function dnum(iso){ return Math.floor(new Date(String(iso).slice(0,10) + "T12:00:00").getTime() / 86400000); }
function daysBetween(a, b){ return dnum(b) - dnum(a); }
function fmtDay(iso){
  if(!iso) return "--";
  return new Date(String(iso).slice(0,10) + "T12:00:00")
    .toLocaleDateString(undefined, {month:"short", day:"numeric"});
}
/* LOCAL time, not UTC. The filename is not just a name: vision.py derives an
   uploaded screenshot's timestamp from it, so a UTC stamp would file an evening
   reading under tomorrow's date and quietly skew both its age and the
   "did I already dose this?" comparison. */
function nowIso(){ const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0,19); }
function nowStamp(){ return nowIso().replace(/[:.]/g, "-"); }

/* ============================================================ THE MIRROR
   A JavaScript twin of the ARITHMETIC in engine/chem.py and the two cheap
   derivations in checklist.py/state.py, so tapping "I backwashed it" or fixing
   a misread pH moves the screen now instead of in a minute. The rules -- which
   items exist, the ORP gating, the acid/shock gap -- are never duplicated here;
   those come from the cloud, because two copies of a safety rule is one too many.
   The cloud recomputes all of this and its answer overwrites mine.
   ==================================================================== */
const SUPERSEDED_BY = {ph_down:"added_acid", salt_up:"added_salt", cya_up:"added_cya",
                       borate_up:"added_borate", shock:"added_shock", orp_cell:"cell_output"};
const SUPERSEDED_NOUN = {ph_down:"pH", salt_up:"salt", cya_up:"stabilizer",
                         borate_up:"borates", shock:"chlorine", orp_cell:"the cell"};

function scale(gal){ return (gal > 0 ? gal/10000 : 1.8); }
function saltLb(cur, tgt, gal){ return (tgt - cur) * scale(gal) / CHEM.ppm_per_lb_per_10k; }
function cyaLb(cur, tgt, gal){ return (tgt - cur) * scale(gal) / CHEM.ppm_per_lb_per_10k; }
function acidFloz(now, tgt, gal, ta){
  return CHEM.acid_floz_per_ta_per_ph_per_10k * (ta || CHEM.default_ta) * (now - tgt) * scale(gal);
}
function chlorineGal(d, gal){ return d * scale(gal) / CHEM.fc_ppm_per_gal_per_10k; }

function statusOf(v, lo, hi){
  if(v === null || v === undefined) return "unknown";
  if(lo !== null && lo !== undefined && v < lo) return "low";
  if(hi !== null && hi !== undefined && v > hi) return "high";
  return "ok";
}

/* local, not-yet-synced writes so the screen reflects the tap immediately */
function pending(){ return ls.get("pp_pending", {readings:[], actions:[], opening:{}}); }
function addPending(kind, rec){
  const p = pending(); (p[kind] = p[kind] || []).push(rec); ls.set("pp_pending", p);
}
function clearConfirmedPending(){
  /* drop anything the cloud has now echoed back, matched on id */
  const p = pending(), raw = (STATE && STATE.raw) || {readings:[], actions:[]};
  const have = {};
  (raw.readings || []).forEach(function(r){ have[r.id] = 1; });
  (raw.actions  || []).forEach(function(a){ have[a.id] = 1; });
  p.readings = (p.readings || []).filter(function(r){ return !have[r.id]; });
  p.actions  = (p.actions  || []).filter(function(a){ return !have[a.id]; });
  if(STATE && STATE.opening && STATE.opening.steps){
    STATE.opening.steps.forEach(function(s){
      if(s.state === "done" && p.opening) delete p.opening[s.id];
    });
  }
  ls.set("pp_pending", p);
}

function mergedActions(){
  const raw = ((STATE && STATE.raw) || {}).actions || [];
  const byId = {}; raw.forEach(function(a){ byId[a.id] = a; });
  (pending().actions || []).forEach(function(a){ byId[a.id] = a; });
  return Object.keys(byId).map(function(k){ return byId[k]; })
    .sort(function(a,b){ return (a.at||"") < (b.at||"") ? -1 : 1; });
}
function mergedReadings(){
  const raw = ((STATE && STATE.raw) || {}).readings || [];
  const byId = {}; raw.forEach(function(r){ byId[r.id] = r; });
  (pending().readings || []).forEach(function(r){
    byId[r.id] = Object.assign({}, byId[r.id] || {}, r);
  });
  return Object.keys(byId).map(function(k){ return byId[k]; })
    .sort(function(a,b){ return (a.at||"") < (b.at||"") ? -1 : 1; });
}
function lastAction(key, acts){
  const hits = (acts || mergedActions()).filter(function(a){ return a.action === key; });
  return hits.length ? hits[hits.length-1] : null;
}

/* recompute what's cheap to recompute; leave the rules to the cloud */
function recompute(){
  if(!STATE) return null;
  const acts = mergedActions(), reads = mergedReadings();
  const confirmed = reads.filter(function(r){ return r.confirmed; });
  const latest = confirmed.length ? confirmed[confirmed.length-1] : null;
  const gal = (STATE.pool && STATE.pool.gallons) || 18000;

  const panel = (STATE.panel || []).map(function(p){
    const v = latest ? latest[p.key] : null;
    const st = p.key === "water_temp_f" ? (v === null || v === undefined ? "unknown" : "ok")
                                        : statusOf(v, p.low, p.high);
    const disp = (v === null || v === undefined) ? "not measured"
      : (p.key === "salt_ppm" ? Math.round(v).toLocaleString() + " ppm"
                              : (Math.round(v*100)/100) + (p.unit ? " " + p.unit : ""));
    return Object.assign({}, p, {value:v, display:disp, status:st});
  });

  const chips = (STATE.chips || []).map(function(c){
    const a = lastAction(c.key, acts);
    const n = a && a.date ? daysBetween(a.date, today()) : null;
    return Object.assign({}, c, {days:n, date:a ? a.date : null,
      amount:a ? a.amount : null, unit:a ? a.unit : "",
      never:n === null, due: !!(c.every && n !== null && n >= c.every)});
  });

  /* an item I've just acted on goes stale immediately -- same rule as the engine */
  const cl = (STATE.checklist || {});
  const items = (cl.items || []).map(function(it){
    const copy = Object.assign({}, it);
    const akey = SUPERSEDED_BY[it.key];
    const rAt = (cl.reading || {}).at;
    if(akey && rAt && !copy.superseded){
      const later = acts.filter(function(a){ return a.action === akey && (a.at||"") > rAt; });
      if(later.length){
        copy.superseded = true; copy.dose = null; copy.kind = "test";
        copy.title = "Retest before touching " + (SUPERSEDED_NOUN[it.key] || "that") + " again";
        copy.why = "You just logged “" + (later[later.length-1].label || akey) +
                   "”, which is after the reading this dose came from. The water has moved " +
                   "— take a fresh reading rather than dosing the same measurement twice.";
        copy.how = "";
      }
    }
    return copy;
  });

  const done = ls.get("pp_done", {});
  items.forEach(function(it){ it.doneToday = done[today() + "|" + it.id] === true; });

  const age = latest ? daysBetween(latest.date, today()) : null;
  return {panel:panel, chips:chips, items:items, latest:latest, age:age, gallons:gal,
          readings:reads, actions:acts};
}

/* ---------------------------------------------------------------- github */
function cfgApp(){ return {repo: ls.get("pp_repo", DEFAULT_REPO) || "", token: ls.get("pp_token", "") || ""}; }
function connected(){ const c = cfgApp(); return !!(c.repo && c.repo.indexOf("/") > 0 && c.token); }

async function ghGet(path){
  const c = cfgApp();
  const r = await fetch("https://api.github.com/repos/" + c.repo + "/contents/" + path +
                        "?ref=HEAD&t=" + Date.now(),
    {headers: {Authorization: "Bearer " + c.token, Accept: "application/vnd.github.raw"}});
  if(r.status === 404) return null;
  if(!r.ok) throw new Error("GitHub " + r.status + " on " + path);
  return await r.text();
}
async function ghPutB64(path, b64, message){
  const c = cfgApp();
  const r = await fetch("https://api.github.com/repos/" + c.repo + "/contents/" + path, {
    method:"PUT",
    headers:{Authorization:"Bearer " + c.token, Accept:"application/vnd.github+json",
             "Content-Type":"application/json"},
    body: JSON.stringify({message: message, content: b64})
  });
  if(!r.ok) throw new Error("GitHub " + r.status + ": " + (await r.text()).slice(0,140));
  return await r.json();
}
function b64text(text){ return btoa(unescape(encodeURIComponent(text))); }

function queue(item){ const q = ls.get("pp_queue", []); q.push(item); ls.set("pp_queue", q); }
async function flush(){
  let q = ls.get("pp_queue", []);
  if(!q.length || !connected()) return q.length;
  const left = [];
  for(const item of q){
    try { await ghPutB64(item.path, item.b64, item.message); }
    catch(e){ left.push(item); }
  }
  ls.set("pp_queue", left);
  if(q.length && !left.length) toast("Synced " + q.length + " pending item(s)");
  return left.length;
}

/* one write path for everything the phone creates. Never edits a file --
   always creates a new one, so the phone and the cron job can't collide. */
async function send(path, b64, message, okMsg){
  if(EMBED){                                    /* local build: talk to engine/serve.py if it's up */
    try {
      const r = await fetch(LOCAL_API + "/api/write", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({path:path, b64:b64})
      });
      if(r.ok){ toast(okMsg + " (saved locally)"); return true; }
    } catch(e){}
  }
  if(!connected()){
    queue({path:path, b64:b64, message:message});
    toast("Saved on this device — add a token in Settings to sync");
    return false;
  }
  try { await ghPutB64(path, b64, message); toast(okMsg); return true; }
  catch(e){ queue({path:path, b64:b64, message:message});
            toast("Offline — queued, will sync later"); return false; }
}
function sendJson(name, obj, okMsg){
  const stamp = nowStamp();
  return send("inbox/" + stamp + "-" + name + ".json", b64text(JSON.stringify(obj, null, 2)),
              "chore: " + name + " from phone", okMsg);
}

/* ----------------------------------------------------------------- load */
async function load(quiet){
  if(EMBED){
    STATE = EMBED.state;
    $("sync").textContent = "local"; $("sync").className = "chip ok";
    render(); return;
  }
  const cached = ls.get("pp_cache", null);
  if(cached && !STATE){ STATE = cached; render(); }
  if(!connected()){
    $("sync").textContent = "not connected"; $("sync").className = "chip warn";
    if(!cached) showTab("set");
    render(); return;
  }
  if(!quiet){ $("sync").textContent = "syncing"; $("sync").className = "chip"; }
  $("hrefresh").classList.add("spin");
  try {
    const s = await ghGet("data/store/state.json");
    STATE = s ? JSON.parse(s) : null;
    ls.set("pp_cache", STATE);
    clearConfirmedPending();
    $("sync").textContent = "synced"; $("sync").className = "chip ok";
    render();
  } catch(e){
    $("sync").textContent = "sync failed"; $("sync").className = "chip warn";
    $("s_status").textContent = String(e.message || e);
  }
  $("hrefresh").classList.remove("spin");
  await flush();
}

/* --------------------------------------------------------------- render */
function render(){
  $("today").textContent = new Date().toLocaleDateString(undefined,
    {weekday:"long", month:"long", day:"numeric"});
  if(!STATE){
    /* first run on a fresh phone: nothing has synced yet, so say so everywhere
       rather than leaving labelled cards sitting empty and looking broken. */
    $("headline").textContent = "Not connected yet.";
    $("summary").textContent = "Add your private repo and a GitHub token in Settings, and this " +
      "fills in from the cloud.";
    $("s_pool").textContent = "Syncs from your private repo once you're connected.";
    $("s_ref").textContent = "These are worked out from your pool's volume, which arrives with " +
      "the first sync.";
    $("s_built").textContent = "App built " + BUILT + ". Nothing synced yet.";
    const q0 = ls.get("pp_queue", []);
    $("s_queue").textContent = q0.length
      ? (q0.length + " item(s) waiting to sync.") : "Nothing waiting to sync.";
    return;
  }
  LIVE = recompute();
  renderHero(); renderPanel(); renderChips(); renderItems(); renderConfirm();
  renderActionChips(); renderManual(); renderSafety();
  renderChart(); renderTimeline(); renderOpening(); renderSettings();
  $("foot").textContent = (STATE.disclaimer || "") + " Built " + BUILT + ".";
}

function renderHero(){
  const cl = STATE.checklist || {};
  $("headline").textContent = cl.headline || "—";
  $("summary").textContent = cl.summary || "";
  const out = [];
  const age = LIVE.age;
  if(age === null) out.push('<span class="chip none">no reading yet</span>');
  else {
    const every = (STATE.cadence_days || {}).reading || 7;
    const cls = age >= every*2 ? "bad" : (age >= every ? "warn" : "ok");
    out.push('<span class="chip ' + cls + '">reading ' +
             (age === 0 ? "today" : age + "d old") + '</span>');
  }
  const todo = LIVE.items.filter(function(i){ return i.priority <= 2 && !i.superseded && !i.doneToday; });
  out.push('<span class="chip ' + (todo.length ? "warn" : "ok") + '">' +
           (todo.length ? todo.length + " to do" : "nothing due") + '</span>');
  if(cl.source === "fallback") out.push('<span class="chip none">offline wording</span>');
  $("heroChips").innerHTML = out.join("");
}

function renderPanel(){
  $("panel").innerHTML = LIVE.panel.map(function(p){
    let bar = "";
    if(p.value !== null && p.value !== undefined && (p.low !== null || p.high !== null)){
      const lo = p.low !== null && p.low !== undefined ? p.low : p.value * 0.8;
      const hi = p.high !== null && p.high !== undefined ? p.high : p.value * 1.2;
      const span = Math.max(1e-9, (hi - lo));
      const a = lo - span * 0.6, b = hi + span * 0.6;
      const pct = function(v){ return Math.max(0, Math.min(100, (v - a) / (b - a) * 100)); };
      bar = '<div class="bar"><u style="left:' + pct(lo).toFixed(1) + '%;right:' +
            (100 - pct(hi)).toFixed(1) + '%"></u><i style="left:' +
            pct(p.value).toFixed(1) + '%"></i></div>';
    }
    return '<div class="pv ' + p.status + '"><div class="k">' + esc(p.label) + '</div>' +
           '<div class="v">' + esc(p.display) + '</div>' +
           '<div class="t">' + esc(p.target) + '</div>' + bar + '</div>';
  }).join("");
  const r = LIVE.latest;
  $("panelAge").textContent = r
    ? ("From the " + fmtDay(r.date) + " reading" + (r.source ? " (" + r.source + ")" : "") +
       (LIVE.age ? ", " + LIVE.age + " day(s) ago." : ", today."))
    : "No confirmed reading yet — upload a screenshot or type one in below.";
}

function renderChips(){
  $("chips").innerHTML = LIVE.chips.map(function(c){
    const cls = c.never ? "never" : (c.due ? "due" : "");
    const n = c.never ? "never" : (c.days === 0 ? "today" : c.days + "d");
    const extra = c.amount ? (c.amount + " " + (c.unit || "")) : (c.every ? "every " + c.every + "d" : "");
    return '<div class="dchip ' + cls + '"><div class="n">' + esc(n) + '</div>' +
           '<div class="l">' + esc(c.label) + '</div>' +
           '<div class="u">' + esc(extra) + '</div></div>';
  }).join("");
}

function renderItems(){
  const items = LIVE.items;
  if(!items.length){
    $("items").innerHTML = '<div class="card"><div class="why">Nothing on the list. ' +
      'Either the water is where it should be, or there is no reading to judge it by.</div></div>';
    return;
  }
  $("items").innerHTML = items.map(function(it){
    const cls = "item p" + it.priority + (it.superseded || it.doneToday ? " stale" : "");
    let h = '<div class="' + cls + '" data-item="' + esc(it.id) + '">';
    h += '<div class="ih"><div class="it">' + esc(it.title) + '</div>';
    h += '<span class="chip ' + (it.doneToday ? "ok" : (it.priority === 0 ? "bad" :
         (it.priority === 1 ? "" : "none"))) + '">' +
         (it.doneToday ? "done" : ["first","today","this week","watching"][it.priority]) +
         '</span></div>';
    if(it.dose && it.dose.label) h += '<div class="dose">' + esc(it.dose.label) + '</div>';
    if(it.wait_hours) h += '<span class="gap">⏱ Wait at least ' + it.wait_hours +
                           ' hours after the acid — never together.</span>';
    if(it.why) h += '<div class="why">' + esc(it.why) + '</div>';
    if(it.how) h += '<div class="how"><b>How</b>' + esc(it.how) + '</div>';
    if(!it.superseded && !it.doneToday && LOGGABLE[it.key])
      h += '<div class="itembtns"><button class="btn tiny" data-did="' + esc(it.id) +
           '">I did this</button></div>';
    h += '</div>';
    return h;
  }).join("");
  Array.prototype.forEach.call(document.querySelectorAll("[data-did]"), function(b){
    b.addEventListener("click", function(){ didItem(b.dataset.did); });
  });
}

/* which checklist items map onto a loggable action, and what amount to log */
const LOGGABLE = {
  ph_down:"added_acid", salt_up:"added_salt", cya_up:"added_cya", borate_up:"added_borate",
  shock:"added_shock", orp_cell:"cell_output", backwashed_filter:"backwashed_filter",
  emptied_skimmer:"emptied_skimmer"
};

async function didItem(id){
  const it = LIVE.items.filter(function(i){ return i.id === id; })[0];
  if(!it) return;
  const akey = LOGGABLE[it.key];
  const rec = {kind:"action", id:nowIso(), action:akey, at:nowIso(),
               amount:(it.dose && it.dose.amount) || null,
               unit:(it.dose && it.dose.unit) || "", note:"from the checklist"};
  if(it.key === "orp_cell" && it.dose && it.dose.detail && it.dose.detail.to_pct != null){
    rec.amount = it.dose.detail.to_pct;              /* log the resulting %, not the step */
    rec.unit = "%";
  }
  addPending("actions", {id:rec.id, at:rec.at, date:rec.at.slice(0,10), action:akey,
                         label:(STATE.action_kinds.filter(function(k){ return k.key===akey; })[0]||{}).label || akey,
                         amount:rec.amount, unit:rec.unit, note:rec.note});
  const done = ls.get("pp_done", {}); done[today() + "|" + id] = true; ls.set("pp_done", done);
  render();
  await sendJson("action", rec, "Logged");
}

function renderConfirm(){
  const pend = (STATE.pending_confirmation || []);
  const row = pend.length ? pend[pend.length-1] : null;
  confirmRow = row;
  $("confirmCard").classList.toggle("hide", !row);
  if(!row) return;
  const got = MEASURES.filter(function(m){ return row[m.key] !== null && row[m.key] !== undefined; });
  $("confirmLead").textContent = got.length
    ? ("From the " + fmtDay(row.date) + " screenshot we read " +
       got.map(function(m){ return m.label + " " + row[m.key]; }).join(", ") + ". Look right?")
    : ("We couldn't read the " + fmtDay(row.date) + " screenshot. Type the numbers in and it " +
       "still counts.");
  $("confirmFields").innerHTML = MEASURES.map(function(m){
    const v = row[m.key];
    return '<div class="field"><label for="cf_' + m.key + '">' + esc(m.label) + '</label>' +
           '<input type="number" step="any" inputmode="decimal" id="cf_' + m.key + '" value="' +
           (v === null || v === undefined ? "" : v) + '"></div>';
  }).join("");
  $("confirmNote").textContent = row.note || "Nothing is dosed off this reading until you confirm it.";
}

const MEASURES = [
  {key:"ph", label:"pH"}, {key:"orp_mv", label:"ORP mV"}, {key:"salt_ppm", label:"Salt ppm"},
  {key:"water_temp_f", label:"Temp °F"}, {key:"cya_ppm", label:"CYA ppm"},
  {key:"ta_ppm", label:"TA ppm"}, {key:"fc_ppm", label:"Free Cl ppm"},
  {key:"borates_ppm", label:"Borates ppm"}
];

function readFields(prefix){
  const out = {};
  MEASURES.forEach(function(m){
    const el = $(prefix + m.key);
    if(!el) return;
    const v = el.value.trim();
    out[m.key] = v === "" ? null : Number(v);
  });
  return out;
}

async function confirmReading(){
  if(!confirmRow) return;
  const vals = readFields("cf_");
  const rec = Object.assign({kind:"confirm", id:confirmRow.id}, vals);
  addPending("readings", Object.assign({id:confirmRow.id, at:confirmRow.at,
    date:confirmRow.date, confirmed:true, source:confirmRow.source}, vals));
  STATE.pending_confirmation = (STATE.pending_confirmation || [])
    .filter(function(r){ return r.id !== confirmRow.id; });
  render();
  await sendJson("confirm", rec, "Reading confirmed");
}

function renderManual(){
  if($("manualFields").children.length) return;
  $("manualFields").innerHTML = MEASURES.map(function(m){
    return '<div class="field"><label for="mn_' + m.key + '">' + esc(m.label) + '</label>' +
           '<input type="number" step="any" inputmode="decimal" id="mn_' + m.key + '"></div>';
  }).join("");
}

async function saveManual(){
  const vals = readFields("mn_");
  if(!MEASURES.some(function(m){ return vals[m.key] !== null; })){
    toast("Nothing to save — fill in at least one number"); return;
  }
  const at = nowIso();
  const rec = Object.assign({kind:"reading", id:at, at:at, source:"manual", confirmed:true}, vals);
  addPending("readings", Object.assign({id:at, at:at, date:at.slice(0,10),
    confirmed:true, source:"manual"}, vals));
  MEASURES.forEach(function(m){ if($("mn_" + m.key)) $("mn_" + m.key).value = ""; });
  render();
  await sendJson("reading", rec, "Reading saved");
}

/* ---- screenshot upload: downscale in a canvas first, then straight to the repo ---- */
function shrink(file, maxPx){
  return new Promise(function(resolve, reject){
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = function(){
      const s = Math.min(1, maxPx / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      const data = c.toDataURL("image/jpeg", 0.85);
      resolve(data.slice(data.indexOf(",") + 1));
    };
    img.onerror = function(){ URL.revokeObjectURL(url); reject(new Error("couldn't read that image")); };
    img.src = url;
  });
}

async function sendShot(){
  if(!shotFile) return;
  $("shot_send").disabled = true;
  $("shot_send").textContent = "Uploading…";
  try {
    const b64 = await shrink(shotFile, 1600);
    const ok = await send("inbox/" + nowStamp() + "-reading.jpg", b64,
                          "chore: reading screenshot from phone",
                          "Uploaded — it'll be read in about a minute");
    if(ok){ $("shot").value = ""; shotFile = null; }
  } catch(e){
    toast("Couldn't process that image");
  }
  $("shot_send").textContent = "Upload screenshot";
  $("shot_send").disabled = !shotFile;
}

function renderActionChips(){
  if($("actionChips").children.length) return;
  $("actionChips").innerHTML = (STATE.action_kinds || []).map(function(a){
    return '<button class="sym" data-act="' + esc(a.key) + '" data-unit="' + esc(a.unit) + '">' +
           esc(a.label) + '</button>';
  }).join("");
  Array.prototype.forEach.call(document.querySelectorAll("[data-act]"), function(b){
    b.addEventListener("click", function(){
      pickedAction = b.dataset.act;
      Array.prototype.forEach.call(document.querySelectorAll("[data-act]"), function(x){
        x.classList.toggle("on", x === b); });
      if(!$("a_unit").value) $("a_unit").value = b.dataset.unit || "";
    });
  });
}

async function saveAction(){
  if(!pickedAction){ toast("Pick what you did first"); return; }
  const d = $("a_date").value || today();
  const at = d === today() ? nowIso() : d + "T12:00:00";
  const amt = $("a_amount").value.trim();
  const label = (STATE.action_kinds.filter(function(k){ return k.key === pickedAction; })[0] || {}).label;
  const rec = {kind:"action", id:at, at:at, action:pickedAction,
               amount: amt === "" ? null : Number(amt), unit:$("a_unit").value.trim(),
               note:$("a_note").value.trim()};
  addPending("actions", {id:at, at:at, date:d, action:pickedAction, label:label || pickedAction,
                         amount:rec.amount, unit:rec.unit, note:rec.note});
  $("a_amount").value = ""; $("a_note").value = ""; $("a_unit").value = "";
  pickedAction = null;
  Array.prototype.forEach.call(document.querySelectorAll("[data-act]"), function(x){
    x.classList.remove("on"); });
  render();
  await sendJson("action", rec, "Logged");
}

function renderSafety(){
  $("safety").innerHTML = (STATE.safety_rules || []).map(function(s){
    return "<li>" + esc(s) + "</li>"; }).join("");
}

/* ------------------------------------------------------------- history */
function renderChart(){
  const series = ((STATE.trends || {}).series) || [];
  if(!series.length){
    $("metricbtns").innerHTML = "";
    $("chart").innerHTML = '<div class="note">No confirmed readings yet — the chart fills ' +
      'in as you log them.</div>';
    $("chartnote").textContent = "";
    return;
  }
  if(!chartMetric || !series.some(function(s){ return s.key === chartMetric; }))
    chartMetric = series[0].key;
  $("metricbtns").innerHTML = series.map(function(s){
    return '<button data-metric="' + esc(s.key) + '"' +
           (s.key === chartMetric ? ' class="on"' : '') + '>' + esc(s.label) + '</button>';
  }).join("");
  Array.prototype.forEach.call(document.querySelectorAll("[data-metric]"), function(b){
    b.addEventListener("click", function(){ chartMetric = b.dataset.metric; renderChart(); });
  });

  const s = series.filter(function(x){ return x.key === chartMetric; })[0];
  const W = 320, H = 150, PL = 38, PR = 8, PT = 10, PB = 22;
  const pts = s.points;
  const lo = s.low !== null && s.low !== undefined ? s.low : s.min;
  const hi = s.high !== null && s.high !== undefined ? s.high : s.max;
  let yMin = Math.min(s.min, lo), yMax = Math.max(s.max, hi);
  const pad = (yMax - yMin) * 0.15 || Math.abs(yMax * 0.05) || 1;
  yMin -= pad; yMax += pad;
  const x0 = dnum(pts[0].date), x1 = Math.max(dnum(pts[pts.length-1].date), x0 + 1);
  const X = function(d){ return PL + (dnum(d) - x0) / (x1 - x0) * (W - PL - PR); };
  const Y = function(v){ return PT + (1 - (v - yMin) / (yMax - yMin)) * (H - PT - PB); };

  let band = "";
  if(s.low !== null && s.low !== undefined || s.high !== null && s.high !== undefined){
    const yTop = Y(s.high !== null && s.high !== undefined ? s.high : yMax);
    const yBot = Y(s.low !== null && s.low !== undefined ? s.low : yMin);
    band = '<rect x="' + PL + '" y="' + yTop.toFixed(1) + '" width="' + (W-PL-PR) +
           '" height="' + Math.max(1, yBot - yTop).toFixed(1) +
           '" fill="#2f5c43" opacity="0.28"/>';
  }
  const line = pts.map(function(p, i){
    return (i ? "L" : "M") + X(p.date).toFixed(1) + " " + Y(p.v).toFixed(1); }).join(" ");
  const dots = pts.map(function(p){
    return '<circle cx="' + X(p.date).toFixed(1) + '" cy="' + Y(p.v).toFixed(1) +
           '" r="3" fill="#40c4d0"><title>' + esc(p.date + ": " + p.v) + '</title></circle>';
  }).join("");
  const ticks = [yMin, (yMin+yMax)/2, yMax].map(function(v){
    return '<text x="' + (PL-5) + '" y="' + (Y(v)+3.5).toFixed(1) +
           '" text-anchor="end" font-size="9" fill="#66798a">' +
           (Math.abs(v) >= 100 ? Math.round(v) : Math.round(v*10)/10) + '</text>' +
           '<line x1="' + PL + '" x2="' + (W-PR) + '" y1="' + Y(v).toFixed(1) + '" y2="' +
           Y(v).toFixed(1) + '" stroke="#2a3945" stroke-width="0.6"/>';
  }).join("");
  const xlab = '<text x="' + PL + '" y="' + (H-6) + '" font-size="9" fill="#66798a">' +
      esc(fmtDay(pts[0].date)) + '</text>' +
    '<text x="' + (W-PR) + '" y="' + (H-6) + '" text-anchor="end" font-size="9" fill="#66798a">' +
      esc(fmtDay(pts[pts.length-1].date)) + '</text>';

  $("chart").innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" ' +
    'style="height:' + H + 'px">' + band + ticks +
    '<path d="' + line + '" fill="none" stroke="#40c4d0" stroke-width="2" ' +
    'stroke-linejoin="round" stroke-linecap="round"/>' + dots + xlab + '</svg>';
  $("chartnote").textContent = pts.length + " confirmed reading(s) over the last " +
    ((STATE.trends || {}).days || 90) + " days. The green band is the target. " +
    "Gaps between readings are left as gaps — nothing is drawn through water nobody tested.";
}

function renderTimeline(){
  const rows = STATE.timeline || [];
  if(!rows.length){
    $("timeline").innerHTML = '<div class="note">Nothing logged yet.</div>'; return;
  }
  $("timeline").innerHTML = rows.map(function(r){
    let body;
    if(r.type === "reading"){
      body = '<div class="what">' + (r.confirmed ? "Reading" : "Reading (unconfirmed)") +
             (r.source && r.source !== "manual" ? " · " + esc(r.source) : "") + '</div>';
      if(r.values && r.values.length)
        body += '<div class="vals">' + r.values.map(function(v){
          return '<span class="vpill ' + esc(v.status) + '">' + esc(v.label) + " " +
                 esc(v.display) + '</span>'; }).join("") + '</div>';
    } else {
      body = '<div class="what">' + esc(r.label) +
             (r.amount ? " — " + r.amount + " " + esc(r.unit || "") : "") + '</div>';
    }
    if(r.note) body += '<div class="nt">' + esc(r.note) + '</div>';
    return '<div class="tlrow' + (r.type === "reading" && !r.confirmed ? " unconfirmed" : "") +
           '"><div class="when">' + esc(fmtDay(r.date)) + '</div><div>' + body + '</div></div>';
  }).join("");
}

/* ------------------------------------------------------------- opening */
function renderOpening(){
  const op = STATE.opening || {steps:[], done:0, total:0};
  const pend = pending().opening || {};
  const steps = (op.steps || []).map(function(s){
    return pend[s.id] ? Object.assign({}, s, {state:"done", confirmed_date:pend[s.id]}) : s;
  });
  /* a locally-ticked step unlocks the next one immediately, same as the engine would */
  let unlocked = true;
  steps.forEach(function(s){
    if(s.state !== "done") s.state = unlocked ? "current" : "locked";
    if(s.state !== "done") unlocked = false;
  });
  const done = steps.filter(function(s){ return s.state === "done"; }).length;

  $("openLead").textContent = op.total
    ? (done === op.total ? "All " + op.total + " steps done. The pool is open."
       : "Step " + (done + 1) + " of " + op.total + ". Each one unlocks the next.")
    : "No opening steps configured.";
  $("openBar").style.width = (op.total ? (done / op.total * 100) : 0) + "%";

  $("steps").innerHTML = steps.map(function(s){
    let h = '<div class="step ' + s.state + '"><div class="n">' +
            (s.state === "done" ? "✓" : s.n) + '</div><div>';
    h += '<div class="st">' + esc(s.title) + '</div>';
    h += '<div class="sd">' + esc(s.detail) + '</div>';
    if(s.state === "done")
      h += '<div class="smsg">Done ' + esc(fmtDay(s.confirmed_date)) + '</div>' +
           '<button class="btn ghost sm" data-undo="' + esc(s.id) + '">Undo this and after</button>';
    if(s.state === "current")
      h += '<label for="op_' + esc(s.id) + '">Date you did it</label>' +
           '<input type="date" id="op_' + esc(s.id) + '" value="' + today() + '">' +
           '<button class="btn sm" data-step="' + esc(s.id) + '">Mark done &amp; unlock next</button>';
    if(s.state === "locked")
      h += '<div class="note" style="margin-top:4px">Unlocks when the step above is done.</div>';
    return h + '</div></div>';
  }).join("");

  Array.prototype.forEach.call(document.querySelectorAll("[data-step]"), function(b){
    b.addEventListener("click", function(){ doStep(b.dataset.step); });
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-undo]"), function(b){
    b.addEventListener("click", function(){ undoStep(b.dataset.undo); });
  });
}

async function doStep(id){
  const el = $("op_" + id);
  const on = (el && el.value) || today();
  const p = pending(); p.opening = p.opening || {}; p.opening[id] = on; ls.set("pp_pending", p);
  renderOpening();
  await sendJson("opening", {kind:"opening", step:id, on:on}, "Step marked done");
}
async function undoStep(id){
  const op = STATE.opening || {steps:[]};
  const ids = (op.steps || []).map(function(s){ return s.id; });
  const p = pending(); p.opening = p.opening || {};
  ids.slice(ids.indexOf(id)).forEach(function(k){ delete p.opening[k]; });
  ls.set("pp_pending", p);
  renderOpening();
  await sendJson("opening", {kind:"opening", step:id, undo:true}, "Step reopened");
}

/* ------------------------------------------------------------ settings */
function renderSettings(){
  const p = STATE.pool || {}, e = STATE.equipment || {}, ref = STATE.reference || {};
  $("s_pool").innerHTML =
    esc((p.gallons || 0).toLocaleString()) + " gallons · " + esc(p.type || "") + " · " +
    esc(p.sanitization || "") + "<br>" + esc(e.salt_cell || "") + " cell, " +
    esc(e.salt_cell_increment_pct || 10) + "% per step · " + esc(e.skimmer || "") + " skimmer";
  $("s_ref").innerHTML = [
    "100 ppm of salt = <b>" + ref.salt_lb_per_100ppm + " lb</b>",
    "10 ppm of CYA = <b>" + ref.cya_lb_per_10ppm + " lb</b>",
    "0.1 of pH (at TA 80) = <b>" + ref.acid_floz_per_0_1ph_at_ta80 + " fl oz</b> of acid",
    "1 ppm of chlorine = <b>" + ref.chlorine_gal_per_1ppm + " gal</b>",
    "10 ppm of borate = <b>" + ref.boric_acid_lb_per_10ppm + " lb</b> of boric acid"
  ].join("<br>");
  const q = ls.get("pp_queue", []);
  $("s_queue").textContent = q.length
    ? (q.length + " item(s) waiting to sync.") : "Nothing waiting to sync.";
  $("s_built").textContent = "App built " + BUILT + ". Engine state from " +
    ((STATE && STATE.generated_at) || "?") + ".";
}

async function forceRebuild(){
  if(!connected()){ toast("Add a repo and token in Settings first"); return; }
  await sendJson("ping", {kind:"ping", date:today()}, "Rebuild requested");
}

/* ----------------------------------------------------------------- tabs */
function showTab(name){
  ["today","hist","open","set"].forEach(function(t){
    $("tab-" + t).classList.toggle("hide", t !== name);
  });
  Array.prototype.forEach.call(document.querySelectorAll("nav button"), function(b){
    b.classList.toggle("on", b.dataset.tab === name);
  });
  window.scrollTo(0, 0);
}

/* ------------------------------------------------------------------ init */
function initSettings(){
  const c = cfgApp();
  $("s_repo").value = c.repo; $("s_token").value = c.token;
  $("s_save").addEventListener("click", async function(){
    ls.set("pp_repo", $("s_repo").value.trim());
    ls.set("pp_token", $("s_token").value.trim());
    $("s_status").textContent = "Saved. Syncing…";
    await load(); await flush();
    $("s_status").textContent = connected() ? "Connected." : "Add both a repo and a token.";
  });
  $("s_refresh").addEventListener("click", function(){ load(); });
  $("s_rebuild").addEventListener("click", forceRebuild);
}

Array.prototype.forEach.call(document.querySelectorAll("nav button"), function(b){
  b.addEventListener("click", function(){ showTab(b.dataset.tab); });
});

initSettings();
$("a_date").value = today();
$("a_save").addEventListener("click", saveAction);
$("man_save").addEventListener("click", saveManual);
$("cf_ok").addEventListener("click", confirmReading);
$("cf_skip").addEventListener("click", function(){ $("confirmCard").classList.add("hide"); });
$("shot").addEventListener("change", function(e){
  shotFile = (e.target.files || [])[0] || null;
  $("shot_send").disabled = !shotFile;
});
$("shot_send").addEventListener("click", sendShot);
$("open_reset").addEventListener("click", async function(){
  ls.set("pp_pending", Object.assign(pending(), {opening:{}}));
  await sendJson("opening_reset", {kind:"opening_reset", date:today()}, "New season started");
});
$("hrefresh").addEventListener("click", function(){ load(); toast("Refreshing…", 1200); });

load();
if(!EMBED && "serviceWorker" in navigator){
  navigator.serviceWorker.register("sw.js").catch(function(){});
}
</script>
</body>
</html>
'''

MANIFEST = {
    "name": "Pool Pilot",
    "short_name": "Pool Pilot",
    "start_url": "./index.html",
    "scope": "./",
    "display": "standalone",
    "background_color": "#0d141a",
    "theme_color": "#0d141a",
    "orientation": "portrait",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}

SW = r'''/* Pool Pilot service worker: cache the shell, never cache the pool data.
   Readings are fetched live from the private repo (or served from localStorage
   when offline), so they never land in the HTTP cache. */
const SHELL = "poolpilot-shell-__BUILD__";
const FILES = ["./", "./index.html", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", function(e){
  self.skipWaiting();
  e.waitUntil(caches.open(SHELL).then(function(c){ return c.addAll(FILES); }).catch(function(){}));
});
self.addEventListener("activate", function(e){
  e.waitUntil(caches.keys().then(function(keys){
    return Promise.all(keys.filter(function(k){ return k !== SHELL; })
                           .map(function(k){ return caches.delete(k); }));
  }).then(function(){ return self.clients.claim(); }));
});
self.addEventListener("fetch", function(e){
  const url = new URL(e.request.url);
  if(url.hostname.indexOf("github") >= 0 || url.hostname === "127.0.0.1") return;  // always live
  if(e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request).then(function(r){
      const copy = r.clone();
      caches.open(SHELL).then(function(c){ c.put(e.request, copy); }).catch(function(){});
      return r;
    }).catch(function(){ return caches.match(e.request).then(function(m){
      return m || caches.match("./index.html"); }); })
  );
});
'''


# --------------------------------------------------------------------- build

# the handful of constants the JS mirror needs, exported from chem.py rather than
# retyped in JavaScript -- so changing a coefficient in one file changes both.
def chem_constants():
    return {
        "ppm_per_lb_per_10k": chem.PPM_PER_LB_PER_10K,
        "fc_ppm_per_gal_per_10k": chem.FC_PPM_PER_GAL_12_5_PER_10K,
        "acid_floz_per_ta_per_ph_per_10k": chem.ACID_FLOZ_PER_TA_PER_PH_PER_10K,
        "default_ta": chem.DEFAULT_TA,
        "lb_per_gal_water": chem.LB_PER_GAL_WATER,
    }


def _read_json(name, default=None):
    p = poolcfg.store_path(name)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
    return default


def _html(data_json, title, repo, built):
    return (TEMPLATE.replace("__DATA__", data_json)
                    .replace("__CHEM__", json.dumps(chem_constants()))
                    .replace("__TITLE__", title)
                    .replace("__REPO__", repo)
                    .replace("__BUILT__", built))


def main():
    title = poolcfg.title()
    repo = poolcfg.CONFIG["app"].get("data_repo", "")
    built = datetime.now().strftime("%Y-%m-%d %H:%M")
    app_dir, out_dir = poolcfg.path_of("app"), poolcfg.path_of("out")

    # public shell -- no pool data, ever
    open(os.path.join(app_dir, "index.html"), "w", encoding="utf-8").write(
        _html("null", title, repo, built))
    manifest = dict(MANIFEST, name=title, short_name=title)
    json.dump(manifest, open(os.path.join(app_dir, "manifest.webmanifest"), "w",
                             encoding="utf-8"), indent=2)
    open(os.path.join(app_dir, "sw.js"), "w", encoding="utf-8").write(
        SW.replace("__BUILD__", built.replace(" ", "-").replace(":", "")))
    for size in (192, 512):
        open(os.path.join(app_dir, "icon-%d.png" % size), "wb").write(_png(size, BG, ACCENT))
    open(os.path.join(app_dir, ".nojekyll"), "w", encoding="utf-8").write("")
    open(os.path.join(app_dir, "robots.txt"), "w", encoding="utf-8").write(
        "User-agent: *\nDisallow: /\n")

    # local copy -- data inlined, opens straight off disk
    embedded = {"state": _read_json("state.json")}
    open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8").write(
        _html(json.dumps(embedded, ensure_ascii=False), title, repo, built))

    print("pwa built: %s (public shell) and %s (local, data inlined)"
          % (os.path.join(app_dir, "index.html"), os.path.join(out_dir, "index.html")))


if __name__ == "__main__":
    main()
