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
    smoothing: float = 0.6        # EMA weight for new error (0=ignore,1=raw)

    # Human-like extras, off by default in the stable build.
    tremor_px: float = 0.0
    tremor_freq: float = 8.0
    overshoot_factor: float = 1.0
    overshoot_error: float = 99999.0
    overshoot_frames: int = 0

    max_segment: int = 127
    # Mouse counts emitted per screen pixel of error. Depends on the in-game
    # sensitivity; 1.0 is a wrong default for most games, so the app
    # auto-calibrates this at startup (see main.calibrate_gain).
    counts_per_px: float = 1.0


class AimEngine:
    """Stateful shaper. One instance per session."""

    def __init__(self, params: AimParams | None = None, seed: int | None = None) -> None:
        self.p = params or AimParams()
        self._sx: float | None = None   # smoothed error x
        self._sy: float | None = None
        self._carry_x = 0.0             # sub-pixel remainder
        self._carry_y = 0.0
        self._overshoot_left = 0
        self._hist = []               # (t, px_x, px_y) commands not yet rendered

    def reset(self) -> None:
        self._sx = self._sy = None
        self._carry_x = self._carry_y = 0.0
        self._overshoot_left = 0
        self._hist = []

    def step(self, dx: float, dy: float,
             lead_x: float = 0.0, lead_y: float = 0.0) -> tuple[int, int]:
        """Return a relative mouse step for the current pixel error.

        lead_x/lead_y are caller-computed target-motion predictions (px); they
        are added AFTER smoothing so target velocity never feeds back through
        our own actuation latency (which is what made a measurement-derivative
        term unstable here).
        """
        p = self.p

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

        if dist <= p.deadzone:
            self._carry_x = self._carry_y = 0.0
            return 0, 0

        gain = p.move_fraction
        if dist > p.overshoot_error and self._overshoot_left == 0:
            self._overshoot_left = p.overshoot_frames
        if self._overshoot_left > 0:
            gain *= p.overshoot_factor
            self._overshoot_left -= 1

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
