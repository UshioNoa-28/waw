"""Human aim-signature extraction from aimlab exports.

Segments the raw pointer stream into flick bursts (on a resampled 4ms grid to
defeat duplicate timestamps), then extracts the features we need to design the
humanization profile: bell-shaped velocity, rise fraction, path curvature,
terminal corrections, inter-flick pauses, peak-vs-distance scaling.
"""
import csv, json, math, statistics as st

PATH = "data/session_human_20260930/aimlab_samples.csv"
STEP = 0.004  # s

def load(path):
    rows = []
    with open(path) as f:
        r = csv.reader(f); next(r)
        for who, t, x, y in r:
            try:
                rows.append((float(t) / 1000.0, float(x), float(y)))
            except ValueError:
                pass
    rows.sort(key=lambda p: p[0])
    return rows

def resample(pts, step):
    if len(pts) < 2:
        return []
    out = []
    t0, t1 = pts[0][0], pts[-1][0]
    i = 0
    t = t0
    while t <= t1:
        while i + 1 < len(pts) and pts[i + 1][0] < t:
            i += 1
        ta, xa, ya = pts[i]
        if i + 1 < len(pts):
            tb, xb, yb = pts[i + 1]
            f = 0.0 if tb == ta else min(1.0, max(0.0, (t - ta) / (tb - ta)))
            out.append((t, xa + (xb - xa) * f, ya + (yb - ya) * f))
        else:
            out.append((t, xa, ya))
        t += step
    return out

pts = resample(load(PATH), STEP)
V_START, V_END, T_END = 0.10, 0.05, 0.20   # px/ms

bursts, cur, last_move = [], None, None
for k in range(1, len(pts)):
    t, x, y = pts[k]
    dt = t - pts[k - 1][0]
    v = math.hypot(x - pts[k - 1][1], y - pts[k - 1][2]) / max(dt, 1e-6)
    if cur is None:
        if v > V_START:
            cur = {"t0": pts[k - 1][0], "samples": [pts[k - 1], pts[k]]}
            last_move = t
    else:
        cur["samples"].append(pts[k])
        if v > V_END:
            last_move = t
        elif t - last_move > T_END:
            cur["t1"] = last_move
            bursts.append(cur)
            cur = None
if cur:
    cur["t1"] = last_move
    bursts.append(cur)

feats = []
for b in bursts:
    S = b["samples"]
    if len(S) < 8:
        continue
    start, end = S[0][1:], S[-1][1:]
    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    dur = max(1e-4, b["t1"] - b["t0"])
    path = sum(math.hypot(S[i + 1][1] - S[i][1], S[i + 1][2] - S[i][2]) for i in range(len(S) - 1))
    vs = [math.hypot(S[i + 1][1] - S[i][1], S[i + 1][2] - S[i][2]) / STEP
          for i in range(len(S) - 1)]
    if not vs or dist < 30:
        continue
    peak = max(vs)
    rise = vs.index(peak) * STEP / dur
    tail = vs[int(len(vs) * 0.7):]
    rev = sum(1 for i in range(2, len(tail))
              if abs(tail[i] - tail[i - 1]) > 0.03 and abs(tail[i - 1] - tail[i - 2]) > 0.03
              and (tail[i] - tail[i - 1]) * (tail[i - 1] - tail[i - 2]) < 0)
    dwell = sum(1 for v in tail if v < 0.3) / max(1, len(tail))
    feats.append(dict(dist=dist, dur=dur * 1000, path_ratio=path / dist,
                      peak=peak, rise=rise, rev=rev, dwell=dwell))

pauses = [bursts[i + 1]["t0"] - bursts[i]["t1"] for i in range(len(bursts) - 1)]
pauses = [p for p in pauses if 0.05 < p < 3]

def q(xs, f):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(f * len(xs)))]

F = feats
print(f"有效甩枪数: {len(F)}   (暂停 {len(pauses)} 个)")
if F:
    d = [f["dist"] for f in F]; p = [f["peak"] for f in F]
    print(f"距离 px     : p10 {q(d,.1):.0f} med {st.median(d):.0f} p90 {q(d,.9):.0f}")
    print(f"峰速 px/s   : med {st.median(p)*1000:.0f} p90 {q(p,.9)*1000:.0f}")
    print(f"上升时间占比: med {st.median([f['rise'] for f in F]):.2f}  (0.25-0.4=钟形,0=阶跃)")
    print(f"路径弯度比  : med {st.median([f['path_ratio'] for f in F]):.2f} (1.0=直线)")
    print(f"单枪时长 ms : med {st.median([f['dur'] for f in F]):.0f}")
    print(f"末端修正次数: med {st.median([f['rev'] for f in F]):.0f} p90 {q([f['rev'] for f in F],.9):.0f}")
    print(f"末端低速占比: med {st.median([f['dwell'] for f in F]):.2f}")
if pauses:
    print(f"两枪间隔 s  : med {st.median(pauses):.2f} p10 {q(pauses,.1):.2f} (含犹豫)")
dd = [(math.log(f["dist"]), math.log(f["peak"]), math.log(f["dur"]), f["dist"]) for f in F if f["dist"] > 40]
def powfit(rows, ix, iy):
    xs = [r[ix] for r in rows]; ys = [r[iy] for r in rows]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return math.exp(my - b * mx), b
if len(dd) > 5:
    k, b = powfit(dd, 0, 1)
    k2, b2 = powfit(dd, 0, 2)
    print(f"峰速≈{k*1000:.0f}·dist^{b:.2f} px/s | 时长≈{k2:.0f}·dist^{b2:.2f} ms")
json.dump(F, open("data/session_human_20260930/features.json", "w"))
print("saved features.json")
