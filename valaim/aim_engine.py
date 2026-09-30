"""Aim shaping: turn a per-frame pixel error into a mouse step.

Design goals for this build: **stability**, not human-likeness.

  * **Sub-pixel accumulation** - fractional remainders are carried to the next
    frame, so small errors are not rounded away to zero.
  * **Error smoothing** - an exponential moving average damps the frame-to-frame
    jitter of the model's bounding box, so the crosshair does not twitch on a
    static target.
  * **Slew limit** - the per-frame step is capped in pixels, so a large or noisy
    error cannot fling the crosshair past the target (no more "jumps to the
    head and overshoots").
  * **Dead zone** - inside a small radius, nothing is emitted.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass


@dataclass
class AimParams:
    move_fraction: float = 0.4    # P: fraction of the error per frame. Safe up
                                  # to ~0.45 only together with in-flight comp
                                  # (comp_frames); without comp keep <= 0.25.
    max_step: int = 60            # hard cap on mouse counts per frame
    min_move: float = 1.0
    min_speed: float = 2.0        # counts/frame floor outside the deadzone
    comp_weight: float = 1.0      # fraction of in-flight move pre-subtracted from error
    comp_frames: int = 3          # assumed in-flight window, in loop frames

    deadzone: float = 2.5         # stop inside this radius (px)
    arrive_px: float = 5.0        # lock OFF the output inside this radius...
    resume_px: float = 10.0       # ...and only resume past this (hysteresis)
    med_win: int = 5              # median filter width on raw error (px jitter)
    smoothing: float = 0.6        # EMA weight for new error (0=ignore,1=raw)

    # Humanization (measured from aim_lab: 60 trials, jit med 3.1px, bell rise,
    # reaction included in 623ms median flick). Off unless enabled.
    humanize: bool = False
    react_ms: tuple = (120.0, 220.0)   # hold-off after a NEW target lock
    ramp_s: float = 0.09               # gain ramps 40% -> 100% over this
    tremor_px: float = 2.2             # OU tremor steady-state std (px)
    tremor_hz: float = 10.0            # action-tremor band
    seed: float = 0.0

    max_segment: int = 127
    # Mouse counts emitted per screen pixel of error. Depends on the in-game
    # sensitivity; 1.0 is a wrong default for most games, so the app
    # auto-calibrates this at startup (see main.calibrate_gain).
    counts_per_px: float = 1.0


class AimEngine:
    """Stateful shaper. One instance per session."""

    def __init__(self, params: AimParams | None = None, seed: int | None = None) -> None:
        import random as _r
        self.p = params or AimParams()
        self._rng = _r.Random(seed or None)
        self._lock_at: float | None = None   # when current lock began (reaction gate)
        self._gate = 0.0                      # gate length, s
        self._tx = self._ty = 0.0             # OU tremor state
        self._ph = 0.0
        self._sx: float | None = None   # smoothed error x
        self._sy: float | None = None
        self._carry_x = 0.0             # sub-pixel remainder
        self._carry_y = 0.0
        self._hist = []               # (t, px_x, px_y) commands not yet rendered
        self._raw: list = []          # recent raw errors for median filter
        self._latched = False         # arrived: output suppressed

    def reset(self) -> None:
        self._sx = self._sy = None
        self._carry_x = self._carry_y = 0.0
        self._hist = []
        self._raw = []
        self._latched = False
        self._lock_at = None

    def on_new_lock(self) -> None:
        """Caller signals a fresh target (or a re-acquire after a gap)."""
        self._lock_at = time.monotonic()
        self._latched = False
        lo = min(self.p.react_ms) / 1000.0
        hi = max(self.p.react_ms) / 1000.0
        self._gate = self._rng.uniform(lo, hi) if self.p.humanize else 0.0

    def _tremor(self, dt: float) -> tuple[float, float]:
        """Band-limited OU noise: theta=2*pi*hz, sigma set so std=tremor_px."""
        import math as _m
        if self.p.tremor_hz <= 0 or self.p.tremor_px <= 0:
            return 0.0, 0.0
        theta = 2.0 * _m.pi * self.p.tremor_hz
        alpha = _m.exp(-theta * dt)
        sigma = self.p.tremor_px * _m.sqrt(2.0 * theta)   # OU stationary std
        scale = sigma * _m.sqrt(max(0.0, (1.0 - alpha * alpha) / (2.0 * theta))) * dt
        self._tx = self._tx * alpha + self._rng.gauss(0.0, 1.0) * scale
        self._ty = self._ty * alpha + self._rng.gauss(0.0, 1.0) * scale
        return self._tx, self._ty

    def step(self, dx: float, dy: float,
             lead_x: float = 0.0, lead_y: float = 0.0) -> tuple[int, int]:
        """Return a relative mouse step for the current pixel error.

        lead_x/lead_y are caller-computed target-motion predictions (px); they
        are added AFTER smoothing so target velocity never feeds back through
        our own actuation latency (which is what made a measurement-derivative
        term unstable here).
        """
        p = self.p

        # Median filter first: kills single-frame outlier jumps of the box.
        if p.med_win >= 3:
            self._raw.append((dx, dy))
            if len(self._raw) > p.med_win:
                self._raw.pop(0)
            xs = sorted(v[0] for v in self._raw)
            ys = sorted(v[1] for v in self._raw)
            dx = xs[len(xs) // 2]
            dy = ys[len(ys) // 2]

        # Arrival latch (hysteresis): once on target, hold position until the
        # error clearly exceeds resume_px. Humans stop micro-correcting noise;
        # chasing model jitter is exactly the "末端晃动" complaint.
        raw_dist = math.hypot(dx, dy)
        if self._latched:
            if raw_dist <= p.resume_px:
                self._carry_x = self._carry_y = 0.0
                return 0, 0
            self._latched = False

        # Exponential moving average of the target error (damps model jitter).
        if self._sx is None:
            self._sx, self._sy = dx, dy
        else:
            a = max(0.05, min(1.0, p.smoothing))
            self._sx += a * (dx - self._sx)
            self._sy += a * (dy - self._sy)

        cp0 = max(0.05, p.counts_per_px)
        # Smith-predictor style compensation: our commands only appear in the
        # screenshot after the loop delay, so subtract everything still in
        # flight; otherwise the loop re-sends moves it already ordered.
        now = time.monotonic()
        window = max(1, p.comp_frames) / 60.0
        self._hist = [h for h in self._hist if now - h[0] <= window]
        w = max(0.0, min(1.0, p.comp_weight))
        infl_x = w * sum(h[1] for h in self._hist)
        infl_y = w * sum(h[2] for h in self._hist)
        ex = self._sx + lead_x - infl_x
        ey = self._sy + lead_y - infl_y
        dist = math.hypot(self._sx, self._sy)

        if raw_dist <= p.arrive_px:
            self._latched = True
            self._carry_x = self._carry_y = 0.0
            return 0, 0
        if dist <= p.deadzone:
            self._carry_x = self._carry_y = 0.0
            return 0, 0

        gain = p.move_fraction
        # Humanization: reaction gate, gain ramp-in (bell-ish onset), tremor.
        if p.humanize and self._lock_at is not None:
            since = time.monotonic() - self._lock_at
            if since < self._gate:
                return 0, 0
            if p.ramp_s > 0 and since < self._gate + p.ramp_s:
                f = (since - self._gate) / p.ramp_s
                gain *= 0.4 + 0.6 * f
            jx, jy = self._tremor(max(0.004, min(0.05, time.monotonic() - self._last_t if self._last_t else 0.016)))
            self._last_t = time.monotonic()
            ex += jx
            ey += jy

        # Pixel error -> mouse counts using the calibrated gain.
        cp = cp0
        step_x = ex * gain * cp + self._carry_x
        step_y = ey * gain * cp + self._carry_y

        # Slew limit (also respects the HID report's +-127 range).
        limit = max(1, min(p.max_step, p.max_segment))
        mag = math.hypot(step_x, step_y)
        if mag > limit:
            scale = limit / mag
            step_x *= scale
            step_y *= scale
            # The clamp is not a queue: dropped remainder must NOT wind up
            # into the carry or the crosshair jumps when the error shrinks.
            self._carry_x = self._carry_y = 0.0

        ix = int(round(step_x))
        iy = int(round(step_y))

        # Speed floor: outside the deadzone never crawl below min_speed,
        # otherwise the final stretch feels sluggish (exponential tail).
        floor = max(0, p.min_speed)
        if floor and dist > p.deadzone:
            if abs(ix) < floor and ex != 0:
                ix = int(math.copysign(floor, ex))
            if abs(iy) < floor and ey != 0:
                iy = int(math.copysign(floor, ey))

        self._carry_x = step_x - ix if abs(step_x) >= floor or ix == 0 else 0.0
        self._carry_y = step_y - iy if abs(step_y) >= floor or iy == 0 else 0.0
        if ix or iy:
            self._hist.append((now, ix / cp, iy / cp))

        return ix, iy


def split_segments(dx: int, dy: int, max_segment: int = 127) -> list[tuple[int, int]]:
    """Split a large delta into HID-sized segments (each axis <= 127)."""
    segments: list[tuple[int, int]] = []
    rx, ry = int(dx), int(dy)
    while rx != 0 or ry != 0:
        sx = max(-max_segment, min(max_segment, rx))
        sy = max(-max_segment, min(max_segment, ry))
        segments.append((sx, sy))
        rx -= sx
        ry -= sy
    return segments
