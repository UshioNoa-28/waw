"""Browser-based control panel for ValAim.

Runs a tiny HTTP server on localhost and serves a single-page UI with live
sliders. Uses only the Python standard library (http.server, json, webbrowser),
so it packages cleanly into the exe - no tkinter, no Electron, no extra deps.

Launch the exe and your browser opens at http://127.0.0.1:8765 automatically.
Closing the tab exits the program (pagehide beacon + idle watchdog).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import AimConfig

CONFIG_FILE = "valaim_gui.json"
HOST = "127.0.0.1"
PORT = 8765

# Quit when the browser tab says goodbye (pagehide beacon), or as a fallback
# when its heartbeat stops (browser crash / killed). Background tabs are
# throttled to ~1 poll/min by browsers, so the idle limit must be well above
# that to avoid quitting while the user is alt-tabbed into the game.
IDLE_TIMEOUT_S = 150.0

_session = {"opened": False, "last_seen": 0.0, "bye": False}


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
    # Frozen exe: a relative model path must be resolved to the bundled copy.
    cfg.model_path = resolve_model_path_safe(cfg.model_path)
    if cfg.model_info:
        cfg.model_info = resolve_model_path_safe(cfg.model_info)
    return cfg


def resolve_model_path_safe(path: str) -> str:
    try:
        from .resources import resolve_model_path

        return resolve_model_path(path)
    except Exception:
        return path


def save_config(cfg: AimConfig) -> None:
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(asdict(cfg), f, indent=2)
    except OSError:
        pass


NUMERIC = {
    "fov_radius": int, "conf_threshold": float, "iou_threshold": float,
    "head_bias": float, "head_offset_y": float,
    "move_fraction": float, "max_step": int, "smoothing": float,
    "deadzone": float, "aim_dz_frac": float, "fps": int, "trigger_radius": int, "aim_gain": float,
    "aim_lead": float, "sens": float, "fire_radius": int,
    "aim_floor": float, "aim_comp": int, "aim_cw": float,
    "burst_cooldown": float, "burst_gain": float, "settle_ms": float, "settle_frac": float,
    "arrive_px": float, "resume_px": float,
}


class Panel:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.lock = threading.Lock()
        self.status = "空闲 - 确认手机 App 已连接后点启动。"
        self.running = False
        self.stop_flag = threading.Event()
        self.worker: threading.Thread | None = None

    def snapshot(self) -> dict:
        with self.lock:
            data = asdict(self.cfg)
            data["target_classes"] = " ".join(self.cfg.target_classes)
            data["status"] = self.status
            data["running"] = self.running
        try:
            from .input_ctrl import raw_input_state

            active, _events = raw_input_state()
            data["raw_sink"] = active
        except Exception:
            data["raw_sink"] = False
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
                elif key in ("debug", "triggerbot", "burst"):
                    setattr(self.cfg, key, bool(value))
            return None

    def start(self) -> str:
        if self.running:
            return "已经在运行"
        if not self.cfg.bt_host:
            return "请先填写手机 IP"
        path = resolve_model_path_safe(self.cfg.model_path)
        if not os.path.exists(path):
            return f"模型文件不存在: {self.cfg.model_path}"
        self.cfg.model_path = path
        self.stop_flag.clear()
        self.running = True
        self.status = "启动中..."
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
        class StatusBox:
            def __init__(self, panel: "Panel") -> None:
                self.panel = panel

            def set(self, text: str) -> None:
                self.panel.status = text

        try:
            from .main import run

            with self.lock:
                cfg = self.cfg
            run(cfg, stop_flag=self.stop_flag, status=StatusBox(self))
        except Exception as exc:  # noqa: BLE001
            self.status = f"错误: {exc}"
        finally:
            try:
                from .input_ctrl import close_backend

                close_backend()
            except Exception:
                pass
            self.running = False


PANEL = Panel()


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>ValAim 控制台</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#14161a;--card:#1d2126;--fg:#e8eaed;--mut:#9aa0a6;--acc:#4f8cff;--ok:#34d399;--warn:#fbbf24}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font-family:'Segoe UI',system-ui,'Microsoft YaHei',sans-serif;margin:0;padding:16px}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:20px;margin:0 0 12px}
.card{background:var(--card);border-radius:10px;padding:14px 16px;margin-bottom:12px}
.card h2{font-size:13px;color:var(--mut);margin:0 0 10px;text-transform:uppercase;letter-spacing:.05em}
.row{display:flex;align-items:center;gap:10px;margin:6px 0}
.row label{flex:0 0 200px;font-size:13px}
.row input[type=range]{flex:1}
.row .val{flex:0 0 56px;text-align:right;font-variant-numeric:tabular-nums;font-size:13px;color:var(--acc)}
input[type=text]{background:#101316;color:var(--fg);border:1px solid #2c3137;border-radius:6px;padding:6px 8px;font-size:14px}
input[type=checkbox]{width:18px;height:18px}
button{border:0;border-radius:8px;padding:10px 18px;font-size:14px;font-weight:600;cursor:pointer}
#start{background:var(--ok);color:#06281b}
#stop{background:#ef4444;color:#2b0606}
#save{background:#374151;color:#e5e7eb}
#quit{background:#3f3f46;color:#fde68a}
button:disabled{opacity:.4;cursor:default}
.bar{display:flex;gap:10px;margin:14px 0}
#status{background:var(--card);border-left:3px solid var(--acc);border-radius:6px;padding:10px 12px;font-size:13px;min-height:40px;color:var(--fg)}
#status.run{border-color:var(--ok)}
#status.err{border-color:#ef4444}
.hint{color:var(--mut);font-size:12px;margin-top:4px}
</style></head><body><div class="wrap">
<h1>ValAim 控制台</h1>

<div class="card"><h2>手机(蓝牙 HID 桥接)</h2>
<div class="row"><label>手机 IP(看 App 显示)</label><input type=text id="bt_host" size=16>
<label style="flex:0 0 auto">端口</label><input type=text id="bt_port" size=7></div>
<div class="hint">手机打开 BtAimBridge,点 START,在 Windows 蓝牙里配对,然后把 App 显示的 IP 填进来。</div>
</div>

<div class="card"><h2>增益(计数/像素)</h2>
<div class="row"><label>游戏内灵敏度(>0 用公式)</label><input type=text id="sens" size=8></div>
<div class="row"><label>手动增益(0=自动)</label><input type=range id="aim_gain" min=0 max=5 step=0.05><span class=val id="aim_gain_v"></span></div>
<div class="hint">优先用标定文件 valaim_calib.json;留空/0 则按灵敏度公式自动算。</div>
</div>

<div class="card"><h2>瞄准点</h2>
<div class="row"><label>头部偏置(0顶 1底)</label><input type=range id="head_bias" min=0 max=1 step=0.01><span class=val id="head_bias_v"></span></div>
<div class="row"><label>头部纵向偏移(px)</label><input type=range id="head_offset_y" min=-30 max=80 step=1><span class=val id="head_offset_y_v"></span></div>
</div>

<div class="card"><h2>检测</h2>
<div class="row"><label>目标类别(空格分隔)</label><input type=text id="classes" size=18></div>
<div class="row"><label>视野半径 FOV(px)</label><input type=range id="fov_radius" min=20 max=500 step=5><span class=val id="fov_radius_v"></span></div>
<div class="row"><label>置信度阈值</label><input type=range id="conf_threshold" min=0.05 max=0.95 step=0.01><span class=val id="conf_threshold_v"></span></div>
</div>

<div class="card"><h2>移动手感</h2>
<div class="row"><label>单发吸附(推荐开:一枪到位再补)</label><input type=checkbox id="burst"></div>
<div class="row"><label>开火后间隔(秒,≥你的网络延迟)</label><input type=range id="burst_cooldown" min=0.1 max=0.5 step=0.01><span class=val id="burst_cooldown_v"></span></div>
<div class="row"><label>弹道力度(误差×系数)</label><input type=range id="burst_gain" min=0.5 max=1.3 step=0.05><span class=val id="burst_gain_v"></span></div>
<div class="row"><label>稳定门(毫秒,手停才开火)</label><input type=range id="settle_ms" min=30 max=250 step=5><span class=val id="settle_ms_v"></span></div>
<div class="row"><label>稳定阈值(头框宽×)</label><input type=range id="settle_frac" min=0.8 max=3 step=0.1><span class=val id="settle_frac_v"></span></div>
<div class="row"><label>吸住半径(px)</label><input type=range id="arrive_px" min=0 max=20 step=1><span class=val id="arrive_px_v"></span></div>
<div class="row"><label>脱锁半径(px)</label><input type=range id="resume_px" min=8 max=80 step=2><span class=val id="resume_px_v"></span></div>
<div class="row"><label>每帧最大移动(计数)</label><input type=range id="max_step" min=2 max=600 step=10><span class=val id="max_step_v"></span></div>
<div class="row"><label>移动系数(仅单发关闭时)</label><input type=range id="move_fraction" min=0.05 max=1 step=0.01><span class=val id="move_fraction_v"></span></div>
<div class="row"><label>移动提前量(秒,仅单发关闭时)</label><input type=range id="aim_lead" min=0 max=0.3 step=0.01><span class=val id="aim_lead_v"></span></div>
<div class="row"><label>在途补偿(帧,仅单发关闭时)</label><input type=range id="aim_comp" min=0 max=8 step=1><span class=val id="aim_comp_v"></span></div>
<div class="row"><label>补偿权重</label><input type=range id="aim_cw" min=0 max=1 step=0.05><span class=val id="aim_cw_v"></span></div>
<div class="row"><label>最低速度(计数/帧,仅单发关闭时)</label><input type=range id="aim_floor" min=0 max=6 step=0.5><span class=val id="aim_floor_v"></span></div>
<div class="row"><label>平滑(越小越稳)</label><input type=range id="smoothing" min=0.05 max=1 step=0.01><span class=val id="smoothing_v"></span></div>
<div class="row"><label>死区比例(头框宽×)</label><input type=range id="aim_dz_frac" min=0 max=0.5 step=0.01><span class=val id="aim_dz_frac_v"></span></div>
<div class="row"><label>死区(固定像素)</label><input type=range id="deadzone" min=0 max=20 step=0.5><span class=val id="deadzone_v"></span></div>
<div class="row"><label>帧率上限</label><input type=range id="fps" min=30 max=240 step=5><span class=val id="fps_v"></span></div>
</div>

<div class="card"><h2>触发与辅助</h2>
<div class="row"><label>自动开火(吸上钉住后手机替你按左键)</label><input type=checkbox id="triggerbot"></div>
<div class="row"><label>开火半径(px)</label><input type=range id="fire_radius" min=4 max=60 step=2 style="flex:1"><span class=val id="fire_radius_v"></span></div>
<div class="row"><label>显示画面预览(debug 窗口)</label><input type=checkbox id="debug"></div>
<div class="hint">说明:游戏在前台时 Vanguard 屏蔽一切第三方按键读取,键盘/侧键绑定无效——吸附默认常驻开启,想暂停按手机 App 上的「锁定」按钮。</div>
</div>

<div class="bar">
<button id="start">启 动</button>
<button id="stop">停 止</button>
<button id="save">保 存</button>
<button id="quit">退出程序</button>
</div>

<div id="status">加载中...</div>

<script>
const NUM=["head_bias","head_offset_y","fov_radius","conf_threshold","move_fraction","max_step","smoothing","deadzone","aim_dz_frac","aim_gain","aim_lead","aim_comp","aim_cw","aim_floor","fire_radius","fps","burst_cooldown","burst_gain","settle_ms","settle_frac","arrive_px","resume_px"];
const DEC={burst_cooldown:2,burst_gain:2,settle_ms:0,settle_frac:1,arrive_px:0,resume_px:0,head_bias:2,conf_threshold:2,move_fraction:2,smoothing:2,aim_gain:2,aim_lead:2,aim_comp:0,aim_cw:2,aim_floor:1,fire_radius:0,fov_radius:0,max_step:0,deadzone:1,aim_dz_frac:2,fps:0,head_offset_y:0};
function fill(s){
  const act=document.activeElement;
  for(const id of ["bt_host","bt_port","classes"]){
    const el=document.getElementById(id);
    if(el&&el!==act){
      const want=id==="classes"?(s.target_classes||""):String(s[id]??(id==="bt_port"?47800:""));
      if(el.value!==want) el.value=want;
    }
  }
  const sv=document.getElementById("sens"); if(sv&&sv!==act) sv.value=s.sens>0?s.sens:"";
  const tb=document.getElementById("triggerbot"); if(tb&&tb!==act) tb.checked=!!s.triggerbot;
  const dg=document.getElementById("debug"); if(dg&&dg!==act) dg.checked=!!s.debug;
  const bu=document.getElementById("burst"); if(bu&&bu!==act) bu.checked=!!s.burst;
  for(const k of NUM){
    const el=document.getElementById(k);
    if(el!==act) el.value=s[k];
    document.getElementById(k+"_v").textContent=(+s[k]).toFixed(DEC[k]??1);
  }
  const st=document.getElementById("status");
  st.textContent=s.status;
  st.className=s.running?"run":(s.status.startsWith("错误")?"err":"");
  document.getElementById("start").disabled=s.running;
  document.getElementById("stop").disabled=!s.running;
}
async function poll(){
  try{ fill(await(await fetch("/state")).json()); }
  catch(e){ document.getElementById("status").textContent="与控制台断开连接,请重启 ValAim.exe"; }
  setTimeout(poll,300);
}
function send(obj){
  return fetch("/set",{"method":"POST","headers":{"Content-Type":"application/json"},"body":JSON.stringify(obj)});
}
function track(id){
  const el=document.getElementById(id);
  const push=()=>send(id==="triggerbot"?{triggerbot:el.checked}:id==="debug"?{debug:el.checked}:id==="burst"?{burst:el.checked}:{[id]:el.value});
  if(el.type==="checkbox") el.addEventListener("change",push);
  else{ el.addEventListener("input",push); el.addEventListener("change",push); }
}
NUM.forEach(track);
["bt_host","bt_port","classes","sens","triggerbot","debug","burst"].forEach(track);
function postp(path){return fetch(path,{method:"POST"});}
document.getElementById("start").onclick=async()=>{
  await send({bt_host:document.getElementById("bt_host").value,bt_port:document.getElementById("bt_port").value});
  const t=await(await postp("/start")).text();
  document.getElementById("status").textContent=t==="started"?"启动中...":("错误: "+t);
};
document.getElementById("stop").onclick=async()=>{ await postp("/stop"); };
document.getElementById("save").onclick=async()=>{
  const t=await(await postp("/save")).text();
  document.getElementById("status").textContent=t==="saved"?"已保存":"保存失败";
};
document.getElementById("quit").onclick=async()=>{
  document.getElementById("status").textContent="已退出,可关闭本页面。";
  await postp("/bye");
};
// Closing this tab/browser tells the exe to quit (beacon survives page unload).
window.addEventListener("pagehide",()=>{try{navigator.sendBeacon("/bye");}catch(e){}});
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
            _session["opened"] = True
            _session["last_seen"] = time.time()
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/state"):
            _session["opened"] = True
            _session["last_seen"] = time.time()
            self._send(200, json.dumps(PANEL.snapshot()).encode("utf-8"), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            patch = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            patch = {}
        if self.path == "/set":
            err = PANEL.apply(patch)
            self._send(400 if err else 200, (err or "ok").encode(), "text/plain")
        elif self.path == "/bye":
            _session["bye"] = True
            self._send(200, b"bye", "text/plain")
        elif self.path == "/start":
            self._send(200, PANEL.start().encode(), "text/plain")
        elif self.path == "/stop":
            self._send(200, PANEL.stop().encode(), "text/plain")
        elif self.path == "/save":
            save_config(PANEL.cfg)
            self._send(200, b"saved", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")


def _shutdown(server: ThreadingHTTPServer) -> None:
    save_config(PANEL.cfg)
    PANEL.stop_flag.set()
    worker = PANEL.worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=1.5)
    print("Panel closed - exiting ValAim.")
    threading.Thread(target=server.shutdown, daemon=True).start()
    time.sleep(0.3)
    os._exit(0)


def _watchdog(server: ThreadingHTTPServer) -> None:
    while True:
        time.sleep(2.0)
        if not _session["opened"]:
            continue
        gone = _session["bye"] or (time.time() - _session["last_seen"] > IDLE_TIMEOUT_S)
        if gone:
            _shutdown(server)


def _fatal(message: str) -> None:
    """Show a startup failure to the user even in windowed (no-console) mode."""
    print(message, file=sys.stderr)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "ValAim", 0x10)
    except Exception:
        pass


def main() -> None:
    try:
        server = None
        port = 0
        for candidate in range(PORT, PORT + 12):
            try:
                server = ThreadingHTTPServer((HOST, candidate), Handler)
                port = candidate
                break
            except OSError:
                continue
        if server is None:
            _fatal(
                f"无法启动控制面板:端口 {PORT}-{PORT + 11} 都被占用。\n"
                "很可能有一个旧的 ValAim.exe 还在后台运行。\n\n"
                "解决办法:打开任务管理器,结束所有 ValAim.exe 进程后重新打开。"
            )
            return
        url = f"http://{HOST}:{port}"
        print(f"ValAim panel: {url}")
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        threading.Thread(target=_watchdog, args=(server,), daemon=True).start()
        server.serve_forever()
    except KeyboardInterrupt:
        _shutdown(server)
    except Exception as exc:  # noqa: BLE001
        _fatal(f"控制面板启动失败: {exc}")
