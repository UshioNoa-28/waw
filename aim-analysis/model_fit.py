"""Identify device parameters from in-game traces -> device_model.json.

Inputs (repo root): step.csv (open-loop strokes), noise.csv (idle jitter),
move.csv (moving target), ping.log (WiFi RTT, may be UTF-16/GBK mojibake).

Outputs JSON with: actuation delay distribution (samples in s), noise sigma,
spike rate/size, screen target speed distribution, gain check.
"""
import csv
import json
import math
import re
import statistics as st
import sys
from bisect import bisect_left

CP = 0.75  # px per count (measured)


def load(path):
    errs, cmds, heads = [], [], []
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            k = row[0]
            try:
                if k == "err":
                    errs.append((float(row[1]), float(row[2]), float(row[3])))
                elif k == "cmd":
                    cmds.append((float(row[1]), float(row[2]), float(row[3])))
                elif k == "det" and row[7].strip() == "Head":
                    heads.append((float(row[1]), float(row[2]), float(row[3])))
            except (ValueError, IndexError):
                pass
    return errs, cmds, heads


def fit_step(path):
    errs, cmds, _ = load(path)
    et = [e[0] for e in errs]
    delays, ratios = [], []
    for n, (t, mx, my) in enumerate(cmds):
        stroke = math.hypot(mx, my) * CP
        if stroke < 20:
            continue
        nxt = cmds[n + 1][0] if n + 1 < len(cmds) else errs[-1][0]
        if nxt - t < 0.45:
            continue  # chained strokes: attribution unsafe
        i = bisect_left(et, t)
        if i < 1 or i >= len(errs):
            continue
        base = errs[i - 1]
        ux, uy = (mx / max(1e-6, math.hypot(mx, my)), my / max(1e-6, math.hypot(mx, my)))
        onset, drop = None, 0.0
        for j in range(i, min(i + 25, len(errs))):
            tj, xj, yj = errs[j]
            if tj - t > min(0.45, nxt - t):
                break
            prog = -( (xj - base[1]) * ux + (yj - base[2]) * uy )
            drop = max(drop, prog)
            if onset is None and prog >= 0.5 * stroke:
                onset = tj - t
        if onset is not None:
            delays.append(onset)
            if stroke > 0:
                ratios.append(drop / stroke)
    return delays, ratios


def fit_noise(path):
    errs, _, heads = load(path)
    diffs = []
    for i in range(1, len(errs)):
        if errs[i][0] - errs[i - 1][0] < 0.05:
            diffs.append(math.hypot(errs[i][1] - errs[i - 1][1],
                                    errs[i][2] - errs[i - 1][2]))
    if not diffs:
        return {}
    med = st.median(diffs)
    mad = st.median([abs(d - med) for d in diffs])
    sig = 1.4826 * mad if mad else med
    spike = [d for d in diffs if d > 12]
    hw = [h[3] if len(h) > 3 else 0 for h in heads]
    return {
        "frame_disp_p50": round(med, 2),
        "frame_disp_p90": round(sorted(diffs)[int(len(diffs) * 0.9)], 2),
        "sigma_mad": round(sig, 2),
        "spike_rate": round(len(spike) / len(diffs), 4),
        "spike_p50": round(st.median(spike), 1) if spike else 0,
    }


def fit_move(path):
    errs, _, heads = load(path)
    speeds = []
    for i in range(1, len(heads)):
        dt = heads[i][0] - heads[i - 1][0]
        if 0.005 < dt < 0.05:
            speeds.append(math.hypot(heads[i][1] - heads[i - 1][1],
                                     heads[i][2] - heads[i - 1][2]) / dt)
    if not speeds:
        return {}
    s = sorted(speeds)
    return {
        "screen_speed_p50": round(s[int(len(s) * 0.5)]),
        "screen_speed_p90": round(s[int(len(s) * 0.9)]),
        "screen_speed_p95": round(s[int(len(s) * 0.95)]),
    }


def fit_ping(path):
    try:
        raw = open(path, "rb").read()
        for enc in ("utf-16", "gbk", "utf-8"):
            try:
                txt = raw.decode(enc, errors="ignore")
                break
            except Exception:
                continue
        ms = [int(m) for m in re.findall(r"(\d+)\s*m\s*s", txt)]
        ms = [m for m in ms if 0 < m < 5000]
        if not ms:
            ms = [int(m) for m in re.findall(r"=\s*(\d+)", txt) if 0 < int(m) < 5000]
        if not ms:
            return {}
        s = sorted(ms)
        return {"rtt_p50": s[len(s) // 2], "rtt_p90": s[int(len(s) * 0.9)],
                "rtt_max": s[-1], "n": len(s)}
    except OSError:
        return {}


def main():
    out = {}
    delays, ratios = fit_step("step.csv")
    if delays:
        ds = sorted(delays)
        out["actuation"] = {
            "samples": len(ds),
            "p50": round(ds[len(ds) // 2], 3),
            "p75": round(ds[int(len(ds) * 0.75)], 3),
            "p90": round(ds[int(len(ds) * 0.9)], 3),
            "max": round(ds[-1], 3),
            "delay_hist": [round(x, 3) for x in ds],
        }
        out["gain_check_ratio_p50"] = round(st.median(ratios), 3)
    out["noise"] = fit_noise("noise.csv")
    out["target"] = fit_move("move.csv")
    out["wifi"] = fit_ping("ping.log")
    out["loop_fps"] = 30
    out["cp_px_per_count"] = CP
    with open("device_model.json", "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "actuation"}, indent=1, ensure_ascii=False))
    if "actuation" in out:
        a = out["actuation"]
        print(f"actuation: n={a['samples']} p50={a['p50']*1000:.0f}ms "
              f"p90={a['p90']*1000:.0f}ms max={a['max']*1000:.0f}ms")
    print("saved device_model.json")


if __name__ == "__main__":
    sys.exit(main())
