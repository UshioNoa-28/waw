"""Closed-loop aim simulator calibrated to MEASURED in-game data.

  cp:        1 count = 0.75 screen px      (trace fit: sens 0.6 @ 2560 wide)
  jitter:    head-box noise gauss(4px) + 8% spikes of 15-25px   (trace p50/p90)
  latency:   4 frames (~67ms) queue         (capture+DML+BT+render)
  head box:  ~26x29 px                     (real screenshot inference result)

Scenarios / metrics:
  static150  : settle ms, overshoot px, hold wobble px over 5s
  switch     : first command after new lock must not point away (kick)
  move150    : tracking error px at 2.5s while following 150 px/s target

Usage: python scripts/aim_sim.py [--tune]
"""
import argparse
import math
import random
import sys
import types

sys.path.insert(0, ".")
import valaim.aim_engine as _AE  # noqa: E402
from valaim.aim_engine import AimEngine, AimParams  # noqa: E402

# The engine reads time.monotonic() for the in-flight window; drive it with a
# simulated 60fps clock so frame semantics match the real program.
_CLOCK = {"t": 0.0}
_AE.time = types.SimpleNamespace(monotonic=lambda: _CLOCK["t"])

CP = 0.75          # px per count
LAT = 4            # frames of actuation latency
LEAD_S = 0.08      # caller lead (seconds) as wired in main()


class World:
    def __init__(self, seed):
        self.rng = random.Random(seed)

    def observe(self, err):
        n = self.rng.gauss(0, 4.0)
        if self.rng.random() < 0.08:
            n = self.rng.choice([-1, 1]) * self.rng.uniform(15, 25)
        m = self.rng.gauss(0, 4.0)
        if self.rng.random() < 0.08:
            m = self.rng.choice([-1, 1]) * self.rng.uniform(15, 25)
        return err + n, m


def simulate(p, seed, err0, move=0.0, switch_at=None, t_frames=360):
    _CLOCK["t"] = 0.0
    w = World(seed)
    e = AimEngine(p)
    e.on_new_lock()
    cross = 0.0
    pipe = [0.0] * LAT
    settle = None
    overshoot = 0.0
    first_cmd = None
    first_after_switch = None
    hold = 0.0
    track = None
    prev_tgt = None
    for f in range(t_frames):
        _CLOCK["t"] += 1.0 / 60.0
        t = f / 60.0
        tgt = err0 + move * t
        if switch_at is not None and f == switch_at:
            tgt = err0 - 140.0 - move * t
            e.on_new_lock()
        err_true = tgt - cross
        ex, ey = w.observe(err_true)
        cmd = e.step(ex, ey, move * LEAD_S, 0.0)[0]
        if first_cmd is None and cmd:
            first_cmd = cmd
        if switch_at is not None and f > switch_at and first_after_switch is None and cmd:
            first_after_switch = cmd
        out = pipe.pop(0)
        pipe.append(cmd * CP)
        cross += out
        if prev_tgt is not None and err_true * (prev_tgt - cross) < -1 and abs(err_true) < 40:
            overshoot = max(overshoot, abs(err_true))
        prev_tgt = err_true
        if settle is None and f > 3 and abs(err_true) <= 8 and move == 0 and switch_at is None:
            settle = t
        if settle is not None and t > settle + 0.5:
            hold += abs(out)
        if move and f == int(2.5 * 60):
            track = abs(err_true)
    kick = False
    if switch_at is not None and first_after_switch is not None:
        kick = first_after_switch > 0
    return dict(settle=settle, overshoot=overshoot, hold=hold, kick=kick,
                track=track, first=first_cmd)


BASE = dict(move_fraction=0.7, max_step=60, smoothing=0.55, counts_per_px=1.0 / CP,
            deadzone=11.0, min_speed=0.0, comp_frames=6, comp_weight=1.0, arrive_px=0.0)


def P(**kw):
    d = dict(BASE)
    d.update(kw)
    return AimParams(**d)


def run_current():
    print("=== 当前默认 ===")
    r1 = simulate(P(), 7, 150)
    r2 = simulate(P(), 21, 30, switch_at=90)
    r3 = simulate(P(), 33, 40, move=150.0)
    print(f"static150 : settle {r1['settle'] * 1000 if r1['settle'] else -1:.0f}ms  "
          f"过冲 {r1['overshoot']:.0f}px  保持晃动 {r1['hold']:.0f}px/5s")
    print(f"switch    : kick={r2['kick']}  过冲 {r2['overshoot']:.0f}px")
    print(f"move150px/s: 跟踪误差 {r3['track']:.0f}px")
    return r1, r2, r3


def score(r1, r2, r3):
    s = (r1["settle"] or 6) * 40 + r1["overshoot"] * 2 + r1["hold"] * 0.25
    s += (500 if r2["kick"] else 0) + r2["overshoot"] * 2
    s += (r3["track"] or 99) * 1.5
    return s


def tune():
    print("\n=== 扫参(真实标定噪声)===")
    best = None
    for mf in (0.35, 0.45, 0.55, 0.7):
        for cf in (2, 3, 4, 5, 6):
            for sm in (0.4, 0.55, 0.7):
                for dz in (5.0, 8.0, 11.0):
                    p = P(move_fraction=mf, comp_frames=cf, smoothing=sm, deadzone=dz)
                    r1 = simulate(p, 7, 150)
                    r2 = simulate(p, 21, 30, switch_at=90)
                    r3 = simulate(p, 33, 40, move=150.0)
                    sc = score(r1, r2, r3)
                    if best is None or sc < best[0]:
                        best = (sc, mf, cf, sm, dz, r1, r2, r3)
    _, mf, cf, sm, dz, r1, r2, r3 = best
    print(f"最优: move_fraction={mf} comp={cf} smoothing={sm} deadzone={dz}  (score {best[0]:.0f})")
    print(f"  static150 : settle {(r1['settle'] or 0) * 1000:.0f}ms 过冲 {r1['overshoot']:.0f}px 晃 {r1['hold']:.0f}px/5s")
    print(f"  switch    : kick={r2['kick']} 过冲 {r2['overshoot']:.0f}px")
    print(f"  move150   : 跟踪误差 {r3['track']:.0f}px")
    r1 = simulate(P(), 7, 150)
    r2 = simulate(P(), 21, 30, switch_at=90)
    r3 = simulate(P(), 33, 40, move=150.0)
    print(f"当前默认 score {score(r1, r2, r3):.0f}  vs 最优 {best[0]:.0f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", action="store_true")
    a = ap.parse_args()
    run_current()
    if a.tune:
        tune()
