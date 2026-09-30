"""Offline gain calibration for the aim pipeline.

The conversion between one mouse count and screen pixels depends on the
in-game sensitivity, render width, and Valorant's fixed horizontal FOV.
Valorant rotates 0.07 degrees per count times the sensitivity setting.

Two ways to get counts-per-pixel, both deterministic (no runtime probing):

  1. Formula:     counts_per_px = 1 / (px_per_deg * 0.07 * sens)
                  px_per_deg at screen centre = (W/2)/tan(fov/2) * pi/180
  2. Measured:    run_calibration() sweeps probe moves of different sizes,
                  measures the actual scene shift, checks linearity, fits a
                  slope, and saves valaim_calib.json.

Startup preference: --aim-gain > calib file > --sens formula > (probe only
if --probe is passed).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

VAL_YAW_PER_COUNT = 0.07  # degrees per mouse count per unit sensitivity
VAL_FOV_DEG = 103.0       # fixed horizontal FOV


def _resource_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


CALIB_FILE = "valaim_calib.json"


def calib_path(explicit: str | None = None) -> str:
    name = explicit or CALIB_FILE
    if os.path.isabs(name):
        return name
    return os.path.join(_resource_dir(), name)


def load_calib(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cp = float(data["counts_per_px"])
        if 0.01 < cp < 100:
            return data
    except (OSError, ValueError, KeyError):
        pass
    return None


def counts_per_px_formula(render_width: int, sens: float, fov_deg: float = VAL_FOV_DEG) -> float:
    """Mouse counts needed to move the crosshair one screen pixel."""
    if sens <= 0 or render_width <= 0:
        return 0.0
    px_per_rad = (render_width / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    px_per_deg = px_per_rad * math.pi / 180.0
    px_per_count = px_per_deg * VAL_YAW_PER_COUNT * sens
    return 1.0 / px_per_count


def measure_shift(img1, img2) -> tuple[float, float] | None:
    """(px shift x, response) of img2 relative to img1, via numpy-only phase
    correlation (plain opencv-python has no cv2.phaseCorrelation)."""
    import numpy as np

    g1 = np.asarray(img1, dtype=np.float32).mean(axis=2)
    g2 = np.asarray(img2, dtype=np.float32).mean(axis=2)
    g1 -= g1.mean()
    g2 -= g2.mean()
    if g1.std() < 1.0 or g2.std() < 1.0:
        return None
    try:
        f1 = np.fft.rfft2(g1)
        f2 = np.fft.rfft2(g2)
        r = f1 * np.conj(f2)
        r /= np.abs(r) + 1e-9
        corr = np.fft.irfft2(r, s=g1.shape)
    except ValueError:
        return None
    h, w = corr.shape
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)
    shift_x = ix - w if ix > w // 2 else ix
    resp = float(corr.max())
    return float(shift_x), resp


def fit_slope(points: list[tuple[float, float]]) -> float:
    """Least-squares slope through origin: px = slope * counts."""
    num = sum(c * p for c, p in points)
    den = sum(c * c for c, _ in points)
    return num / den if den else 0.0


def run_calibration(cfg, sizes: list[int], reps: int, settle: float, countdown: float = 8.0) -> int:
    """Interactive sweep calibration; writes the json; prints a table."""
    from .capture import ScreenCapture
    from .input_ctrl import set_backend, move_mouse, close_backend

    backend = set_backend(cfg.input_backend, bt_host=cfg.bt_host, bt_port=cfg.bt_port)
    print(f"input backend: {backend}")
    if backend != "bt":
        print("标定需要通过手机的 bt 后端发移动")
        return 1

    capture = ScreenCapture(
        monitor=cfg.monitor,
        mode=cfg.capture_mode,
        crop_size=cfg.crop_size,
        anchor=cfg.capture_anchor,
    )

    print()
    print("站在训练场原地不动,准星对着有纹理的墙面(几米外)。")
    print("注意:游戏失焦后不处理鼠标输入,所以标定期间【不要回到本窗口】。")
    secs = max(2, int(countdown))
    for i in range(secs, 0, -1):
        print(f"  {i} 秒后开始,请立刻切回游戏(Alt+Tab)...")
        time.sleep(1)
    print()
    print(f"{'counts':>8} {'px shift':>10} {'px/count':>10}  置信")

    points: list[tuple[float, float]] = []
    per_size: dict[int, list[float]] = {s: [] for s in sizes}
    for size in sizes:
        for rep in range(reps):
            img1, *_ = capture.grab()
            move_mouse(size, 0)
            time.sleep(settle)
            img2, *_ = capture.grab()
            move_mouse(-size, 0)
            time.sleep(settle)
            res = measure_shift(img1, img2)
            if res is None or res[1] < 0.05:
                print(f"{size:>8} {'失败(响应太低/无纹理)':>24}")
                continue
            shift = abs(res[0])
            if shift < 2.0:
                # No measurable movement: periodic wall patterns can fool the
                # global correlation at large shifts; count as a failed sample.
                print(f"{size:>8} {'失败(未检出移动/纹理重复)':>24}")
                continue
            points.append((float(size), shift))
            per_size[size].append(shift)
            print(f"{size:>8} {shift:>10.1f} {shift / size:>10.3f}  {res[1]:.2f}")
            time.sleep(0.2)

    if len(points) < 2:
        print("\n有效样本不足,无法拟合。靠近墙面/增加光照再试。")
        return 1

    slope = fit_slope(points)
    if slope <= 0:
        print("\n拟合失败:没有检测到画面移动(被游戏吞了?检查蓝牙连接)")
        return 1

    # Linearity check: slope per size vs global slope.
    print("\n线性检查(每档斜率 / 总体斜率):")
    worst = 0.0
    for size, shifts in sorted(per_size.items()):
        if not shifts:
            continue
        local = (sum(shifts) / len(shifts)) / size
        dev = (local - slope) / slope * 100.0
        worst = max(worst, abs(dev))
        print(f"  {size:>4} counts: px/count={local:.3f}  偏差 {dev:+.1f}%")
    print(f"  最大偏差 {worst:.1f}%" + ("  -> 线性良好" if worst < 8 else "  -> 有非线性,建议只用小移动量附近"))

    cp = 1.0 / slope
    data = {
        "counts_per_px": round(cp, 4),
        "px_per_count": round(slope, 4),
        "linearity_pct": round(worst, 1),
        "samples": [[c, p] for c, p in points],
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "generated by --calibrate-tool",
    }
    out = calib_path(cfg.calib_file)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n已保存: {out}")
    print(f"counts_per_px = {cp:.3f}  (启动时将自动读取,不再需要探针)")

    # Cross-check against the closed-form formula.
    mon = capture.monitors[capture.monitor + 1] if capture.monitor >= 0 else capture.monitors[0]
    print("\n理论对照(Valorant 固定 FOV 103, 0.07°/count/sens):")
    for s in (0.15, 0.3, 0.4, 0.5):
        theo = counts_per_px_formula(int(mon["width"]), s)
        print(f"  游戏灵敏度 {s:>4}: 理论 {theo:.2f} counts/px")
    close_backend()
    return 0
