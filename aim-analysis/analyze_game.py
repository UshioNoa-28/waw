"""Analyze an in-game aim_trace.csv (ValAim --log-aim).

Answers with real data:
  - detection jitter: how much the Head/Body box center wobbles per frame
    while the crosshair sits on a mostly-still bot  -> sets the deadzone
  - command dither: how much the engine still emits while |error| is small
  - target speed distribution
Usage: python analyze_game.py <aim_trace.csv>
"""
import csv, math, statistics as st, sys
from collections import defaultdict

def load(path):
    dets, errs, cmds, meta = [], [], [], {"cp": 1.34}
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if not row:
                continue
            k = row[0]
            try:
                if k == "meta":
                    meta["cp"] = float(row[2]) or meta["cp"]
                elif k == "det" and len(row) >= 8:
                    dets.append((float(row[1]), float(row[2]), float(row[3]), row[7], float(row[6])))
                elif k == "err":
                    errs.append((float(row[1]), float(row[2]), float(row[3])))
                elif k == "cmd":
                    cmds.append((float(row[1]), float(row[2]), float(row[3])))
            except (ValueError, IndexError):
                pass
    return dets, errs, cmds, meta

def per_frame_groups(dets):
    g = defaultdict(list)
    for t, x, y, name, conf in dets:
        g[round(t, 3)].append((x, y, name, conf))
    return g

def main(path):
    dets, errs, cmds, meta = load(path)
    if not dets:
        print("trace 里没有 det 行(用最新包 --log-aim 重采)")
        return 1
    groups = per_frame_groups(dets)
    ts = sorted(groups)
    heads = {t: [d for d in groups[t] if "head" in d[2].lower()] for t in ts}

    # --- detection jitter: head center delta between consecutive frames
    jumps_h, jumps_b = [], []
    track = None
    prev_t = None
    for t in ts:
        for x, y, name, conf in heads[t] or groups[t]:
            if track is not None and t - prev_t < 0.1:
                d = math.hypot(x - track[0], y - track[1])
                (jumps_h if "head" in name.lower() else jumps_b).append(d)
            track, prev_t = (x, y), t
    def pct(xs, p):
        xs = sorted(xs)
        return xs[int(p * (len(xs) - 1))] if xs else float("nan")
    print(f"det样本 {len(dets)}  帧 {len(ts)}")
    if jumps_h:
        print(f"Head框抖动 px/帧: med {pct(jumps_h,.5):.1f} p90 {pct(jumps_h,.9):.1f} p99 {pct(jumps_h,.99):.1f}")
    if jumps_b:
        print(f"Body框抖动 px/帧: med {pct(jumps_b,.5):.1f} p90 {pct(jumps_b,.9):.1f}")

    # --- error stats near target (latched region behaviour)
    small = [math.hypot(a, b) for _, a, b in errs]
    if small:
        near = [e for e in small if e <= 12]
        far = [e for e in small if e > 12]
        print(f"误差分布: <=12px 帧占比 {len(near)/len(small):.0%} | 其中 med {pct(near,.5) if near else float('nan'):.1f}px")
        _ = far

    # --- command dither: output while error small
    e_by_t = {t: (a, b) for t, a, b in errs}
    dither = 0.0
    n_small = 0
    for t, cx, cy in cmds:
        e = e_by_t.get(round(t, 4)) or e_by_t.get(t)
        if e is None:
            continue
        if math.hypot(*e) <= max(6, 2 * (pct(jumps_h, .9) if jumps_h else 6)):
            dither += abs(cx) / meta["cp"]
            n_small += 1
    print(f"小误差区仍发的指令量: {dither:.0f}px 共{n_small}帧 → 中位每帧 {dither/max(1,n_small):.2f}px")
    print()
    if jumps_h:
        rec = max(4.0, pct(jumps_h, .9))
        print(f"建议: --deadzone {rec:.0f} (≈90%帧抖动,低于它的误差全是噪声,别追)")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "aim_trace.csv"))
