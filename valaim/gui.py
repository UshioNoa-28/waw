"""Tkinter control panel for ValAim.

Runs the aim loop in a background thread and exposes the tunable parameters as
live controls. Changing a slider takes effect on the next frame - no restart,
no command line.

Only depends on the standard library (tkinter), so it works inside the frozen
exe.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import tkinter as tk
from dataclasses import asdict
from tkinter import ttk
from tkinter import messagebox

from .config import AimConfig


CONFIG_FILE = "valaim_gui.json"


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
        data = asdict(cfg)
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


class ValAimGUI:
    def __init__(self, cfg: AimConfig) -> None:
        self.cfg = cfg
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()

        self.root = tk.Tk()
        self.root.title("ValAim")
        self.root.geometry("460x720")
        self.root.minsize(420, 600)

        self.status_var = tk.StringVar(value="Idle")

        self._build()

    # -- layout --------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 3}

        tk.Label(self.root, text="ValAim", font=("Segoe UI", 16, "bold")).pack(anchor="w", **pad)

        # IP / port
        net = tk.LabelFrame(self.root, text="Phone (Bluetooth bridge)")
        net.pack(fill="x", **pad)
        self.ip_var = tk.StringVar(value=self.cfg.bt_host or "")
        self.port_var = tk.StringVar(value=str(self.cfg.bt_port))
        row = tk.Frame(net)
        row.pack(fill="x", padx=6, pady=4)
        tk.Label(row, text="Phone IP").pack(side="left")
        tk.Entry(row, textvariable=self.ip_var, width=16).pack(side="left", padx=4)
        tk.Label(row, text="Port").pack(side="left")
        tk.Entry(row, textvariable=self.port_var, width=7).pack(side="left", padx=4)

        # Aim point
        head = tk.LabelFrame(self.root, text="Aim point")
        head.pack(fill="x", **pad)
        self._slider(head, "head_bias", "Head bias (0=top,1=bottom)", 0.0, 1.0, 0.01, self.cfg.head_bias)
        self._slider(head, "head_offset_y", "Head offset Y (px down)", -40, 60, 1, self.cfg.head_offset_y)

        # Detection
        det = tk.LabelFrame(self.root, text="Detection")
        det.pack(fill="x", **pad)
        self._slider(det, "conf_threshold", "Confidence", 0.05, 0.95, 0.01, self.cfg.conf_threshold)
        self._slider(det, "fov_radius", "FOV radius (px)", 20, 500, 5, self.cfg.fov_radius)
        self.classes_var = tk.StringVar(value="Head")
        r = tk.Frame(det)
        r.pack(fill="x", padx=6, pady=3)
        tk.Label(r, text="Classes (blank = all)").pack(side="left")
        tk.Entry(r, textvariable=self.classes_var, width=18).pack(side="left", padx=4)

        # Movement feel
        mv = tk.LabelFrame(self.root, text="Movement feel")
        mv.pack(fill="x", **pad)
        self._slider(mv, "move_fraction", "Move fraction", 0.05, 1.0, 0.01, self.cfg.move_fraction)
        self._slider(mv, "max_step", "Max step (px/frame)", 2, 127, 1, self.cfg.max_step)
        self._slider(mv, "smoothing", "Smoothing (low=steady)", 0.05, 1.0, 0.01, self.cfg.smoothing)
        self._slider(mv, "deadzone", "Dead zone (px)", 0.0, 10.0, 0.5, self.cfg.deadzone)
        self._slider(mv, "fps", "FPS cap", 30, 240, 10, self.cfg.fps)

        # Trigger
        trg = tk.LabelFrame(self.root, text="Trigger")
        trg.pack(fill="x", **pad)
        self.always_var = tk.BooleanVar(value=(not self.cfg.keybind and not self.cfg.hold_button))
        tk.Checkbutton(trg, text="Always on (no trigger key)", variable=self.always_var,
                       command=self._on_always).pack(anchor="w", padx=6, pady=2)
        self.key_var = tk.StringVar(value=self.cfg.keybind or "")
        rk = tk.Frame(trg)
        rk.pack(fill="x", padx=6, pady=3)
        tk.Label(rk, text="Or hold key").pack(side="left")
        tk.Entry(rk, textvariable=self.key_var, width=10).pack(side="left", padx=4)

        # Buttons
        btns = tk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.start_btn = tk.Button(btns, text="START", height=2, command=self.toggle)
        self.start_btn.pack(side="left", fill="x", expand=True)
        tk.Button(btns, text="Save", command=self.save).pack(side="left", padx=4)
        tk.Button(btns, text="Quit", command=self.quit).pack(side="left")

        self.debug_var = tk.BooleanVar(value=self.cfg.debug)
        tk.Checkbutton(self.root, text="Show detection preview window (--debug)",
                       variable=self.debug_var).pack(anchor="w", **pad)

        # status
        tk.Label(self.root, textvariable=self.status_var, fg="#1565C0",
                 wraplength=420, justify="left").pack(anchor="w", fill="x", **pad)

    def _slider(self, parent, attr, label, lo, hi, step, value):
        f = tk.Frame(parent)
        f.pack(fill="x", padx=6, pady=2)
        tk.Label(f, text=label, width=26, anchor="w").pack(side="left")
        val_lbl = tk.Label(f, text=f"{value:g}", width=6, anchor="e")
        val_lbl.pack(side="right")

        def on_change(v, attr=attr, val_lbl=val_lbl):
            fv = float(v)
            if step >= 1:
                fv = float(int(round(fv)))
            setattr(self.cfg, attr, fv)
            val_lbl.config(text=f"{fv:g}")

        s = tk.Scale(f, from_=lo, to=hi, resolution=step, orient="horizontal",
                     showvalue=False, command=on_change)
        s.set(value)
        s.pack(side="left", fill="x", expand=True)

    # -- actions -------------------------------------------------------

    def _on_always(self):
        if self.always_var.get():
            self.cfg.keybind = ""
            self.cfg.hold_button = ""
            self.key_var.set("")

    def _apply(self) -> AimConfig:
        c = self.cfg
        c.bt_host = self.ip_var.get().strip() or None
        try:
            c.bt_port = int(self.port_var.get())
        except ValueError:
            c.bt_port = 47800
        c.keybind = self.key_var.get().strip().upper()
        if self.always_var.get():
            c.keybind = ""
            c.hold_button = ""
        classes = self.classes_var.get().strip()
        c.target_classes = tuple(classes.split()) if classes else ()
        c.debug = self.debug_var.get()
        c.input_backend = "bt" if c.bt_host else "auto"
        return c

    def toggle(self):
        if self.worker and self.worker.is_alive():
            self.stop_flag.set()
            self.status_var.set("Stopping...")
            self.start_btn.config(text="START")
        else:
            self._apply()
            if not self.cfg.bt_host:
                messagebox.showwarning("ValAim", "Enter the phone IP first (shown in BtAimBridge).")
                return
            self.stop_flag.clear()
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()
            self.start_btn.config(text="STOP")
            self.status_var.set("Running...")

    def _run(self):
        try:
            from .main import run
            run(self.cfg, stop_flag=self.stop_flag, status=self.status_var)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, lambda: self.status_var.set(f"Error: {exc}"))

    def save(self):
        self._apply()
        save_config(self.cfg)
        self.status_var.set(f"Saved to {_config_path()}")

    def quit(self):
        self.stop_flag.set()
        self.save()
        self.root.after(200, self.root.destroy)

    def run(self):
        self.root.mainloop()


def main() -> None:
    cfg = load_config()
    ValAimGUI(cfg).run()


if __name__ == "__main__":
    main()
