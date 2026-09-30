"""Analyze in-game ValAim traces (aim_trace.csv). Two columns:
human (in-game) flicks reconstructed from detection-frame deltas,
machine flicks from actual emitted commands. Plus detection-jitter and
command-dither stats -> concrete deadzone recommendation.

Usage:
    python analyze_game.py <trace.csv> [more.csv...]

For clean human data record with:  ValAim.exe --cli --log-aim --no-aim --trace human.csv
Machine data:                      ValAim.exe --cli --log-aim --trace machine.csv
"""
import bisect
import csv
import math
import statistics as st
import sys

sys.path.insert(0, ".")
from compare_trace import feats, load_trace, segment  # noqa: E402


def pct(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))] if xs else float("nan")


def human_deltas(errs, cmds):
    cmd_ts = sorted(t for t, _, _ in cmds)

    def near_cmd(t):
        i = bisect.bisect_left(cmd_ts, t)
        return any(abs(cmd_ts[j] - t) < 0.25 for j in range(max(0, i - 1), min(len(cmd_ts), i + 2)))

    deltas = []
    prev = None
    for t, a, b in errs:
        if near_cmd(t):
            prev = None
            continue
        if prev is not None and t - prev[0] < 0.12:
            da, db = a - prev[1], b - prev[2]
            if abs(da) < 250 and abs(db) < 250:      # ignore target switches
                deltas.append((t, -da, -db))         # crosshair moved -d(error)
        prev = (t, a, b)
    return deltas


def load_raw(path):
    dets, errs, cmds, meta = [], [], [], {"cp": 1.34}
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            try:
                k = row[0]
                if k == "meta":
                    meta["cp"] = float(row[2]) or meta["cp"]
                elif k == "det" and len(row) >= 8:
                    dets.append((float(row[1]), float(row[2]), float(row[3]), row[7]))
                elif k == "err":
                    errs.append((float(row[1]), float(row[2]), float(row[3])))
                elif k == "cmd":
                    cmds.append((float(row[1]), float(row[2]), float(row[3])))
            except (ValueError, IndexError):
                pass
    return dets, errs, cmds, meta


def head_jitter(dets):
    per_t = {}
    for t, x, y, name in dets:
        if "head" in name.lower():
            per_t.setdefault(t, (x, y))
    ts = sorted(per_t)
    jumps = []
    for a, b in zip(ts, ts[1:]):
        if b - a < 0.05:
            xa, ya = per_t[a]
            xb, yb = per_t[b]
            jumps.append(math.hypot(xb - xa, yb - ya))
    return jumps


def med(xs):
    return st.median(xs) if xs else float("nan")


def main(paths):
    for path in paths:
        dets, errs, cmds, meta = load_raw(path)
        print(f"===== {path}: {len(errs)} err / {len(cmds)} cmd / {len(dets)} det =====")
        hj = head_jitter(dets)
        if hj:
            print(f"Head框抖动 px/帧: med {pct(hj,.5):.1f}  p90 {pct(hj,.9):.1f}")
        Fm = feats(segment(cmds)) if cmds else []
        Fh = feats(segment(human_deltas(errs, cmds)))
        hdr = f"{'指标':8s} {'人在图':>9s} {'机器':>9s}"
        print(hdr)
        for name, k, sc in (("距离px", "dist", 1), ("时长ms", "dur", 1),
                            ("峰速px/s", "peak", 1000.0), ("路径比", "path_ratio", 1),
                            ("末端修正", "rev", 1)):
            hv = med([f[k] * sc for f in Fh]) if Fh else float("nan")
            mv = med([f[k] * sc for f in Fm]) if Fm else float("nan")
            print(f"{name:8s} {hv:>9.1f} {mv:>9.1f}")
        if hj:
            print(f"\n建议 --deadzone {max(4.0, pct(hj, .9)):.0f}  (p90 抖动以下的误差不该追)")
        if not Fh:
            print("(此文件里没有无指令时段的轨迹:人的数据要 --no-aim 录)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["aim_trace.csv"]))
