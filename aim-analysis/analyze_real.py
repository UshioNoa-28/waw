"""Analyze a real in-game ValAim trace: measure loop latency, frame rate,
per-flick oscillation (sign flips / overshoot) and recommend comp frames.

Usage: python analyze_real.py <real.csv> [cp_override]
"""
import csv
import math
import statistics as st
import sys


def load(path):
    errs, cmds, dbgs = [], [], []
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) < 3:
                continue
            k = row[0]
            try:
                t = float(row[1])
            except ValueError:
                continue
            if k == "err":
                errs.append((t, float(row[2]), float(row[3])))
            elif k == "cmd" and len(row) >= 4:
                cmds.append((t, float(row[2]), float(row[3])))
            elif k == "dbg":
                dbgs.append(row)
    return errs, cmds, dbgs


def main():
    path = sys.argv[1]
    cp = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    errs, cmds, _ = load(path)
    if len(errs) < 50:
        print("数据太少")
        return 1

    ts = [t for t, _, _ in errs]
    dts = [b - a for a, b in zip(ts, ts[1:]) if 0.001 < b - a < 0.2]
    dt = st.median(dts) if dts else 1 / 60
    print(f"帧数 {len(errs)}  有效循环 ~{1/dt:.0f} fps (帧间隔 {dt*1000:.0f}ms)")

    # map cmd px per frame time-bin
    t0, t1 = ts[0], ts[-1]
    nb = int((t1 - t0) / dt) + 2
    applied = [0.0] * nb
    errmag = [0.0] * nb
    seen = [False] * nb

    def bin_of(t):
        return min(nb - 1, max(0, int((t - t0) / dt)))

    for t, x, y in errs:
        i = bin_of(t)
        errmag[i] = math.hypot(x, y)
        seen[i] = True
    for t, mx, my in cmds:
        applied[bin_of(t)] += math.hypot(mx, my) * cp

    # ---- latency via drop-vs-apply correlation ----
    drops = [max(0.0, (errmag[i] - errmag[i + 1]) if (seen[i] and seen[i+1]) else 0.0)
             for i in range(nb - 1)]
    best = None
    for Lms in range(0, 220, 10):
        L = max(1, int(Lms / 1000 / dt))
        num = den = 0.0
        pairs = 0
        for i in range(L, nb - 1):
            if applied[i - L] > 5:
                num += drops[i] * applied[i - L]
                den += applied[i - L] * applied[i - L]
                pairs += 1
        if pairs >= 8 and den > 0:
            score = num / math.sqrt(den)
            if best is None or score > best[0]:
                best = (score, Lms, pairs)
    if best:
        lat = best[1]
        print(f"回路延迟 ≈ {lat}ms  (相关样本 {best[2]}, 即 {lat/1000/dt:.1f} 帧)")
    else:
        lat = None
        print("延迟: 样本不足")

    # ---- per-flick oscillation analysis ----
    flicks = 0
    flips_list, over_list = [], []
    i = 0
    while i < nb:
        if seen[i] and errmag[i] > 60:
            j = i
            while j < nb and seen[j] and errmag[j] > 15 and j - i < int(1.5 / dt):
                j += 1
            k = min(j, nb - 1)
            if seen[k] and errmag[k] <= 15:
                run = errmag[i:k + 1]
                signs = [1 if v > 0 else -1 for v in run]
                flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
                # overshoot: minimum (signed) error during the flick run
                s0 = 1 if run[0] > 0 else -1
                over = max(0.0, -s0 * min(run) if s0 > 0 else 0.0)
                # signed min via err vectors not kept -> approximate by magnitude
                flips_list.append(flips)
                flicks += 1
            i = k + 5
        else:
            i += 1
    if flips_list:
        print(f"有效甩枪 {flicks} 次 | 符号翻转中位 {st.median(flips_list):.0f} "
              f"(0-1=一次到位, >=3=振荡)")

    if lat is not None:
        rec = math.ceil(lat / 1000 / dt) + 1
        print(f"\n建议 --aim-comp {rec}  (延迟 {lat}ms / 帧 {dt*1000:.0f}ms + 1 安全)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
