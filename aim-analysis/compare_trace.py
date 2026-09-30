"""Compare ValAim's own movement trace (aim_trace.csv) against human
signature stats (features.json from extract_human.py).

aim_trace.csv rows: kind in {meta, err, cmd}, t (s), a (x or cp), b (y).
The pointer trajectory is reconstructed by integrating cmd events at cp.
"""
import csv, json, math, os, statistics as st, sys

def load_trace(path):
    meta_cp = 1.34
    cmds = []
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) != 4:
                continue
            kind = row[0]
            try:
                t, a, b = float(row[1]), float(row[2]), float(row[3])
            except ValueError:
                continue
            if kind == "meta":
                meta_cp = a if a > 0.01 else 1.34
            elif kind == "cmd":
                cmds.append((t, a / meta_cp, b / meta_cp))
    return cmds

def segment(cmds, gap=0.30):
    bursts = []
    cur = []
    for ev in cmds:
        if cur and ev[0] - cur[-1][0] > gap:
            bursts.append(cur)
            cur = []
        cur.append(ev)
    if cur:
        bursts.append(cur)
    return bursts

def feats(bursts):
    out = []
    for b in bursts:
        t0 = b[0][0]
        ts = [e[0] - t0 for e in b]
        xs, ys = [e[1] for e in b], [e[2] for e in b]
        cx, cy = 0.0, 0.0
        pts = [(t0, 0.0, 0.0)]
        for e in b:
            cx += e[1]; cy += e[2]
            pts.append((e[0], cx, cy))
        dist = math.hypot(cx, cy)
        dur = ts[-1] - ts[0] if len(ts) > 1 else 0
        if dist < 30 or dur < 0.05:
            continue
        path = sum(math.hypot(pts[i + 1][1] - pts[i][1], pts[i + 1][2] - pts[i][2])
                   for i in range(len(pts) - 1))
        vs = []
        for i in range(1, len(pts)):
            dt = pts[i][0] - pts[i - 1][0]
            if dt <= 0.0015:
                continue
            # store px/ms to match human trials.json units
            vs.append(math.hypot(pts[i][1] - pts[i - 1][1], pts[i][2] - pts[i - 1][2]) / dt / 1000.0)
        peak = max(vs) if vs else 0
        rise = vs.index(peak) * dur / max(1e-9, len(vs) * dur) if vs else 0
        tail = vs[int(len(vs) * 0.7):]
        rev = sum(1 for i in range(2, len(tail))
                  if abs(tail[i] - tail[i - 1]) > 2 and abs(tail[i - 1] - tail[i - 2]) > 2
                  and (tail[i] - tail[i - 1]) * (tail[i - 1] - tail[i - 2]) < 0)
        dwell = sum(1 for v in tail if v < 0.3) / max(1, len(tail))
        out.append(dict(dist=dist, dur=dur * 1000, peak=peak, rise=rise,
                        path_ratio=path / dist, rev=rev, dwell=dwell))
    return out

def med(xs):
    return st.median(xs) if xs else float("nan")

def main():
    trace = sys.argv[1] if len(sys.argv) > 1 else "aim_trace.csv"
    if not os.path.exists(trace):
        print("找不到", trace)
        return 1
    F = feats(segment(load_trace(trace)))
    print(f"machine 甩枪数: {len(F)}")
    hf = None
    tj = "data/session_human_20260930/aimlab_trials.json"
    if os.path.exists(tj):
        tr = json.load(open(tj)).get("human", [])
        hf = [dict(dist=t["dist0"], dur=t["dur"], peak=t["peak"],
                  path_ratio=t["pathRatio"], rev=t["rev"], dwell=float("nan")) for t in tr]
    rows = [("距离px", "dist", "{:.0f}"), ("时长ms", "dur", "{:.0f}"),
            ("峰速px/s", "peak", "{:.0f}"), ("路径比", "path_ratio", "{:.2f}"),
            ("末端修正", "rev", "{:.0f}"), ("末端低速占比", "dwell", "{:.2f}")]
    if hf:
        print(f"{'指标':10s} {'human':>10s} {'machine':>10s}")
        hm = {"dist": med([f["dist"] for f in hf]), "dur": med([f["dur"] for f in hf]),
              "peak": med([f["peak"] * 1000 for f in hf]),
              "path_ratio": med([f["path_ratio"] for f in hf]),
              "rev": med([f["rev"] for f in hf]), "dwell": med([f["dwell"] for f in hf])}
        for name, k, fm in rows:
            mv = k if k != "peak" else "peak"
            mm = med([f[mv] * (1000 if k == "peak" else 1) for f in F]) if F else float("nan")
            print(f"{name:10s} {fm.format(hm[k]):>10s} {fm.format(mm):>10s}")
    elif F:
        for name, k, fm in rows:
            v = med([f[k] * (1000 if k == "peak" else 1) for f in F])
            print(f"{name:10s} {fm.format(v):>10s}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
