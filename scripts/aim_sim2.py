"""Closed-loop simulator driven by the IDENTIFIED device model (device_model.json).

Differs from aim_sim.py: latency is an empirical distribution (per command),
noise is from idle-fit (MAD sigma + spike rate/size), loop runs at 30 fps.
Grid-searches the continuous control law (move_fraction / comp window /
smoothing / deadzone / arrive-latch) against worst-case behaviour.
"""
import argparse
import json
import math
import random
import sys
import types

sys.path.insert(0, ".")
import valaim.aim_engine as _AE  # noqa: E402
from valaim.aim_engine import AimEngine, AimParams  # noqa: E402

_CLOCK = {"t": 0.0}
_AE.time = types.SimpleNamespace(monotonic=lambda: _CLOCK["t"])

CFG = json.load(open("device_model.json"))
DT = 1.0 / CFG.get("loop_fps", 30)
CP = CFG["cp_px_per_count"]
DELAYS = CFG["actuation"]["delay_hist"]
NOISE = CFG["noise"]
SPEED = CFG["target"]


class World:
    def __init__(self, seed):
        self.rng = random.Random(seed)

    def delay(self):
        return self.rng.choice(DELAYS)

    def observe(self, err):
        d = self.rng.random()
        if d < NOISE["spike_rate"]:
            n = self.rng.choice([-1, 1]) * NOISE["spike_p50"] * self.rng.uniform(0.7, 1.3)
        else:
            n = self.rng.gauss(0, max(0.8, NOISE["frame_disp_p90"] * 0.18))
        return err + n


def simulate(alpha, cf, sm, dz, arrive, resume, seed, err0=150.0, move_v=0.0,
             frames=180):
    _CLOCK["t"] = 0.0
    w = World(seed)
    p = AimParams(move_fraction=alpha, max_step=250, smoothing=sm,
                  counts_per_px=1.0 / CP, deadzone=dz, min_speed=0.0,
                  comp_frames=cf, comp_weight=1.0, arrive_px=arrive,
                  resume_px=resume, burst=False)
    e = AimEngine(p)
    e.on_new_lock()
    cross = 0.0
    due = []  # (finish_time, px)
    settle = None
    over = 0.0
    hold = 0.0
    track = []
    prev_sign = None
    tgt = err0
    for f in range(frames):
        t = f * DT
        _CLOCK["t"] = t
        tgt = err0 + move_v * t
        err_true = tgt - cross
        ex = w.observe(err_true)
        cmd = e.step(ex, 0.0)[0]
        if cmd:
            due.append((t + w.delay() + DT, cmd * CP))
        landed = 0.0
        keep = []
        for ft, v in due:
            if ft <= t:
                landed += v
            else:
                keep.append((ft, v))
        due = keep
        cross += landed
        if prev_sign is not None and err_true * prev_sign < 0 and abs(err_true) < 60:
            over = max(over, abs(err_true))
        prev_sign = err_true
        if settle is None and f > 2 and abs(err_true) <= 8 and move_v == 0:
            settle = t
        if settle is not None and t > settle + 0.4 and move_v == 0:
            hold += abs(landed)
        if move_v and f > frames * 0.6:
            track.append(abs(err_true))
    tr = max(track) if track else 0.0
    return dict(settle=settle if settle is not None else 6.0,
                over=over, hold=hold, track=tr)


def worst_case(alpha, cf, sm, dz, arrive, resume, seeds, moves):
    """Aggregate over seeds & two motion regimes; static worst = p90 delay heavy."""
    S = dict(settle=0.0, over=0.0, hold=0.0, track=0.0)
    for sd in seeds:
        r = simulate(alpha, cf, sm, dz, arrive, resume, sd, move_v=0.0)
        S["settle"] += r["settle"]
        S["over"] += r["over"]
        S["hold"] += r["hold"]
        for mv in moves:
            r2 = simulate(alpha, cf, sm, dz, arrive, resume, sd + 100, move_v=mv)
            S["track"] += r2["track"]
    n = len(seeds)
    return {k: v / n for k, v in S.items()}


def score(r):
    return (r["settle"] * 1000 * 0.06 + r["over"] * 1.5
            + r["hold"] * 0.35 + r["track"] * 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", action="store_true")
    a = ap.parse_args()
    moves = [0.0]  # tracking measured separately
    print(f"model: Dp50={CFG['actuation']['p50']*1000:.0f}ms Dp90={CFG['actuation']['p90']*1000:.0f}ms "
          f"spike={NOISE['spike_rate']:.1%} loop={1/DT:.0f}fps")
    if a.grid:
        best = None
        for alpha in (0.15, 0.25, 0.4, 0.55, 0.7, 0.9, 1.0):
            for cf in (2, 4, 6, 8, 10, 12):          # window = cf/60 s
                for sm in (0.55,):
                    for dz in (8.0, 11.0):
                        for arr in (0.0, 8.0):
                            if arr and (dz > arr):
                                continue
                            for rv in (32.0,):
                                w = worst_case(alpha, cf, sm, dz, arr, rv, (11, 12, 13), moves)
                                sc = score(w)
                                if best is None or sc < best[0]:
                                    best = (sc, alpha, cf, sm, dz, arr, rv, w)
        sc, alpha, cf, sm, dz, arr, rv, w = best
        print(f"\n最优连续律: alpha={alpha} comp={cf}帧({cf/60*1000:.0f}ms) sm={sm} "
              f"dz={dz} arrive={arr} resume={rv}")
        print(f"  settle {w['settle']*1000:.0f}ms  过冲 {w['over']:.0f}px  "
              f"钉住晃 {w['hold']:.0f}px/3s  跟踪差 {w['track']:.0f}px  score {sc:.0f}")
    # motion tracking check on chosen law (p50 speed target)
    for alpha in (0.25, 0.4, 0.7, 1.0):
        w = worst_case(alpha, 10, 0.55, 8.0, 8.0, 32.0, (21, 22, 23), [SPEED["screen_speed_p50"] / 3.0])
        print(f"alpha={alpha:<4} 中速移动靶(372/3px/s): 跟踪 {w['track']:.0f}px")


if __name__ == "__main__":
    main()
