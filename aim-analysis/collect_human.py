"""Collect human mouse-flick telemetry for aim-signature analysis.

Click targets as fast as you can while the script records the pointer
trajectory at high frequency. Output is one CSV of samples plus a JSON
session manifest (trial boundaries), used later to distinguish human
movement from algorithmic (fixed-fraction convergence) movement.

Usage (Windows or any desktop Python 3.10+; only stdlib + tkinter):

    python collect_human.py [--trials 12]

Space: start next trial. Esc or q: finish and save.
"""

import argparse
import csv
import json
import math
import random
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

POLL_MS = 1          # pointer sampling interval
TAIL_S = 0.15        # keep sampling this long after the click
MARGIN = 40          # spawn keep-away from window edges
MIN_DIST = 120       # min distance (px) from previous click point
MAX_DIST = 380       # max distance (px) from previous click point


class Collector:
    W = 900
    H = 640

    def __init__(self, root: tk.Tk, n_trials: int, out_dir: Path):
        self.root = root
        self.n_trials = n_trials
        self.out_dir = out_dir

        self.samples: list[tuple[float, float, float]] = []  # (t, x, y)
        self.events: list[dict] = []
        self.trial_no = 0
        self.active = False
        self.done = False

        self.canvas = tk.Canvas(root, width=self.W, height=self.H, bg="#101014",
                                highlightthickness=0)
        self.canvas.pack()
        self.status = self.canvas.create_text(20, 20, anchor="w", fill="#9aa",
                                              font=("Consolas", 12))
        self.target_id = None

        root.bind("<space>", self.start_trial)
        root.bind("<q>", lambda e: self.finish())
        root.bind("<Escape>", lambda e: self.finish())
        self.canvas.bind("<Button-1>", self.on_click)

        root.title(f"human aim collector - 0/{n_trials} trials")
        self.set_status("Press SPACE to start trial 1")

        self.last_end_local = (self.W / 2, self.H / 2)

        self._poll_job = None
        self._tail_until = None

    def set_status(self, text: str) -> None:
        self.canvas.itemconfig(self.status, text=text)

    def start_trial(self, _event=None) -> None:
        if self.done or self.active or self.trial_no >= self.n_trials:
            return
        self.trial_no += 1
        self.active = True
        self.t0 = time.perf_counter()

        # pick a spawn point in canvas-local coords: random bearing,
        # MIN_DIST..MAX_DIST from the previous click
        for _ in range(200):
            ang = random.uniform(0, 2 * math.pi)
            dist = random.uniform(MIN_DIST, MAX_DIST)
            tx = self.last_end_local[0] + dist * math.cos(ang)
            ty = self.last_end_local[1] + dist * math.sin(ang)
            if MARGIN < tx < self.W - MARGIN and MARGIN < ty < self.H - MARGIN:
                break
        else:  # last click near an edge; fall back to any legal spot
            tx = random.uniform(MARGIN, self.W - MARGIN)
            ty = random.uniform(MARGIN, self.H - MARGIN)

        self.target_local = (tx, ty)
        # screen coords for the event log, so samples and targets share a frame
        sx = tx + self.canvas.winfo_rootx()
        sy = ty + self.canvas.winfo_rooty()
        self.target_screen = (sx, sy)
        r = 18
        self.target_id = self.canvas.create_oval(tx - r, ty - r, tx + r, ty + r,
                                                 fill="#e34040", outline="white")
        self.events.append({"type": "spawn", "trial": self.trial_no,
                            "t": self.t0, "x": sx, "y": sy})
        self.root.title(f"human aim collector - trial {self.trial_no}/{self.n_trials}")
        self.set_status(f"Trial {self.trial_no}: flick and click the red dot")
        self._poll()

    def _poll(self) -> None:
        """Sample the pointer while a trial is running."""
        if not self.active and not self._tail_pending():
            return
        t = time.perf_counter()
        x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        if not self.samples or (x, y) != (self.samples[-1][1], self.samples[-1][2]):
            self.samples.append((t, x, y))
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
        self._poll_job = self.root.after(POLL_MS, self._poll)

    def _tail_pending(self) -> bool:
        return self._tail_until is not None and time.perf_counter() < self._tail_until

    def on_click(self, event) -> None:
        if self.done or not self.active:
            return
        # hit test in canvas-local coords; log in screen coords (same frame
        # as the winfo_pointerx/y samples)
        dx, dy = event.x - self.target_local[0], event.y - self.target_local[1]
        hit = math.hypot(dx, dy) <= 18
        t_end = time.perf_counter()
        sx, sy = event.x_root, event.y_root

        self.events.append({"type": "click", "trial": self.trial_no, "t": t_end,
                            "x": sx, "y": sy, "hit": hit})
        self.last_end_local = (event.x, event.y)
        if self.target_id:
            self.canvas.delete(self.target_id)
            self.target_id = None

        if not hit:  # missed click does not end the trial
            self.set_status("Missed - keep going")
            return

        self.active = False
        self._tail_until = t_end + TAIL_S
        flick_ms = (t_end - self.t0) * 1000
        err = math.hypot(dx, dy)
        self.set_status(f"Trial {self.trial_no}: {flick_ms:.0f} ms  "
                        f"(space for next)")
        print(f"trial {self.trial_no}: flick {flick_ms:.0f} ms, error {err:.1f} px")
        self.root.after(int(TAIL_S * 1000) + 10, self._poll)
        if self.trial_no >= self.n_trials:
            self.finish()

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        self.active = False
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
        if self.trial_no == 0:
            print("No trials recorded, nothing saved.")
            self.root.destroy()
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sess = self.out_dir / f"session_{stamp}"
        sess.mkdir(parents=True, exist_ok=True)
        t0 = self.samples[0][0] if self.samples else 0.0

        with open(sess / "samples.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_ms", "x", "y"])
            for t, x, y in self.samples:
                w.writerow([f"{(t - t0) * 1000:.3f}", x, y])

        with open(sess / "events.json", "w") as f:
            json.dump({
                "created": stamp,
                "n_trials": self.trial_no,
                "poll_ms": POLL_MS,
                "t0_absolute": t0,
                "events": [{**e, "t_ms": (e["t"] - t0) * 1000}
                           for e in self.events],
            }, f, indent=2)

        print(f"Saved {len(self.samples)} samples, {self.trial_no} trials -> {sess}")
        self.set_status(f"Done. Saved to {sess}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=12)
    p.add_argument("--out", default=str(Path(__file__).parent / "data"))
    args = p.parse_args()

    root = tk.Tk()
    root.update_idletasks()
    # center-ish placement so targets stay on screen
    root.geometry("+150+80")
    Collector(root, args.trials, Path(args.out))
    root.mainloop()


if __name__ == "__main__":
    main()
