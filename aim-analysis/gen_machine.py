"""Synthesize machine-aim trajectories from the aim-loop math in
valaim/main.py (run() lines 144-178):

    dx = target - cursor
    if dist > min_move:  move = clamp(int(dx * move_fraction), max_step)

No game, no model, no cheat binary -- just the arithmetic the cheat
performs, replayed against a static target. Output uses the same
session layout as collect_human.py (samples.csv + events.json), so the
analysis side can segment both identically.

Usage:
    python gen_machine.py [--trials 12] [--fps 125] [--out data]
"""

import argparse
import json
import math
import random
import time
from datetime import datetime
from pathlib import Path

MOVE_FRACTION = 0.65   # config.py default
MAX_STEP = 120         # config.py default
MIN_MOVE = 2           # config.py default
TARGET_R = 18          # match the human trial dot size
MAX_FRAMES = 2000

MIN_DIST = 120
MAX_DIST = 380


def simulate_flick(start: tuple[float, float], target: tuple[float, float],
                   dt: float) -> list[tuple[float, float]]:
    """Replay the per-frame movement rule. Returns pointer positions,
    one per frame, from first move to convergence."""
    x, y = start
    positions = [(0.0, x, y)]  # (t, x, y)
    t = 0.0
    for _ in range(MAX_FRAMES):
        dx = target[0] - x
        dy = target[1] - y
        if math.hypot(dx, dy) <= MIN_MOVE:
            break
        mx = max(-MAX_STEP, min(MAX_STEP, int(dx * MOVE_FRACTION)))
        my = max(-MAX_STEP, min(MAX_STEP, int(dy * MOVE_FRACTION)))
        x += mx
        y += my
        t += dt
        if (x, y) != (positions[-1][1], positions[-1][2]):
            positions.append((t, x, y))
    return positions


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=12)
    p.add_argument("--fps", type=float, default=125.0,
                   help="emulated loop rate; the real one is set by inference latency")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--out", default=str(Path(__file__).parent / "data"))
    args = p.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
    dt = 1.0 / args.fps
    samples: list[tuple[float, int, int]] = []
    events: list[dict] = []

    t_cursor = 0.0
    last_end = (0.0, 0.0)
    for trial in range(1, args.trials + 1):
        ang = random.uniform(0, 2 * math.pi)
        dist = random.uniform(MIN_DIST, MAX_DIST)
        target = (last_end[0] + dist * math.cos(ang),
                  last_end[1] + dist * math.sin(ang))
        events.append({"type": "spawn", "trial": trial,
                       "t_ms": t_cursor * 1000, "x": target[0], "y": target[1]})

        flick = simulate_flick(last_end, target, dt)
        for i, (t, x, y) in enumerate(flick):
            if i > 0:
                samples.append((t_cursor + t, x, y))
        t_click = t_cursor + flick[-1][0] + dt
        events.append({"type": "click", "trial": trial,
                       "t_ms": t_click * 1000, "x": target[0], "y": target[1],
                       "hit": True})
        print(f"trial {trial}: distance {dist:.0f} px, converges in "
              f"{flick[-1][0] * 1000:.0f} ms, {len(flick)} moves")
        t_cursor = t_click + 0.5  # 500 ms between trials
        last_end = target

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sess = Path(args.out) / f"session_machine_{stamp}"
    sess.mkdir(parents=True, exist_ok=True)
    t0 = samples[0][0]
    with open(sess / "samples.csv", "w") as f:
        f.write("t_ms,x,y\n")
        for t, x, y in samples:
            f.write(f"{(t - t0) * 1000:.3f},{x:.0f},{y:.0f}\n")
    with open(sess / "events.json", "w") as f:
        json.dump({"created": stamp, "n_trials": args.trials,
                   "generator": f"move_fraction={MOVE_FRACTION} max_step={MAX_STEP} "
                                f"min_move={MIN_MOVE} fps={args.fps}",
                   "events": events}, f, indent=2)
    print(f"Saved {len(samples)} samples, {args.trials} trials -> {sess}")


if __name__ == "__main__":
    main()
