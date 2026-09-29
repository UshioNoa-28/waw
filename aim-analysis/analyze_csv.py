"""Analyze an aimlab_samples.csv export: segment human trials, group machine
trials, and compute the same discriminative metrics as aim_lab.html finishSeg()."""
import math
import sys


def load(path):
    human, machine = [], []
    with open(path) as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) != 4 or parts[0] not in ("human", "machine"):
                continue
            try:
                t, x, y = float(parts[1]), float(parts[2]), float(parts[3])
            except ValueError:
                continue
            who = parts[0]
            (human if who == "human" else machine).append((t, x, y))
    return human, machine


def exp_fit_r2(pts):
    if len(pts) < 4:
        return float("nan")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return float("nan")
    b = sxy / sxx
    a = my - b * mx
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    return 1.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot


def metrics(samples):
    """samples: list of (t, x, y) for one flick, first=cursor at spawn, last=hit."""
    x0, y0 = samples[0][1], samples[0][2]
    tx, ty = samples[-1][1], samples[-1][2]
    dist0 = math.hypot(tx - x0, ty - y0)
    dur = samples[-1][0] - samples[0][0]
    path = sum(math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(samples, samples[1:]))
    rev = 0
    prev = None
    for a, b in zip(samples, samples[1:]):
        dx, dy = b[1] - a[1], b[2] - a[2]
        if dx == 0 and dy == 0:
            continue
        dot = None if prev is None else prev[0] * dx + prev[1] * dy
        if dot is not None and dot < 0:
            rev += 1
        prev = (dx, dy)
    intervals = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
    mean = sum(intervals) / len(intervals) if intervals else 0.0
    jit = math.sqrt(sum((v - mean) ** 2 for v in intervals) / len(intervals)) if intervals else 0.0
    peak = max((math.hypot(b[1] - a[1], b[2] - a[2]) / (b[0] - a[0]) for a, b in zip(samples, samples[1:]) if b[0] > a[0]), default=0.0)
    decay = []
    for t, x, y in samples:
        d = math.hypot(tx - x, ty - y)
        if d > 0.5:
            decay.append((t - samples[0][0], math.log(d)))
    r2 = exp_fit_r2(decay)
    return {
        "n": len(samples), "dist0": dist0, "dur": dur,
        "path_ratio": path / dist0 if dist0 else float("nan"),
        "rev": rev, "jit": jit, "peak": peak, "r2": r2,
    }


def segment_human(samples, move_thresh=25.0, idle_gap=400.0):
    """Split into trials: each trial = movement burst ending in a rest point
    (the click). Trials separated by >=idle_gap ms of stillness."""
    trials = []
    cur = []
    for i, (t, x, y) in enumerate(samples):
        cur.append((t, x, y))
        nxt = samples[i + 1] if i + 1 < len(samples) else None
        still = nxt is None or (math.hypot(nxt[1] - x, nxt[2] - y) < 2.0)
        long_pause = nxt is not None and (nxt[0] - t) >= idle_gap
        if still or long_pause:
            if len(cur) >= 4:
                moved = max(math.hypot(s[1] - cur[0][1], s[2] - cur[0][2]) for s in cur)
                if moved > move_thresh:
                    trials.append(cur)
            cur = []
    return trials


def group_machine(samples):
    trials = []
    cur = []
    for s in samples:
        if s[0] == 0.0 and cur:
            trials.append(cur)
            cur = []
        cur.append(s)
    if cur:
        trials.append(cur)
    return trials


def report(name, trials):
    print(f"\n=== {name}: {len(trials)} trials ===")
    print(f"{'#':>2} {'n':>4} {'dist':>7} {'dur(ms)':>8} {'path/直':>7} {'rev':>3} {'jitσ(ms)':>8} {'peak(px/ms)':>11} {'R²(log d)':>9}")
    agg = []
    for i, tr in enumerate(trials):
        m = metrics(tr)
        agg.append(m)
        r2s = "n/a" if math.isnan(m["r2"]) else f"{m['r2']:.3f}"
        print(f"{i + 1:>2} {m['n']:>4} {m['dist0']:>7.1f} {m['dur']:>8.1f} {m['path_ratio']:>7.2f} {m['rev']:>3} {m['jit']:>8.2f} {m['peak']:>11.2f} {r2s:>9}")
    if agg:
        mean = lambda k: sum(a[k] for a in agg) / len(agg)
        print(f"AVG dur={mean('dur'):.1f}ms  jitσ={mean('jit'):.2f}ms  peak={mean('peak'):.2f}px/ms  R²={sum(a['r2'] for a in agg if not math.isnan(a['r2'])) / max(1, sum(1 for a in agg if not math.isnan(a['r2']))):.3f}  path={mean('path_ratio'):.2f}  rev={mean('rev'):.2f}")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/pasted_export.csv"
    human, machine = load(path)
    print(f"loaded {len(human)} human samples, {len(machine)} machine samples")
    report("HUMAN (you)", segment_human(human))
    report("MACHINE (aim-loop sim)", group_machine(machine))


if __name__ == "__main__":
    main()
