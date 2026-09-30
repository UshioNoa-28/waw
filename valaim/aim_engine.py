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
from dataclasses import dataclass


@dataclass
class AimParams:
    move_fraction: float = 0.3   # fraction of the (smoothed) error per frame
    max_step: int = 45            # hard cap on pixels per frame
    min_move: float = 1.0

    deadzone: float = 2.0         # stop inside this radius (px)
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

    def reset(self) -> None:
        self._sx = self._sy = None
        self._carry_x = self._carry_y = 0.0
        self._overshoot_left = 0

    def step(self, dx: float, dy: float) -> tuple[int, int]:
        """Return a relative mouse step for the current pixel error."""
        p = self.p

        # Exponential moving average of the target error (damps model jitter).
        if self._sx is None:
            self._sx, self._sy = dx, dy
        else:
            a = max(0.05, min(1.0, p.smoothing))
            self._sx += a * (dx - self._sx)
            self._sy += a * (dy - self._sy)

        ex, ey = self._sx, self._sy
        dist = math.hypot(ex, ey)

        if dist <= p.deadzone:
            self._carry_x = self._carry_y = 0.0
            return 0, 0

        gain = p.move_fraction
        if dist > p.overshoot_error and self._overshoot_left == 0:
            self._overshoot_left = p.overshoot_frames
        if self._overshoot_left > 0:
            gain *= p.overshoot_factor
            self._overshoot_left -= 1

        # Pixel error -> mouse counts using the calibrated gain. Without this
        # the loop emits counts assuming 1:1 and overshoots whenever one count
        # turns more pixels on screen.
        cp = max(0.05, p.counts_per_px)
        step_x = ex * gain * cp + self._carry_x
        step_y = ey * gain * cp + self._carry_y

        # Slew limit (also respects the HID report's +-127 range).
        limit = max(1, min(p.max_step, p.max_segment))
        mag = math.hypot(step_x, step_y)
        if mag > limit:
            scale = limit / mag
            step_x *= scale
            step_y *= scale

        ix = int(round(step_x))
        iy = int(round(step_y))
        self._carry_x = step_x - ix
        self._carry_y = step_y - iy

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
