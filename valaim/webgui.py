"""Browser-based control panel for ValAim.

Runs a tiny HTTP server on localhost and serves a single-page UI with live
sliders. Uses only the Python standard library (http.server, json, webbrowser),
so it packages cleanly into the exe - no tkinter, no Electron, no extra deps.

Launch the exe and your browser opens at http://127.0.0.1:8765 automatically.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import AimConfig

CONFIG_FILE = "valaim_gui.json"
HOST = "127.0.0.1"
PORT = 8765


def _config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.getcwd()
    return os.path.join(base, CONFIG_FILE)


def load_config() -> AimConfig:
    cfg = AimConfig()
    path = _config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(cfg, k):
                    if k in ("target_classes", "exclude_classes") and isinstance(v, list):
                        v = tuple(v)
                    setattr(cfg, k, v)
        except (OSError, ValueError):
            pass
    return cfg


def save_config(cfg: AimConfig) -> None:
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(asdict(cfg), f, indent=2)
        return
    except OSError:
        pass


NUMERIC = {
    "fov_radius": int, "conf_threshold": float, "iou_threshold": float,
    "head_bias": float, "head_offset_y": float,
    "move_fraction": float, "max_step": int, "smoothing": float,
    "deadzone": float, "fps": int, "trigger_radius": int,
}


class Panel:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.lock = threading.Lock()
        self.status = "Idle - press Start when the phone app is connected."
        self.running = False
        self.stop_flag = threading.Event()
        self.worker: threading.Thread | None = None

    def snapshot(self) -> dict:
        with self.lock:
            data = asdict(self.cfg)
            data["target_classes"] = " ".join(self.cfg.target_classes)
            data["status"] = self.status
            data["running"] = self.running
            return data

    def apply(self, patch: dict) -> str | None:
        with self.lock:
            for key, value in patch.items():
                if key in NUMERIC:
                    try:
                        setattr(self.cfg, key, NUMERIC[key](value))
                    except (TypeError, ValueError):
                        return f"bad value for {key}"
                elif key == "classes":
                    self.cfg.target_classes = tuple(str(value).split())
                elif key == "bt_host":
                    self.cfg.bt_host = str(value).strip() or None
                elif key == "bt_port":
                    try:
                        self.cfg.bt_port = int(value)
                    except ValueError:
                        return "bad port"
                elif key == "trigger":
                    self.cfg.keybind = str(value).strip().upper()
                    self.cfg.hold_button = ""
                elif key == "always_on":
                    if value:
                        self.cfg.keybind = ""
                        self.cfg.hold_button = ""
                elif key in ("debug", "triggerbot"):
                    setattr(self.cfg, key, bool(value))
            return None

    def start(self) -> str:
        if self.running:
            return "already running"
        if not self.cfg.bt_host:
            return "phone IP is empty"
        self.stop_flag.clear()
        self.running = True
        self.status = "Starting..."
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()
        return "started"

    def stop(self) -> str:
        if not self.running:
            return "not running"
        self.stop_flag.set()
        self.running = False
        self.status = "Stopping..."
        return "stopping"

    def _run(self) -> None:
        from .main import run

        class StatusBox:
            def __init__(self, panel: "Panel") -> None:
                self.panel = panel

            def set(self, text: str) -> None:
                self.panel.status = text

        try:
            with self.lock:
                cfg = self.cfg
                run(cfg, stop_flag=self.stop_flag, status=StatusBox(self))
        except Exception as exc:  # noqa: BLE001
            self.status = f"Error: {exc}"
        finally:
            self.running = False


PANEL = Panel()


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>ValAim</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#14161a;--card:#1d2126;--fg:#e8eaed;--mut:#9aa0a6;--acc:#4f8cff;--ok:#34d399;--warn:#fbbf24}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:16px}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:20px;margin:0 0 12px}
.card{background:var(--card);border-radius:10px;padding:14px 16px;margin-bottom:12px}
.card h2{font-size:13px;color:var(--mut);margin:0 0 10px;text-transform:uppercase;letter-spacing:.05em}
.row{display:flex;align-items:center;gap:10px;margin:6px 0}
.row label{flex:0 0 190px;font-size:13px}
.row input[type=range]{flex:1}
.row .val{flex:0 0 56px;text-align:right;font-variant-numeric:tabular-nums;font-size:13px;color:var(--acc)}
input[type=text]{background:#101316;color:var(--fg);border:1px solid #2c3137;border-radius:6px;padding:6px 8px;font-size:14px}
button{border:0;border-radius:8px;padding:10px 18px;font-size:14px;font-weight:600;cursor:pointer}
#start{background:var(--ok);color:#06281b}
#stop{background:#ef4444;color:#2b0606}
#save{background:#374151;color:#e5e7eb}
.bar{display:flex;gap:10px;margin:14px 0}
#status{background:var(--card);border-left:3px solid var(--acc);border-radius:6px;padding:10px 12px;font-size:13px;min-height:40px;color:var(--fg)}
#status.run{border-color:var(--ok)}
#status.err{border-color:#ef4444}
.hint{color:var(--mut);font-size:12px;margin-top:4px}
</style></head><body><div class="wrap">
<h1>ValAim - control panel</h1>

<div class="card"><h2>Phone (Bluetooth bridge)</h2>
<div class="row"><label>Phone IP (from app)</label><input type=text id="bt_host" size=16>
<label style="flex:0 0 auto">Port</label><input type=text id="bt_port" size=7></div>
<div class="hint">Run BtAimBridge on the phone, press START there, pair as Bluetooth mouse, then put its IP here.</div>
</div>

<div class="card"><h2>Aim point</h2>
<div class="row"><label>Head bias (0 top - 1 bottom)</label><input type=range id="head_bias" min=0 max=1 step=0.01><span class=val id="head_bias_v"></span></div>
<div class="row"><label>Head offset Y (px down)</label><input type=range id="head_offset_y" min=-30 max=80 step=1><span class=val id="head_offset_y_v"></span></div>
</div>

<div class="card"><h2>Detection</h2>
<div class="row"><label>Classes</label><input type=text id="classes" size=18></div>
<div class="row"><label>FOV radius (px)</label><input type=range id="fov_radius" min=20 max=500 step=5><span class=val id="fov_radius_v"></span></div>
<div class="row"><label>Confidence</label><input type=range id="conf_threshold" min=0.05 max=0.95 step=0.01><span class=val id="conf_threshold_v"></span></div>
</div>

<div class="card"><h2>Movement feel</h2>
<div class="row"><label>Move fraction</label><input type=range id="move_fraction" min=0.05 max=1 step=0.01><span class=val id="move_fraction_v"></span></div>
<div class="row"><label>Max step (px/frame)</label><input type=range id="max_step" min=2 max=127 step=1><span class=val id="max_step_v"></span></div>
<div class="row"><label>Smoothing (lower=steadier)</label><input type=range id="smoothing" min=0.05 max=1 step=0.01><span class=val id="smoothing_v"></span></div>
<div class="row"><label>Dead zone (px)</label><input type=range id="deadzone" min=0 max=10 step=0.5><span class=val id="deadzone_v"></span></div>
<div class="row"><label>FPS cap</label><input type=range id="fps" min=30 max=240 step=5><span class=val id="fps_v"></span></div>
</div>

<div class="card"><h2>Trigger</h2>
<div class="row"><label>Always on</label><input type=checkbox id="always_on"><label style="flex:0 0 auto">or hold key</label><input type=text id="trigger" size=10></div>
<div class="row"><label>Triggerbot (auto click)</label><input type=checkbox id="triggerbot"></div>
<div class="row"><label>Show debug window</label><input type=checkbox id="debug"></div>
</div>

<div class="bar">
<button id="start">START</button>
<button id="stop">STOP</button>
<button id="save">Save</button>
</div>

<div id="status"></div>

<script>
const NUM=["head_bias","head_offset_y","fov_radius","conf_threshold","move_fraction","max_step","smoothing","deadzone","fps"];
let lastPatch={};
function fill(s){
  document.getElementById("bt_host").value=s.bt_host||"";
  document.getElementById("bt_port").value=s.bt_port||47800;
  document.getElementById("classes").value=s.target_classes||"Head";
  document.getElementById("trigger").value=s.keybind||"";
  document.getElementById("always_on").checked=!s.keybind&&!s.hold_button;
  document.getElementById("triggerbot").checked=!!s.triggerbot;
  document.getElementById("debug").checked=!!s.debug;
  for(const k of NUM){
    const el=document.getElementById(k);
    if(el&&document.activeElement!==el){el.value=s[k];}
    document.getElementById(k+"_v").textContent=(+s[k]).toFixed(k.includes("threshold")||k.includes("fraction")||k==="smoothing"?2:0);
  }
  const st=document.getElementById("status");
  st.textContent=s.status;
  st.className=s.running?"run":(s.status.startsWith("Error")?"err":"");
  document.getElementById("start").disabled=s.running;
  document.getElementById("stop").disabled=!s.running;
}
async function poll(){
  try{
    const s=await(await fetch("/state")).json();
    fill(s);
    if(s.running) setTimeout(poll,300); else setTimeout(poll,1000);
  }catch(e){document.getElementById("status").textContent="panel lost connection"; setTimeout(poll,1000);}
}
function send(obj){
  fetch("/set",{"method":"POST","headers":{"Content-Type":"application/json"},"body":JSON.stringify(obj)});
}
function track(id){
  const el=document.getElementById(id);
  el.addEventListener("change",()=>send({[id]:el.value!==undefined&&el.type==="checkbox"?el.checked:el.value}));
  if(el.type==="range") el.addEventListener("input",()=>{
    document.getElementById(id+"_v").textContent=(+el.value).toFixed(id.includes("threshold")||id.includes("fraction")||id==="smoothing"?2:0);
    send({[id]:el.value});
  });
}
NUM.forEach(track);
["bt_host","bt_port","classes","trigger","always_on","triggerbot","debug"].forEach(track);
document.getElementById("start").onclick=()=>fetch("/start");
document.getElementById("stop").onclick=()=>fetch("/stop");
document.getElementById("save").onclick=()=>fetch("/save");
poll();
</script></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/state"):
            self._send(200, json.dumps(PANEL.snapshot()).encode("utf-8"), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            patch = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send(400, b"bad json", "text/plain")
            return
        if self.path == "/set":
            err = PANEL.apply(patch)
            self._send(400 if err else 200, (err or "ok").encode(), "text/plain")
        elif self.path == "/start":
            self._send(200, PANEL.start().encode(), "text/plain")
        elif self.path == "/stop":
            self._send(200, PANEL.stop().encode(), "text/plain")
        elif self.path == "/save":
            save_config(PANEL.cfg)
            self._send(200, b"saved", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"ValAim panel: {url}")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
